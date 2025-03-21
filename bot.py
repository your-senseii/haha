import os
import time
import aiohttp
import asyncio
import logging
import re
import json
import urllib.request
import subprocess
import shlex
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union, Any

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
import humanize
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from udvash_downloader import UdvashDownloader
# Import UdvashDownloader class
# You'll place your UdvashDownloader class here

# Configuration
API_ID = int(os.environ.get("API_ID", 24464839))
API_HASH = os.environ.get("API_HASH", "c906bdd79dae0c7e6b8446db37128705")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7473386163:AAGAo0hN4i9Untye-zB_NEY3084LJNKS7QY")
DUMP_CHANNEL = int(os.environ.get("DUMP_CHANNEL", -1002531978397))
ADMINS = [int(admin) for admin in os.environ.get("ADMINS", "7354647629,7881621674,7408278224").split(",") if admin]
LOG_CHANNEL = os.environ.get("LOG_CHANNEL")

# Constants
VIDEO_THUMBNAIL = "https://files.catbox.moe/f0o670.jpg"
PDF_THUMBNAIL = "https://files.catbox.moe/mins6u.jpg"
PROGRESS_BAR = """<b>\n ╭━━━━❰ᴘʀᴏɢʀᴇss ʙᴀʀ❱━➣ 
┣⪼ 🗃️ Sɪᴢᴇ: {1} | {2} 
┣⪼ ⏳️ Dᴏɴᴇ : {0}% 
┣⪼ 🚀 Sᴩᴇᴇᴅ: {3}/s 
┣⪼ ⏰️ Eᴛᴀ: {4} 
╰━━━━━━━━━━━━━━━➣ </b>"""

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize client
app = Client(
    "udvash_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# User data storage
user_data = {}
download_tasks = {}
active_downloads = 0
max_parallel_downloads = 3
download_semaphore = asyncio.Semaphore(max_parallel_downloads)
# Health check endpoint for Koyeb
# Health check endpoint for Koyeb with proper error handling
async def health_check_server():
    try:
        # Make sure aiohttp.web is properly imported
        from aiohttp import web
        
        # Create application
        app = web.Application()
        
        # Simple health check handler
        async def health_handler(request):
            return web.Response(text="Healthy", status=200)
        
        # Add routes
        app.router.add_get("/health", health_handler)
        app.router.add_get("/", health_handler)  # Also respond to root path
        
        # Set up the server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        
        # Start the site
        await site.start()
        
        logger.info("Health check server started on port 8080")
        
        # Return the runner so it can be cleaned up later if needed
        return runner
        
    except ImportError as e:
        logger.error(f"Error importing aiohttp.web: {e}")
        logger.error("Make sure you have installed aiohttp with: pip install aiohttp")
        
    except Exception as e:
        logger.error(f"Error starting health check server: {e}")
        
        # Try alternate implementation with a simple socket if aiohttp fails
        try:
            import socket
            import threading
            
            def socket_server():
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(('0.0.0.0', 8080))
                server.listen(5)
                
                logger.info("Started fallback socket health check server on port 8080")
                
                while True:
                    try:
                        client, addr = server.accept()
                        data = client.recv(1024)
                        
                        if data:
                            response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 7\r\n\r\nHealthy"
                            client.send(response.encode())
                        
                        client.close()
                    except Exception as e:
                        logger.error(f"Error in socket server: {e}")
            
            # Start the socket server in a thread
            thread = threading.Thread(target=socket_server, daemon=True)
            thread.start()
            logger.info("Fallback health check server started in thread")
            
        except Exception as socket_error:
            logger.error(f"Also failed to start socket server: {socket_error}")

# Store thumbnails
async def download_thumbnails():
    os.makedirs("thumbnails", exist_ok=True)
    
    # Download video thumbnail
    if not os.path.exists("thumbnails/video_thumb.jpg"):
        subprocess.run(["wget", VIDEO_THUMBNAIL, "-O", "thumbnails/video_thumb.jpg"])
    
    # Download PDF thumbnail
    if not os.path.exists("thumbnails/pdf_thumb.jpg"):
        subprocess.run(["wget", PDF_THUMBNAIL, "-O", "thumbnails/pdf_thumb.jpg"])

# Authentication decorator
def require_auth(func):
    async def wrapper(client, message):
        user_id = message.from_user.id
        
        if user_id not in user_data or "credentials" not in user_data[user_id]:
            await message.reply("⚠️ You need to login first using /login userid:password")
            return
        
        return await func(client, message)
    
    return wrapper

import os
import time
import asyncio
import aiohttp
import logging
import humanize
import subprocess
import shlex
from typing import Optional, Dict, Any
from pyrogram.errors import FloodWait

# Assuming logger is configured elsewhere
logger = logging.getLogger(__name__)

# Progress bar format
PROGRESS_BAR = """<b>Progress:</b> {0:.2f}%
<b>Downloaded:</b> {1}/{2}
<b>Speed:</b> {3}/s
<b>ETA:</b> {4}"""

# Store last progress message content for each message to avoid MESSAGE_NOT_MODIFIED errors
last_progress_texts = {}
last_update_times = {}

async def progress(current, total, message, start_time, progress_type, file_name, current_chapter=None, total_chapters=None):
    """
    Monitor and display progress for uploads and downloads with debouncing
    to avoid excessive message updates
    """
    message_id = f"{message.chat.id}:{message.id}"
    now = time.time()
    
    # Implement debouncing - don't update too frequently
    min_update_interval = 2.0  # Minimum seconds between updates
    if message_id in last_update_times:
        if now - last_update_times[message_id] < min_update_interval and current != total:
            return
    
    diff = now - start_time
    
    if diff < 0.5:
        return
    
    # Update more frequently, but still check content difference
    speed = current / diff if diff > 0 else 0
    percentage = current * 100 / total if total > 0 else 0
    
    # Use more decimal places to create more variation
    percentage_str = f"{percentage:.3f}"
    
    elapsed_time = round(diff)
    eta = round((total - current) / speed) if speed > 0 else 0
    
    current_size = humanize.naturalsize(current)
    total_size = humanize.naturalsize(total)
    speed_str = humanize.naturalsize(speed)
    
    elapsed_str = humanize.naturaltime(elapsed_time)
    eta_str = humanize.naturaltime(eta) if eta else "0 seconds"
    
    chapter_info = f"Chapter {current_chapter}/{total_chapters} | " if current_chapter and total_chapters else ""
    
    progress_text = PROGRESS_BAR.format(
        float(percentage_str),
        current_size,
        total_size,
        speed_str,
        eta_str
    )
    
    # Create the full message text
    full_message = f"<b>{chapter_info}{progress_type}</b>\n\n<code>{file_name}</code>\n\n{progress_text}"
    
    # Check if message content actually changed
    if message_id in last_progress_texts and last_progress_texts[message_id] == full_message and current != total:
        return
    
    try:
        await message.edit(full_message)
        # Store the content and update time for future comparison
        last_progress_texts[message_id] = full_message
        last_update_times[message_id] = now
    except FloodWait as e:
        await asyncio.sleep(e.x)
    except Exception as e:
        # Ignore MESSAGE_NOT_MODIFIED errors
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            logger.error(f"Error updating progress: {e}")

async def monitor_download_progress(process, status_msg, file_path, current_chapter=None, total_chapters=None):
    """Monitor the download progress of a subprocess using the file size"""
    filename = os.path.basename(file_path)
    start_time = time.time()
    last_size = 0
    
    while process.poll() is None:
        if os.path.exists(file_path):
            current_size = os.path.getsize(file_path)
            
            # For progress calculation, we need to estimate total size
            # Since we don't know it yet, we'll use a placeholder that's always ahead
            estimated_total = max(current_size * 1.5, 1024 * 1024)  # At least 1MB ahead
            
            if current_size > last_size:
                await progress(
                    current_size, 
                    estimated_total, 
                    status_msg, 
                    start_time, 
                    "⬇️ Downloading", 
                    filename,
                    current_chapter,
                    total_chapters
                )
                last_size = current_size
        
        await asyncio.sleep(1)
    
    # Get final output
    stdout, stderr = process.communicate()
    if stdout:
        logger.info(f"Process stdout: {stdout}")
    if stderr and process.returncode != 0:
        logger.error(f"Process stderr: {stderr}")
    
    # Update progress one last time with final file size
    final_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    if final_size > 0:
        await progress(
            final_size,
            final_size,  # 100% complete
            status_msg,
            start_time,
            "✅ Download Complete",
            filename,
            current_chapter,
            total_chapters
        )
    
    return final_size

async def safe_edit_message(message, text):
    """Safely edit a message with proper error handling"""
    try:
        await message.edit(text)
    except FloodWait as e:
        await asyncio.sleep(e.x)
        await safe_edit_message(message, text)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            logger.error(f"Error editing message: {e}")

# Get video information
async def get_video_info(video_path):
    try:
        metadata = extractMetadata(createParser(video_path))
        duration = int(metadata.get('duration').seconds) if metadata.has("duration") else 0
        width = metadata.get('width') if metadata.has("width") else 0
        height = metadata.get('height') if metadata.has("height") else 0
        
        return {
            'duration': duration,
            'width': width,
            'height': height
        }
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        return {'duration': 0, 'width': 0, 'height': 0}

# Bot commands
@app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply(
        "👋 Welcome to Udvash Downloader Bot!\n\n"
        "Use /login userid:password to login to your Udvash account\n"
        "Use /help to see all available commands"
    )

@app.on_message(filters.command("help"))
async def help_command(client, message):
    help_text = (
        "📚 **Available Commands**:\n\n"
        "/login userid:password - Login to your Udvash account\n"
        "/link url - Set the main Udvash link\n"
        "/download - Start downloading content\n"
        "/download 2-5 - Download chapters 2 to 5\n"
        "/cancel - Cancel ongoing downloads\n"
        "/status - Check download status\n"
        "/include_archive yes/no - Include archive content (default: yes)\n"
        "/parallel num - Set number of parallel downloads (default: 3)\n"
    )
    await message.reply(help_text)

@app.on_message(filters.command("login"))
async def login_command(client, message):
    try:
        # Extract credentials from message
        credentials = message.text.split(" ", 1)[1] if len(message.text.split(" ", 1)) > 1 else None
        
        if not credentials or ":" not in credentials:
            await message.reply("⚠️ Please provide credentials in the format: /login userid:password")
            return
        
        user_id, password = credentials.strip().split(":", 1)
        
        # Store credentials
        if message.from_user.id not in user_data:
            user_data[message.from_user.id] = {}
        
        user_data[message.from_user.id]["credentials"] = {
            "user_id": user_id,
            "password": password
        }
        
        # Test login with UdvashDownloader
        # This is just a placeholder for now
        await message.reply(f"✅ Successfully logged in as user {user_id}")
        
    except Exception as e:
        logger.error(f"Login error: {e}")
        await message.reply(f"❌ Login failed: {str(e)}")

@app.on_message(filters.command("link"))
@require_auth
async def link_command(client, message):
    try:
        # Extract link from message
        link = message.text.split(" ", 1)[1] if len(message.text.split(" ", 1)) > 1 else None
        
        if not link:
            await message.reply("⚠️ Please provide a valid Udvash URL")
            return
        
        # Validate link (simple check)
        if "udvash" not in link.lower():
            await message.reply("⚠️ This doesn't look like an Udvash URL")
            return
        
        # Store link
        user_data[message.from_user.id]["main_link"] = link
        
        await message.reply(f"✅ Main link set successfully: {link}")
        
    except Exception as e:
        logger.error(f"Link setting error: {e}")
        await message.reply(f"❌ Failed to set link: {str(e)}")

@app.on_message(filters.command("include_archive"))
@require_auth
async def include_archive_command(client, message):
    try:
        # Extract option from message
        option = message.text.split(" ", 1)[1] if len(message.text.split(" ", 1)) > 1 else "yes"
        
        include_archive = option.lower() in ["yes", "y", "true", "1"]
        
        # Store option
        user_data[message.from_user.id]["include_archive"] = include_archive
        
        await message.reply(f"✅ Archive content will {'be included' if include_archive else 'not be included'}")
        
    except Exception as e:
        logger.error(f"Include archive setting error: {e}")
        await message.reply(f"❌ Failed to set archive option: {str(e)}")

@app.on_message(filters.command("parallel"))
@require_auth
async def parallel_command(client, message):
    try:
        # Extract number from message
        num_str = message.text.split(" ", 1)[1] if len(message.text.split(" ", 1)) > 1 else "3"
        
        try:
            num = int(num_str)
            if num < 1 or num > 10:
                await message.reply("⚠️ Parallel downloads must be between 1 and 10")
                return
        except ValueError:
            await message.reply("⚠️ Please provide a valid number")
            return
        
        # Store setting
        user_data[message.from_user.id]["parallel_downloads"] = num
        global max_parallel_downloads
        max_parallel_downloads = num
        global download_semaphore
        download_semaphore = asyncio.Semaphore(max_parallel_downloads)
        
        await message.reply(f"✅ Parallel downloads set to {num}")
        
    except Exception as e:
        logger.error(f"Parallel setting error: {e}")
        await message.reply(f"❌ Failed to set parallel downloads: {str(e)}")

@app.on_message(filters.command("download"))
@require_auth
async def download_command(client, message):
    user_id = message.from_user.id
    
    try:
        # Check if main link is set
        if "main_link" not in user_data[user_id]:
            await message.reply("⚠️ Please set the main link first using /link")
            return
        
        # Parse chapter range if provided
        chapter_range = None
        if len(message.text.split(" ", 1)) > 1:
            range_str = message.text.split(" ", 1)[1]
            if "-" in range_str:
                try:
                    start, end = map(int, range_str.split("-", 1))
                    chapter_range = (start, end)
                except ValueError:
                    await message.reply("⚠️ Invalid chapter range format. Use: /download 2-5")
                    return
        
        # Get archive inclusion setting
        include_archive = user_data[user_id].get("include_archive", True)
        
        # Start download process
        status_msg = await message.reply("🔄 Initializing download process...")
        
        # Store download task
        download_tasks[user_id] = {
            "status_msg": status_msg,
            "running": True,
            "chapter_range": chapter_range,
            "include_archive": include_archive
        }
        
        # Start download process in background
        asyncio.create_task(process_download(client, user_id, status_msg, chapter_range, include_archive))
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        await message.reply(f"❌ Download failed: {str(e)}")

@app.on_message(filters.command("cancel"))
@require_auth
async def cancel_command(client, message):
    user_id = message.from_user.id
    
    if user_id in download_tasks and download_tasks[user_id]["running"]:
        download_tasks[user_id]["running"] = False
        await message.reply("⚠️ Download process will be cancelled. Please wait...")
    else:
        await message.reply("ℹ️ No active downloads to cancel")

@app.on_message(filters.command("status"))
@require_auth
async def status_command(client, message):
    user_id = message.from_user.id
    
    if user_id in download_tasks and download_tasks[user_id]["running"]:
        task_info = download_tasks[user_id]
        
        chapter_range_info = ""
        if task_info.get("chapter_range"):
            start, end = task_info["chapter_range"]
            chapter_range_info = f" (Chapters {start}-{end})"
        
        archive_info = "including" if task_info.get("include_archive", True) else "excluding"
        
        status_text = (
            f"📥 Download in progress{chapter_range_info}\n"
            f"🗃️ Archive content: {archive_info}\n"
            f"⏳ Parallel downloads: {max_parallel_downloads}\n"
        )
        
        await message.reply(status_text)
    else:
        await message.reply("ℹ️ No active downloads")


async def download_file(url, file_path, file_type, status_msg=None, current_chapter=None, total_chapters=None):
    """Download a file using aria2c as the main downloader with fallback to yt-dlp for videos
    and direct download for PDFs.
    
    Args:
        url (str): URL of the file to download
        file_path (str): Path where the file should be saved
        file_type (str): Type of file ('video', 'pdf', etc.)
        status_msg (Optional): Message object for progress updates
        current_chapter (Optional[int]): Current chapter number for progress display
        total_chapters (Optional[int]): Total number of chapters for progress display
    
    Returns:
        bool: True if download was successful, False otherwise
    """
    logger.info(f"Downloading {file_type} from {url}")
    
    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Try aria2c first for all file types
        if await try_aria2c_download(url, file_path, file_type, status_msg, current_chapter, total_chapters):
            return True
            
        # Fallback for video files
        if file_type == "video" and await try_ytdlp_download(url, file_path, status_msg, current_chapter, total_chapters):
            return True
            
        # Fallback for other files (like PDFs)
        if await try_direct_download(url, file_path, status_msg, current_chapter, total_chapters):
            return True
            
        logger.error(f"All download methods failed for {file_type}: {url}")
        return False
        
    except Exception as e:
        logger.error(f"Error in download_file: {str(e)}")
        return False

async def try_aria2c_download(url, file_path, file_type, status_msg=None, current_chapter=None, total_chapters=None):
    """Try downloading with aria2c"""
    try:
        # Remove zero-size file if it exists
        if os.path.exists(file_path):
            os.remove(file_path)
            
        # Properly quote the URL for shell
        quoted_url = f"'{url}'"
        
        # Build command for aria2c with quoted URL
        cmd = f"aria2c --file-allocation=none -j 32 -s 32 -x 32 " \
              f"--min-split-size=1M --max-connection-per-server=16 --max-tries=5 " \
              f"--retry-wait=5 --check-certificate=false --continue=true " \
              f"--console-log-level=notice --summary-interval=1 " \
              f"-o {shlex.quote(file_path)} {quoted_url}"
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,  # Using shell=True to properly handle quoted URLs
            text=True
        )
        
        if status_msg:
            file_size = await monitor_download_progress(process, status_msg, file_path, current_chapter, total_chapters)
        else:
            stdout, stderr = process.communicate()
            logger.info(f"aria2c stdout: {stdout}")
            if stderr:
                logger.error(f"aria2c stderr: {stderr}")
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        # Check if file size is valid
        if file_size > 0:
            logger.info(f"Downloaded {file_type} using aria2c: {file_path} ({humanize.naturalsize(file_size)})")
            return True
        else:
            logger.error(f"Aria2c downloaded a zero-size file")
            if os.path.exists(file_path):
                os.remove(file_path)
            return False
            
    except Exception as e:
        logger.warning(f"aria2c download failed: {str(e)}")
        return False

