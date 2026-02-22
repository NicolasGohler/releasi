FROM python:3.11-slim

# Install system dependencies for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir .

# Install Playwright Chromium browser
RUN playwright install chromium

# Create data directories
RUN mkdir -p data/browser_data data/logs

# Run as non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Default command: run the scheduler
CMD ["python", "-m", "linauto", "run"]
