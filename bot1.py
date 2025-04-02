import os
import time
import json
import logging
import subprocess
import threading
import re
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from pyrogram import Client, filters
from pyrogram.types import InputMediaDocument, InputMediaVideo
from pyrogram.errors import PeerIdInvalid, ChannelPrivate, FloodWait
from tqdm import tqdm
import queue
from bot import UdvashDownloader
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

class ThreadSafeTqdm:
    """Thread-safe wrapper for tqdm progress bars with n attribute"""
    def __init__(self, *args, **kwargs):
        self._lock = threading.Lock()
        self._progress = None
        self._current = 0  # Track current progress manually
        self._args = args
        self._kwargs = kwargs
        
    def __enter__(self):
        with self._lock:
            if self._progress is None:
                self._progress = tqdm(*self._args, **self._kwargs)
                self._current = 0
            return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with self._lock:
            if self._progress is not None:
                self._progress.close()
                self._progress = None
                self._current = 0

    @property
    def n(self):
        with self._lock:
            return self._current

    def update(self, n: int):
        with self._lock:
            if self._progress is not None:
                self._progress.update(n)
                self._current += n

    def set_description(self, desc: str):
        with self._lock:
            if self._progress is not None:
                self._progress.set_description(desc)

class TelegramUploader:
    def __init__(self, api_id, api_hash, bot_token, chat_id, max_uploads=3):
        self.logger = self._setup_logger()
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.max_uploads = max_uploads
        
        self._loop = None
        self._client = None
        self._upload_queue = queue.Queue()
        self._active_uploads = 0
        self._shutdown_flag = False
        self._exception = None
        
        self._start_client()
        self._start_upload_workers()
        self._ensure_thumbnails()

    def _setup_logger(self):
        logger = logging.getLogger("telegram_uploader")
        logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        file_handler = logging.FileHandler("telegram_uploader.log")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        return logger

    def _start_client(self):
        def client_thread():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            
            self._client = Client(
                "udvash_uploader_bot",
                api_id=self.api_id,
                api_hash=self.api_hash,
                bot_token=self.bot_token,
                max_concurrent_transmissions=10,
                workers=50
            )
            
            @self._client.on_message(filters.command("mm"))
            async def welcome_command(client, message):
                await message.reply_text(
                    "👋 Welcome to Udvash Uploader Bot!\n\n"
                    "This bot helps download and upload content from Udvash.\n"
                    "Currently uploading content to the configured channel.\n\n"
                    "Use /status to check current upload status."
                )

            @self._client.on_message(filters.command("status"))
            async def status_command(client, message):
                status_msg = (
                    f"📊 **Current Status**\n\n"
                    f"Active uploads: {self._active_uploads}/{self.max_uploads}\n"
                    f"Queued uploads: {self._upload_queue.qsize()}\n"
                )
                await message.reply_text(status_msg)

            with self._client:
                self._loop.run_forever()

        self._client_thread = threading.Thread(target=client_thread, daemon=True)
        self._client_thread.start()
        
        while not (self._loop and self._loop.is_running()):
            time.sleep(0.1)

    def _start_upload_workers(self):
        def upload_worker():
            while not self._shutdown_flag or not self._upload_queue.empty():
                try:
                    task = self._upload_queue.get(timeout=1)
                    if task is None:
                        continue
                        
                    with threading.Lock():
                        self._active_uploads += 1
                        
                    asyncio.run_coroutine_threadsafe(
                        self._process_upload_task(task),
                        self._loop
                    ).result()
                    
                    with threading.Lock():
                        self._active_uploads -= 1
                        
                    self._upload_queue.task_done()
                except queue.Empty:
                    continue
                except Exception as e:
                    self.logger.error(f"Upload worker error: {str(e)}")
                    self._exception = e

        for _ in range(self.max_uploads):
            worker = threading.Thread(target=upload_worker, daemon=True)
            worker.start()

    async def _process_upload_task(self, task):
        file_path = task['file_path']
        chapter_name = task['chapter_name']
        topic_name = task['topic_name']
        file_type = task['file_type']
        retries = task.get('retries', 3)
        
        try:
            with ThreadSafeTqdm(
                total=os.path.getsize(file_path),
                unit='B',
                unit_scale=True,
                desc=f"Uploading {os.path.basename(file_path)}",
                position=task.get('position', 0)
            ) as progress:
                def update_progress(current, total):
                    try:
                        delta = current - progress.n
                        progress.update(delta)
                    except AttributeError:
                        pass  # Handle closed progress bar

                caption = f"<blockquote><b>📚 {chapter_name}\n📖 {topic_name}\n📁 {os.path.basename(file_path)}</b></blockquote>"
                
                if file_type == "video":
                    duration = await self._get_video_duration(file_path)
                    await self._client.send_video(
                        chat_id=self.chat_id,
                        video=file_path,
                        caption=caption,
                        height=1280,
                        width=720,
                        thumb="abc.jpg",
                        duration=int(duration),  # Ensure integer
                        progress=update_progress
                    )
                else:
                    await self._client.send_document(
                        chat_id=self.chat_id,
                        document=file_path,
                        caption=caption,
                        thumb="bcd.jpg",
                        progress=update_progress
                    )
                
                self.logger.info(f"Successfully uploaded {file_path}")
                os.remove(file_path)
                
        except FloodWait as e:
            self.logger.warning(f"Flood wait: Retrying in {e.value} seconds")
            await asyncio.sleep(e.value)
            await self._retry_upload(task)
        except Exception as e:
            self.logger.error(f"Failed to upload {file_path}: {str(e)}")
            if retries > 0:
                await self._retry_upload(task)
            else:
                self.logger.error(f"Permanent failure for {file_path}")
                    
    async def _get_video_duration(self, file_path):
        def run_ffprobe():
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                    "default=noprint_wrappers=1:nokey=1", file_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                return int(round(float(result.stdout.strip())))  # Convert to integer
            except Exception as e:
                self.logger.error(f"Error getting video duration: {str(e)}")
                return 0  # Default to 0 if duration can't be determined
        
        return await asyncio.get_event_loop().run_in_executor(None, run_ffprobe)
    
    async def _retry_upload(self, task):
        task['retries'] = task.get('retries', 3) - 1
        if task['retries'] > 0:
            self.logger.info(f"Retrying upload ({task['retries']} attempts left)")
            self._upload_queue.put(task)
        else:
            self.logger.error(f"Exhausted retries for {task['file_path']}")

    def queue_upload(self, file_path, chapter_name, topic_name, file_type):
        if not os.path.exists(file_path):
            self.logger.error(f"File not found: {file_path}")
            return

        self._upload_queue.put({
            'file_path': file_path,
            'chapter_name': chapter_name,
            'topic_name': topic_name,
            'file_type': file_type,
            'position': self._active_uploads % self.max_uploads
        })

    async def send_chapter_notification(self, chapter_name):
        try:
            await self._client.send_message(
                chat_id=self.chat_id,
                text=f"<blockquote><b><u>{chapter_name}</u></b></blockquote>"
            )
        except Exception as e:
            self.logger.error(f"Failed to send notification: {str(e)}")

    def _ensure_thumbnails(self):
        # Video thumbnail
        if not os.path.exists('abc.jpg'):
            print("Downloading video thumbnail...")
            subprocess.run([
                "wget", "-O", "abc.jpg", 
                "https://files.catbox.moe/f0o670.jpg"  # Replace with actual URL
            ])
        
        # PDF thumbnail
        if not os.path.exists('bcd.jpg'):
            print("Downloading PDF thumbnail...")
            subprocess.run([
                "wget", "-O", "bcd.jpg", 
                "https://files.catbox.moe/mins6u.jpg"  # Replace with actual URL
            ])

    def wait_for_uploads(self):
        """Wait for all queued uploads to complete"""
        self._upload_queue.join()

    def stop(self):
        """Graceful shutdown"""
        self.logger.info("Initiating shutdown...")
        self._shutdown_flag = True
        
        # Wait for uploads to complete with timeout
        start_time = time.time()
        while (not self._upload_queue.empty() and 
               (time.time() - start_time) < 30):
            time.sleep(1)
        
        # Stop the event loop
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        
        self._client_thread.join(timeout=10)
        
        if self._exception:
            raise self._exception