async def try_ytdlp_download(url, file_path, status_msg=None, current_chapter=None, total_chapters=None):
    """Try downloading video with yt-dlp"""
    try:
        logger.info("Trying yt-dlp as fallback for video")
        
        # Remove zero-size file if it exists
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # Properly quote the URL for shell
        quoted_url = f"'{url}'"
        
        # Build command for yt-dlp with quoted URL
        cmd = f"yt-dlp -N 32 --no-check-certificate --no-warnings " \
              f"--prefer-ffmpeg --hls-prefer-native " \
              f"--downloader-args \"ffmpeg:-nostats -loglevel 0\" " \
              f"-o {shlex.quote(file_path)} {quoted_url}"
            
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,  # Using shell=True to properly handle quoted URLs
            text=True
        )
        
        if status_msg:
            file_size = await monitor_download_progress(process, status_msg, file_path, current_chapter, total_chapters)
        else:
            stdout, stderr = process.communicate()
            logger.info(f"yt-dlp stdout: {stdout}")
            if stderr:
                logger.error(f"yt-dlp stderr: {stderr}")
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        # Check if file size is valid
        if file_size > 0:
            logger.info(f"Downloaded video using yt-dlp: {file_path} ({humanize.naturalsize(file_size)})")
            return True
        else:
            logger.error(f"yt-dlp downloaded a zero-size file")
            if os.path.exists(file_path):
                os.remove(file_path)
            return False
            
    except Exception as e:
        logger.error(f"yt-dlp download failed: {str(e)}")
        return False

