import os
import time
import logging
import argparse
import subprocess
import requests
import re
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

class UdvashDownloader:
    def __init__(self, user_id, password, max_parallel_downloads=3, download_dir="downloads"):
        # Setup logging
        self.setup_logger()
        
        # Login credentials
        self.user_id = user_id
        self.password = password
        
        # Download settings
        self.download_dir = download_dir
        self.max_parallel_downloads = max_parallel_downloads
        self.active_downloads = 0
        self.download_queue = []
        
        # Create download directory
        os.makedirs(download_dir, exist_ok=True)
        
        # Configure Chrome webdriver
        self.setup_webdriver()
        
        # Wait conditions
        self.wait = WebDriverWait(self.driver, 20)
        self.short_wait = WebDriverWait(self.driver, 5)
        
        # Login to the website
        if not self.login():
            self.logger.error("Login failed! Exiting...")
            self.cleanup()
            exit(1)
    
    def setup_logger(self):
        """Set up logging configuration"""
        self.logger = logging.getLogger("udvash_downloader")
        self.logger.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
        # Create console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # Create file handler
        file_handler = logging.FileHandler("udvash_downloader.log")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
    
    def setup_webdriver(self):
        """Configure and initialize Chrome webdriver"""
        chrome_options = Options()
        chrome_options.add_argument("--headless")  # Run in headless mode
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=640,360")
        chrome_options.add_argument("--log-level=3")  # Suppress logging
        
        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.set_page_load_timeout(60)
        self.logger.info("WebDriver initialized successfully")
    
    def login(self):
        """Handle login process"""
        self.logger.info("Attempting login...")
        try:
            self.driver.get("https://online.udvash-unmesh.com/Account/Login")
            time.sleep(1)
            
            # Enter registration number
            reg_input = self.wait.until(EC.presence_of_element_located((By.ID, "RegistrationNumber")))
            reg_input.send_keys(self.user_id)
            
            # Click continue
            continue_btn = self.wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit")))
            continue_btn.click()
            time.sleep(1)
            
            # Enter password
            pass_input = self.wait.until(EC.presence_of_element_located((By.ID, "Password")))
            pass_input.send_keys(self.password)
            
            # Click login
            login_btn = self.wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "uu-button-style-2")))
            login_btn.click()
            
            # Wait for dashboard
            self.wait.until(EC.url_contains("Dashboard"))
            self.logger.info("Login successful!")
            return True
        except Exception as e:
            self.logger.error(f"Login failed: {str(e)}")
            return False
    
    def wait_for_elements(self, css_selector, timeout=20):
        """Wait for elements to be present and return them"""
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
            )
            return self.driver.find_elements(By.CSS_SELECTOR, css_selector)
        except TimeoutException:
            self.logger.warning(f"Timeout waiting for elements: {css_selector}")
            return []
    
    def get_subjects(self, course_type_id=2, master_course_id=11):
        """Get all subject links and names"""
        self.logger.info("Getting subjects...")
        # Force English language in URL
        self.driver.get(f"https://online.udvash-unmesh.com/Content/ContentSubject?CourseTypeId={course_type_id}&masterCourseId={master_course_id}&ln=En")
        
        subjects = []
        subject_elements = self.wait_for_elements("div.col-xl-4.col-lg-6.d-flex a")
        
        for idx, element in enumerate(subject_elements, 1):
            try:
                href = element.get_attribute("href")
                # Ensure English language in URL
                if "ln=Bn" in href:
                    href = href.replace("ln=Bn", "ln=En")
                    
                name_elem = element.find_element(By.CSS_SELECTOR, "h3")
                name = name_elem.text.strip()
                
                # Extract subject ID from href
                url_parts = urlparse(href)
                query_params = parse_qs(url_parts.query)
                subject_id = query_params.get('subjectId', [''])[0]
                
                subjects.append({
                    'index': idx,
                    'name': name,
                    'url': href,
                    'id': subject_id
                })
                self.logger.info(f"Found subject {idx}: {name} (ID: {subject_id})")
            except Exception as e:
                self.logger.error(f"Error processing subject element: {str(e)}")
        
        return subjects
    
    def get_chapters(self, subject_url, subject_name):
        """Get all chapter links and names for a subject"""
        self.logger.info(f"Getting chapters for subject: {subject_name}")
        try:
            # Ensure English language in URL
            if "ln=Bn" in subject_url:
                subject_url = subject_url.replace("ln=Bn", "ln=En")
                
            self.driver.get(subject_url)
            
            chapters = []
            chapter_elements = self.wait_for_elements("div.col-xl-4.col-lg-6.d-flex a")
            
            for idx, element in enumerate(chapter_elements, 1):
                try:
                    href = element.get_attribute("href")
                    # Ensure English language in URL
                    if "ln=Bn" in href:
                        href = href.replace("ln=Bn", "ln=En")
                        
                    name_elem = element.find_element(By.CSS_SELECTOR, "h3")
                    chapter_name = name_elem.text.strip()
                    
                    # Extract chapter ID from href
                    url_parts = urlparse(href)
                    query_params = parse_qs(url_parts.query)
                    chapter_id = query_params.get('masterChapterId', [''])[0]
                    
                    chapters.append({
                        'index': f"{subject_name.split()[0]}.{idx}",
                        'name': chapter_name,
                        'url': href,
                        'id': chapter_id
                    })
                    self.logger.info(f"Found chapter {idx}: {chapter_name} (ID: {chapter_id})")
                except Exception as e:
                    self.logger.error(f"Error processing chapter element: {str(e)}")
            
            return chapters
        except Exception as e:
            self.logger.error(f"Error getting chapters: {str(e)}")
            return []
    
    def get_content_types(self, chapter_url, chapter_name):
        """Get content types (marathon, archive, etc.) for a chapter"""
        self.logger.info(f"Getting content types for chapter: {chapter_name}")
        try:
            # Ensure English language in URL
            if "ln=Bn" in chapter_url:
                chapter_url = chapter_url.replace("ln=Bn", "ln=En")
                
            self.driver.get(chapter_url)
            time.sleep(2)
            
            # Extract parameters from the current URL
            url_parts = urlparse(self.driver.current_url)
            query_params = parse_qs(url_parts.query)
            master_course_id = query_params.get('masterCourseId', [''])[0]
            subject_id = query_params.get('subjectId', [''])[0]
            master_chapter_id = query_params.get('masterChapterId', [''])[0]
            
            content_types = []
            
            # Marathon content (masterContentTypeId=2)
            marathon_url = f"https://online.udvash-unmesh.com/Content/DisplayContentCard?masterCourseId={master_course_id}" + \
                          f"&subjectId={subject_id}&masterChapterId={master_chapter_id}&masterContentTypeId=2&ln=En"
            content_types.append({
                'name': 'Marathon',
                'url': marathon_url,
                'type_id': '2'
            })
            
            # Archive content (masterContentTypeId=9)
            archive_url = f"https://online.udvash-unmesh.com/Content/DisplayContentCard?masterCourseId={master_course_id}" + \
                         f"&subjectId={subject_id}&masterChapterId={master_chapter_id}&masterContentTypeId=9&ln=En"
            content_types.append({
                'name': 'Archive',
                'url': archive_url,
                'type_id': '9'
            })
            
            return content_types, master_course_id, subject_id, master_chapter_id
        except Exception as e:
            self.logger.error(f"Error getting content types: {str(e)}")
            return [], '', '', ''
    
    def get_content_cards(self, content_type_url, content_type_name):
        """Get content cards from a content type page"""
        self.logger.info(f"Getting content cards for {content_type_name}...")
        try:
            # Ensure English language in URL
            if "ln=Bn" in content_type_url:
                content_type_url = content_type_url.replace("ln=Bn", "ln=En")
            elif "ln=" not in content_type_url:
                content_type_url += "&ln=En"
                
            self.driver.get(content_type_url)
            time.sleep(2)
            
            cards = []
            card_elements = self.wait_for_elements("div.col-xl-3.col-lg-4.col-md-6.d-flex .card")
            
            for idx, card in enumerate(card_elements, 1):
                try:
                    # Get card title
                    title_elem = card.find_element(By.CSS_SELECTOR, "h2.uuu-wrap-title")
                    title = title_elem.text.strip()
                    
                    # Get video and note links
                    video_link = card.find_element(By.CSS_SELECTOR, "a.btn-video").get_attribute("href")
                    note_link = card.find_element(By.CSS_SELECTOR, "a.btn-note").get_attribute("href")
                    
                    # Ensure English language in URLs
                    if "ln=Bn" in video_link:
                        video_link = video_link.replace("ln=Bn", "ln=En")
                    if "ln=Bn" in note_link:
                        note_link = note_link.replace("ln=Bn", "ln=En")
                    
                    # Extract content ID from the link
                    url_parts = urlparse(video_link)
                    query_params = parse_qs(url_parts.query)
                    content_id = query_params.get('masterContentId', [''])[0]
                    
                    cards.append({
                        'index': idx,
                        'title': title,
                        'video_link': video_link,
                        'note_link': note_link,
                        'content_id': content_id
                    })
                    self.logger.info(f"Found content card {idx}: {title} (ID: {content_id})")
                except NoSuchElementException:
                    self.logger.warning(f"Skipping a card that doesn't have all required elements")
                except Exception as e:
                    self.logger.error(f"Error processing content card: {str(e)}")
            
            return cards
        except Exception as e:
            self.logger.error(f"Error getting content cards: {str(e)}")
            return []
    
    def extract_video_url(self, video_page_url):
        """Extract video download URL from video page"""
        self.logger.info(f"Extracting video URL from: {video_page_url}")
        try:
            # Ensure English language in URL
            if "ln=Bn" in video_page_url:
                video_page_url = video_page_url.replace("ln=Bn", "ln=En")
            elif "ln=" not in video_page_url:
                video_page_url += "&ln=En"
                
            self.driver.get(video_page_url)
            time.sleep(2)
            
            # Get page source and find video source
            page_source = self.driver.page_source
            video_src_match = re.search(r'<source src="([^"]+)" type="video/mp4">', page_source)
            
            if video_src_match:
                # Get the raw URL and decode HTML entities like &amp;
                raw_video_url = video_src_match.group(1)
                
                # Replace HTML entities with proper characters
                video_url = raw_video_url.replace("&amp;", "&")
                
                self.logger.info(f"Found video URL: {video_url[:100]}...")
                return video_url
            else:
                self.logger.warning("No video source found in the page")
                return None
        except Exception as e:
            self.logger.error(f"Error extracting video URL: {str(e)}")
            return None
    
    def extract_pdf_url(self, pdf_page_url):
        """Extract PDF download URL from PDF/note page"""
        self.logger.info(f"Extracting PDF URL from: {pdf_page_url}")
        try:
            # Ensure English language in URL
            if "ln=Bn" in pdf_page_url:
                pdf_page_url = pdf_page_url.replace("ln=Bn", "ln=En")
            elif "ln=" not in pdf_page_url:
                pdf_page_url += "&ln=En"
                
            self.driver.get(pdf_page_url)
            time.sleep(2)
            
            # Look for download button with href
            pdf_link_elem = self.driver.find_element(By.CSS_SELECTOR, "a.btn-success[href]")
            raw_pdf_url = pdf_link_elem.get_attribute("href")
            
            # Replace HTML entities (just in case)
            pdf_url = raw_pdf_url.replace("&amp;", "&") if raw_pdf_url else None
            
            if pdf_url:
                self.logger.info(f"Found PDF URL: {pdf_url[:100]}...")
                return pdf_url
            else:
                self.logger.warning("No PDF download link found")
                return None
        except NoSuchElementException:
            self.logger.warning("PDF download button not found on the page")
            return None
        except Exception as e:
            self.logger.error(f"Error extracting PDF URL: {str(e)}")
            return None
    
    def process_content(self, chapter_name, content_card, master_course_id, subject_id, master_chapter_id, content_type_name):
        """Process a content card for both video and PDF download - English only"""
        title = content_card['title']
        clean_title = re.sub(r'[<>:"/\\|?*]', '_', title)  # Remove invalid filename chars
        
        # Create directory for the download
        base_dir = os.path.join(self.download_dir, chapter_name, content_type_name)
        os.makedirs(base_dir, exist_ok=True)
        
        # Process English video
        try:
            self.logger.info(f"Processing English video for: {title}")
            video_url = content_card['video_link']
            # Ensure English language in URL
            if "ln=Bn" in video_url:
                video_url = video_url.replace("ln=Bn", "ln=En")
            elif "ln=" not in video_url:
                video_url += "&ln=En"
                
            video_download_url = self.extract_video_url(video_url)
            
            if video_download_url:
                video_filename = f"{clean_title}.mp4"
                video_path = os.path.join(base_dir, video_filename)
                
                # Check if file already exists
                if os.path.exists(video_path):
                    self.logger.info(f"Video already exists, skipping: {video_filename}")
                else:
                    self.queue_download(video_download_url, video_path, "video")
            else:
                self.logger.warning(f"No video URL found for English version of {title}")
        except Exception as e:
            self.logger.error(f"Error processing video content: {str(e)}")
        
        # Process English PDF/note
        try:
            self.logger.info(f"Processing English PDF for: {title}")
            pdf_url = content_card['note_link']
            # Ensure English language in URL
            if "ln=Bn" in pdf_url:
                pdf_url = pdf_url.replace("ln=Bn", "ln=En")
            elif "ln=" not in pdf_url:
                pdf_url += "&ln=En"
                
            pdf_download_url = self.extract_pdf_url(pdf_url)
            
            if pdf_download_url:
                pdf_filename = f"{clean_title}.pdf"
                pdf_path = os.path.join(base_dir, pdf_filename)
                
                # Check if file already exists
                if os.path.exists(pdf_path):
                    self.logger.info(f"PDF already exists, skipping: {pdf_filename}")
                else:
                    self.queue_download(pdf_download_url, pdf_path, "pdf")
            else:
                self.logger.warning(f"No PDF URL found for English version of {title}")
        except Exception as e:
            self.logger.error(f"Error processing PDF content: {str(e)}")
        
        # Bangla version processing is commented out
        # No processing for Bangla content
    
    def wait_for_downloads_to_complete(self):
        """Wait for all downloads to complete"""
        self.logger.info("Waiting for all downloads to complete...")
        while self.active_downloads > 0 or self.download_queue:
            time.sleep(1)
        self.logger.info("All downloads completed!")
    
    def process_chapter(self, chapter):
        """Process a single chapter"""
        self.logger.info(f"Processing chapter: {chapter['index']} {chapter['name']}")
        
        try:
            # Get content types (marathon, archive)
            content_types, master_course_id, subject_id, master_chapter_id = self.get_content_types(chapter['url'], chapter['name'])
            
            if not content_types:
                self.logger.warning(f"No content types found for chapter: {chapter['name']}")
                return
            
            # Process each content type
            for content_type in content_types:
                self.logger.info(f"Processing content type: {content_type['name']} for chapter: {chapter['name']}")
                
                # Get content cards for this type
                content_cards = self.get_content_cards(content_type['url'], content_type['name'])
                
                if not content_cards:
                    self.logger.warning(f"No content cards found for {content_type['name']}")
                    continue
                
                # Process each content card
                for card in content_cards:
                    # Only process English version
                    self.process_content(
                        chapter['name'], 
                        card, 
                        master_course_id, 
                        subject_id, 
                        master_chapter_id, 
                        content_type['name']
                    )
                    
                    # Bangla version processing is commented out
                    # No processing for Bangla content
        except Exception as e:
            self.logger.error(f"Error processing chapter {chapter['name']}: {str(e)}")
    
    # Add a cleanup method to UdvashDownloader
    def cleanup(self):
        """Close the webdriver and clean up resources"""
        try:
            if hasattr(self, 'driver') and self.driver:
                self.driver.quit()
                self.logger.info("WebDriver closed successfully")
        except Exception as e:
            self.logger.error(f"Error closing WebDriver: {str(e)}")
        