class UdvashDownloaderUploader(UdvashDownloader):
    def __init__(self, user_id, password, api_id, api_hash, bot_token, chat_id,
                 max_downloads=3, max_uploads=3, download_dir="downloads",
                 download_archive=True, download_marathon=True, download_bangla=True,
                 download_english=True, create_json=True, content_types=None):
        
        super().__init__(
            user_id=user_id,
            password=password,
            max_parallel_downloads=max_downloads,
            download_dir=download_dir,
            download_archive=download_archive,
            download_marathon=download_marathon,
            download_bangla=download_bangla,
            download_english=download_english,
            create_json=create_json
        )
        
        self.uploader = TelegramUploader(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            chat_id=chat_id,
            max_uploads=max_uploads
        )
        
        # Set content types to download/upload (default to both if None)
        self.content_types = content_types or ["video", "pdf"]
        self.current_chapter = None
        self.file_metadata = {}
        self.metadata_lock = threading.Lock()

    def download_file(self, url, file_path, file_type):
        # Skip if file type is not in the specified content types
        if file_type not in self.content_types:
            self.logger.info(f"Skipping {file_type} file (not in selected content types): {file_path}")
            return False
            
        success = super().download_file(url, file_path, file_type)
        
        if success:
            self._queue_upload(file_path, file_type)
        
        return success

    def _queue_upload(self, file_path, file_type):
        try:
            path_parts = Path(file_path).parts
            chapter_name = path_parts[-3]
            topic_name = self._get_topic_name(file_path)
            
            if chapter_name != self.current_chapter:
                asyncio.run_coroutine_threadsafe(
                    self.uploader.send_chapter_notification(chapter_name),
                    self.uploader._loop
                ).result()
                self.current_chapter = chapter_name
                
            self.uploader.queue_upload(
                file_path=file_path,
                chapter_name=chapter_name,
                topic_name=topic_name,
                file_type=file_type
            )
        except Exception as e:
            self.logger.error(f"Error queueing upload: {str(e)}")

    def _get_topic_name(self, file_path):
        base_name = os.path.basename(file_path)
        key = base_name.rsplit('_', 1)[0]  # Remove language suffix
        
        with self.metadata_lock:
            return self.file_metadata.get(key, {}).get('topic', 'Unknown Topic')

    def process_content(self, subject_name, chapter_name, content_card, 
                       master_course_id, subject_id, master_chapter_id, 
                       content_type_name):
        title = content_card['title']
        clean_title = re.sub(r'[<>:"/\\|?*]', '_', title)
        
        # Extract topic from the content card
        topic = content_card.get('topic', None)
        if not topic:
            # If topic isn't available directly, try to extract it
            try:
                topic = self.get_topic_from_content_card(content_card['element'])
            except:
                topic = "Unknown Topic"
        
        with self.metadata_lock:
            self.file_metadata[clean_title] = {
                'topic': topic,
                'chapter': chapter_name,
                'content_type': content_type_name
            }
        
        super().process_content(
            subject_name, chapter_name, content_card, 
            master_course_id, subject_id, master_chapter_id, 
            content_type_name
        )

    def get_topic_from_content_card(self, card_element):
        """Extract topic name from content card HTML"""
        try:
            # Find the content div
            content_div = card_element.find_element(By.CSS_SELECTOR, "div.content")
            if not content_div:
                return None
                
            # Get the HTML content
            content_html = content_div.get_attribute('innerHTML')
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(content_html, 'html.parser')
            
            # Try to find a strong tag with topic information (look for the second one, which is typically the topic)
            # First, look for all strong tags or spans with strong inside
            strong_elements = soup.find_all('strong')
            if len(strong_elements) >= 2:
                # The second strong element often contains the topic name
                topic_text = strong_elements[1].get_text(strip=True)
                self.logger.info(f"Extracted topic from strong element: {topic_text}")
                return topic_text
                
            # If we couldn't find the topic in strong tags, try looking for it in the table cells
            # Get all table cells
            all_cells = soup.find_all('td')
            
            # Look for the cell that contains the "◾" character or has topic-like content
            for cell in all_cells:
                cell_text = cell.get_text(strip=True)
                if "◾" in cell_text or (len(cell_text) > 5 and not cell_text.startswith("🔸")):
                    # Process to clean up the text
                    topic_text = re.sub(r'^\s*◾\s*', '', cell_text)
                    self.logger.info(f"Extracted topic from cell: {topic_text}")
                    return topic_text
            
            # If we still can't find a topic, look for the last non-empty cell as a fallback
            non_empty_cells = [cell for cell in all_cells if cell.get_text(strip=True)]
            if non_empty_cells:
                topic_text = non_empty_cells[-1].get_text(strip=True)
                topic_text = re.sub(r'^\s*◾\s*', '', topic_text)
                self.logger.info(f"Extracted topic from last non-empty cell: {topic_text}")
                return topic_text
                
            # If all else fails, return a default topic
            return "General"
        except Exception as e:
            self.logger.error(f"Error extracting topic name: {str(e)}")
            return "Unknown Topic"  # Return a default value instead of None

    def download_all(self, from_chapter=None, to_chapter=None, specific_subjects=None):
        try:
            super().download_all(from_chapter, to_chapter, specific_subjects)
            self.uploader.wait_for_uploads()
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user. Cleaning up...")
        except Exception as e:
            self.logger.error(f"Error in download_all: {str(e)}")
        finally:
            self.cleanup()

    def cleanup(self):
        super().cleanup()
        self.uploader.stop()