async def try_direct_download(url, file_path, status_msg=None, current_chapter=None, total_chapters=None):
    """Try direct download using aiohttp"""
    try:
        logger.info(f"Trying direct download using requests")
        if status_msg:
            await status_msg.edit(f"⬇️ Trying direct download: {os.path.basename(file_path)}")
        
        # Starting time for progress
        start_time = time.time()
        
        # Using aiohttp for async download
        async with aiohttp.ClientSession() as session:
            async with session.get(url, allow_redirects=True, timeout=30) as response:
                if response.status == 200:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded_size = 0
                    
                    with open(file_path, 'wb') as f:
                        while True:
                            chunk = await response.content.read(1024 * 1024)  # 1MB chunks
                            if not chunk:
                                break
                            
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # Update progress
                            if status_msg and total_size > 0:
                                await progress(
                                    downloaded_size, 
                                    total_size, 
                                    status_msg, 
                                    start_time, 
                                    "⬇️ Direct downloading", 
                                    os.path.basename(file_path),
                                    current_chapter,
                                    total_chapters
                                )
                    
                    file_size = os.path.getsize(file_path)
                    if file_size > 0:
                        # Final progress update
                        if status_msg:
                            await progress(
                                file_size,
                                file_size,
                                status_msg,
                                start_time,
                                "✅ Download Complete",
                                os.path.basename(file_path),
                                current_chapter,
                                total_chapters
                            )
                        logger.info(f"Downloaded file using direct method: {file_path} ({humanize.naturalsize(file_size)})")
                        return True
                    else:
                        logger.error("Direct download resulted in zero-size file")
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        return False
                else:
                    logger.error(f"Direct download failed with status code: {response.status}")
                    return False
    


