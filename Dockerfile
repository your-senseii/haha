FROM python:3.10-slim
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    yt-dlp \
    chromium\
    chromium-driver \
    wget \
    curl \
    unzip \
    xvfb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
    
# Copy requirements first to leverage Docker cache
COPY requirements.txt .
# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
# Copy the source code
COPY . .

# Environment variables (these will be overridden by GitHub Actions)
ENV UDVASH_USER_ID=""
ENV UDVASH_PASSWORD=""
ENV TELEGRAM_API_ID=""
ENV TELEGRAM_API_HASH=""
ENV TELEGRAM_BOT_TOKEN=""
ENV TELEGRAM_CHAT_ID=""
ENV DOWNLOAD_DIR="downloads"
ENV MAX_DOWNLOADS="3"
ENV MAX_UPLOADS="3"
ENV NO_ARCHIVE="true"
ENV NO_BANGLA="true"
ENV ONLY_VIDEO="true"

# Run the bot
CMD ["python", "bot1.py"]
