FROM python:3.11-slim

# Install system utilities, ffmpeg, and fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-montserrat \
    fonts-freefont-ttf \
    fontconfig \
    curl \
    git \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium with browser dependencies
RUN playwright install --with-deps chromium

# Copy project files
COPY . .

# Set default entrypoint
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--source", "auto", "--dry-run"]