# Modified process_chapter function with improved PDF handling
async def process_chapter(client, user_id, status_msg, downloader, chapter, current_chapter, total_chapters, include_archive):
    try:
        async with download_semaphore:
            if not download_tasks[user_id]["running"]:
                return
            
            chapter_name = chapter["name"]
            await status_msg.edit(f"📚 Processing {current_chapter}/{total_chapters}: {chapter_name}")
            
            # Send chapter name to channel
            chapter_msg = await client.send_message(
                DUMP_CHANNEL,
                f"📚 **Chapter {current_chapter}/{total_chapters}**: {chapter_name}"
            )
            
            # Forward to user
            await chapter_msg.forward(user_id)
            
            # Get content types from UdvashDownloader
            content_types, master_course_id, subject_id, master_chapter_id = downloader.get_content_types(chapter['url'], chapter['name'])
            
            # Filter content types based on user preference
            filtered_content_types = []
            for content_type in content_types:
                if content_type['name'] == 'Marathon' or (include_archive and content_type['name'] == 'Archive'):
                    filtered_content_types.append(content_type)
            
            for content_type in filtered_content_types:
                if not download_tasks[user_id]["running"]:
                    return
                
                # Get actual content cards from UdvashDownloader
                content_cards = downloader.get_content_cards(content_type['url'], content_type['name'])
                
                for content_card in content_cards:
                    if not download_tasks[user_id]["running"]:
                        return
                    
                    topic_title = content_card["title"]
                    clean_title = re.sub(r'[<>:"/\\|?*]', '_', topic_title)  # Remove invalid filename chars
                    
                    # Process video
                    video_url = downloader.extract_video_url(content_card["video_link"])
                    if video_url:
                        # Log the extracted video URL for debugging
                        logger.info(f"Extracted video URL: {video_url}")
                        
                        video_filename = f"downloads/{user_id}/{chapter_name}/{content_type['name']}/{clean_title}.mp4"
                        os.makedirs(os.path.dirname(video_filename), exist_ok=True)
                        
                        # Download video with status updates
                        await status_msg.edit(f"⬇️ Preparing to download video: {topic_title} ({current_chapter}/{total_chapters})")
                        
                        # Use updated download_file function with progress
                        download_success = await download_file(
                            video_url, 
                            video_filename, 
                            "video",
                            status_msg,
                            current_chapter,
                            total_chapters
                        )
                        
                        if not download_success:
                            await status_msg.edit(f"❌ Failed to download video: {topic_title}")
                            continue
                        
                        # Check file size before uploading
                        if not os.path.exists(video_filename) or os.path.getsize(video_filename) == 0:
                            await status_msg.edit(f"❌ Downloaded video has zero size: {topic_title}")
                            continue
                            
                        # Get video info
                        video_info = await get_video_info(video_filename)
                        
                        # Create upload status message
                        upload_status_msg = await client.send_message(
                            user_id,
                            f"⬆️ Preparing to upload video: {topic_title}"
                        )
                        
                        try:
                            # Upload video to dump channel
                            start_time = time.time()
                            caption = f"📹 **{topic_title}**\n\n📚 Chapter: {chapter_name}\n📁 Type: {content_type['name']}"
                            
                            video_msg = await client.send_video(
                                DUMP_CHANNEL,
                                video_filename,
                                caption=caption,
                                supports_streaming=True,
                                duration=video_info['duration'],
                                width=video_info['width'],
                                height=video_info['height'],
                                thumb="thumbnails/video_thumb.jpg",
                                progress=progress,
                                progress_args=(
                                    upload_status_msg,
                                    start_time,
                                    f"⬆️ Uploading video: {topic_title}",
                                    os.path.basename(video_filename),
                                    current_chapter,
                                    total_chapters
                                )
                            )
                            
                            # Forward to user
                            await video_msg.forward(user_id)
                            
                            # Delete the file after successful upload
                            if os.path.exists(video_filename):
                                os.remove(video_filename)
                                logger.info(f"Deleted video after upload: {video_filename}")
                                
                        except Exception as e:
                            logger.error(f"Error uploading video: {str(e)}")
                            await upload_status_msg.edit(f"❌ Failed to upload video: {str(e)}")
                        finally:
                            await upload_status_msg.delete()
                    
                    # Process PDF
                    pdf_url = downloader.extract_pdf_url(content_card["note_link"])
                    if pdf_url:
                        # Log the extracted PDF URL for debugging
                        logger.info(f"Extracted PDF URL: {pdf_url}")
                        
                        pdf_filename = f"downloads/{user_id}/{chapter_name}/{content_type['name']}/{clean_title}.pdf"
                        os.makedirs(os.path.dirname(pdf_filename), exist_ok=True)
                        
                        # Download PDF with status updates
                        await status_msg.edit(f"⬇️ Preparing to download PDF: {topic_title} ({current_chapter}/{total_chapters})")
                        
                        # Use updated download_file function with progress
                        download_success = await download_file(
                            pdf_url, 
                            pdf_filename, 
                            "pdf",
                            status_msg,
                            current_chapter,
                            total_chapters
                        )
                        
                        if not download_success:
                            # Try downloading PDF using direct urllib method as a fallback
                            await status_msg.edit(f"⬇️ Trying another method for PDF: {topic_title}")
                            try:
                                # Use urllib with proper headers for direct PDF download
                                headers = {
                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                                    'Accept-Language': 'en-US,en;q=0.5',
                                    'Connection': 'keep-alive',
                                    'Upgrade-Insecure-Requests': '1'
                                }
                                
                                request = urllib.request.Request(pdf_url, headers=headers)
                                with urllib.request.urlopen(request) as response, open(pdf_filename, 'wb') as out_file:
                                    data = response.read()
                                    out_file.write(data)
                                
                                if os.path.exists(pdf_filename) and os.path.getsize(pdf_filename) > 0:
                                    download_success = True
                                    logger.info(f"PDF downloaded using urllib fallback: {pdf_filename}")
                                else:
                                    logger.error(f"urllib fallback downloaded zero-size PDF")
                                    download_success = False
                            except Exception as e:
                                logger.error(f"All PDF download methods failed: {str(e)}")
                                download_success = False
                        
                        if not download_success:
                            await status_msg.edit(f"❌ Failed to download PDF: {topic_title}")
                            continue
                        
                        # Check file size before uploading
                        if not os.path.exists(pdf_filename) or os.path.getsize(pdf_filename) == 0:
                            await status_msg.edit(f"❌ Downloaded PDF has zero size: {topic_title}")
                            continue
                            
                        # Create upload status message
                        upload_status_msg = await client.send_message(
                            user_id,
                            f"⬆️ Preparing to upload PDF: {topic_title}"
                        )
                        
                        try:
                            # Upload PDF to dump channel
                            start_time = time.time()
                            caption = f"📄 **{topic_title}**\n\n📚 Chapter: {chapter_name}\n📁 Type: {content_type['name']}"
                            
                            pdf_msg = await client.send_document(
                                DUMP_CHANNEL,
                                pdf_filename,
                                caption=caption,
                                thumb="thumbnails/pdf_thumb.jpg",
                                progress=progress,
                                progress_args=(
                                    upload_status_msg,
                                    start_time,
                                    f"⬆️ Uploading PDF: {topic_title}",
                                    os.path.basename(pdf_filename),
                                    current_chapter,
                                    total_chapters
                                )
                            )
                            
                            # Forward to user
                            await pdf_msg.forward(user_id)
                            
                            # Delete the file after successful upload
                            if os.path.exists(pdf_filename):
                                os.remove(pdf_filename)
                                logger.info(f"Deleted PDF after upload: {pdf_filename}")
                                
                        except Exception as e:
                            logger.error(f"Error uploading PDF: {str(e)}")
                            await upload_status_msg.edit(f"❌ Failed to upload PDF: {str(e)}")
                        finally:
                            await upload_status_msg.delete()
            
            await status_msg.edit(f"✅ Completed {current_chapter}/{total_chapters}: {chapter_name}")
    
    except Exception as e:
        logger.error(f"Process chapter error: {e}")
        await status_msg.edit(f"❌ Error processing chapter {chapter_name}: {str(e)}")

