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

# Run the bot
CMD ["python", "bot.py"]