def main():
    # Get configuration from environment variables
    user_id = os.environ.get('UDVASH_USER_ID', '')
    password = os.environ.get('UDVASH_PASSWORD', '')
    api_id = int(os.environ.get('TELEGRAM_API_ID', '0'))
    api_hash = os.environ.get('TELEGRAM_API_HASH', '')
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    download_dir = os.environ.get('DOWNLOAD_DIR', 'downloads')
    max_downloads = int(os.environ.get('MAX_DOWNLOADS', '3'))
    max_uploads = int(os.environ.get('MAX_UPLOADS', '3'))
    from_chapter = int(os.environ.get('FROM_CHAPTER', '0')) or None
    to_chapter = int(os.environ.get('TO_CHAPTER', '0')) or None
    subjects = os.environ.get('SUBJECTS', '')
    only_video = os.environ.get('ONLY_VIDEO', 'false').lower() == 'true'
    only_pdf = os.environ.get('ONLY_PDF', 'false').lower() == 'true'
    no_bangla = os.environ.get('NO_BANGLA', 'false').lower() == 'true'
    no_english = os.environ.get('NO_ENGLISH', 'false').lower() == 'true'
    no_marathon = os.environ.get('NO_MARATHON', 'false').lower() == 'true'
    no_archive = os.environ.get('NO_ARCHIVE', 'false').lower() == 'true'

    specific_subjects = None
    if subjects:
        specific_subjects = [s.strip() for s in subjects.split(",")]
    
    # Determine content types to download and upload
    content_types = ["video", "pdf"]  # Default: download and upload both
    if only_video:
        content_types = ["video"]
    elif only_pdf:
        content_types = ["pdf"]
    
    try:
        downloader = UdvashDownloaderUploader(
            user_id=user_id,
            password=password,
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            chat_id=chat_id,
            max_downloads=max_downloads,
            max_uploads=max_uploads,
            download_dir=download_dir,
            download_archive=not no_archive,
            download_marathon=not no_marathon,
            download_bangla=not no_bangla,
            download_english=not no_english,
            content_types=content_types
        )
        
        downloader.download_all(
            from_chapter=from_chapter,
            to_chapter=to_chapter,
            specific_subjects=specific_subjects
        )
    except Exception as e:
        logging.error(f"Fatal error: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()