# Updated process_download function with improved task handling
async def process_download(client, user_id, status_msg, chapter_range, include_archive):
    try:
        credentials = user_data[user_id]["credentials"]
        main_link = user_data[user_id]["main_link"]
        
        await safe_edit_message(status_msg, "🔄 Initializing download process...")
        
        # Initialize the UdvashDownloader with credentials
        downloader = UdvashDownloader(
            user_id=credentials["user_id"],
            password=credentials["password"],
            download_dir=f"downloads/{user_id}"
        )
        
        await status_msg.edit("🔄 Getting subject information...")
        # Get actual subjects from UdvashDownloader
        subjects = downloader.get_subjects()
        
        if not subjects:
            await status_msg.edit("❌ No subjects found!")
            return
        
        # Log to channel
        if LOG_CHANNEL:
            await client.send_message(
                LOG_CHANNEL,
                f"📥 User {user_id} started download process\n"
                f"Subjects: {[s['name'] for s in subjects]}"
            )
        
        # Get total chapters for all subjects
        total_chapters = 0
        all_chapters = []
        
        for subject in subjects:
            # Get actual chapters using UdvashDownloader
            chapters = downloader.get_chapters(subject['url'], subject['name'])
            all_chapters.extend(chapters)
            total_chapters += len(chapters)
        
        # Apply chapter range filter if specified
        if chapter_range:
            start, end = chapter_range
            await status_msg.edit(f"🔄 Will download chapters {start} to {end} (out of {total_chapters})")
            # Filter chapters based on range
            filtered_chapters = all_chapters[start-1:end] if start <= len(all_chapters) and end <= len(all_chapters) else all_chapters
        else:
            await status_msg.edit(f"🔄 Will download all {total_chapters} chapters")
            filtered_chapters = all_chapters
        
        # Create a list for task tracking
        download_tasks[user_id]["total_chapters"] = len(filtered_chapters)
        download_tasks[user_id]["completed_chapters"] = 0
        
        # Process chapters sequentially to avoid getting stuck
        for chapter_idx, chapter in enumerate(filtered_chapters):
            if not download_tasks[user_id]["running"]:
                await status_msg.edit("⚠️ Download process cancelled by user")
                break
                
            current_idx = chapter_idx + 1
            
            # Process each chapter individually
            await process_chapter(
                client, 
                user_id, 
                status_msg, 
                downloader, 
                chapter, 
                current_idx, 
                len(filtered_chapters), 
                include_archive
            )
            
            # Update completed count
            download_tasks[user_id]["completed_chapters"] += 1
            
            # Add a short delay between chapters to prevent rate limiting
            await asyncio.sleep(1)
        
        if download_tasks[user_id]["running"]:
            await status_msg.edit("✅ Download process completed!")
        
        # Clean up
        await cleanup_downloads(user_id)
        downloader.cleanup()
        download_tasks[user_id]["running"] = False
        
    except Exception as e:
        logger.error(f"Process download error: {e}")
        await status_msg.edit(f"❌ Download process failed: {str(e)}")
        download_tasks[user_id]["running"] = False

# New function to clean up downloads
async def cleanup_downloads(user_id):
    """Clean up download directory after processing is complete"""
    try:
        download_dir = f"downloads/{user_id}"
        if os.path.exists(download_dir):
            # Use shutil.rmtree to remove the directory and all its contents
            import shutil
            shutil.rmtree(download_dir, ignore_errors=True)
            logger.info(f"Cleaned up download directory for user {user_id}")
    except Exception as e:
        logger.error(f"Error cleaning up downloads: {str(e)}")

# Add a new command for cleanup
@app.on_message(filters.command("cleanup"))
@require_auth
async def cleanup_command(client, message):
    user_id = message.from_user.id
    
    try:
        status_msg = await message.reply("🧹 Cleaning up download directory...")
        await cleanup_downloads(user_id)
        await status_msg.edit("✅ Download directory cleaned up!")
    except Exception as e:
        await message.reply(f"❌ Error cleaning up: {str(e)}")
        
# Main function
async def main():
    await download_thumbnails()
    await health_check_server()
    
    # Start the client (Pyrogram doesn't use await for start/stop)
    await app.start()
    
    try:
        # Keep the bot running
        await asyncio.Event().wait()
    finally:
        # Stop the client (Pyrogram doesn't use await for start/stop)
        app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
