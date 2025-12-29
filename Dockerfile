FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY enricher.py .

# Default environment variables
ENV MUSIC_FOLDER=/music
ENV SCAN_INTERVAL=3600

# Create music mount point
RUN mkdir -p /music

# Run in daemon mode by default
CMD ["python", "-u", "enricher.py", "--daemon"]
