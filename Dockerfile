FROM python:3.11-slim

WORKDIR /app

# Copy application
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir .

# Install Playwright Chromium browser + system dependencies
RUN playwright install --with-deps chromium

# Create data directories
RUN mkdir -p data/browser_data data/logs

# Run as non-root user — copy Playwright browsers to appuser's home
RUN useradd -m appuser && chown -R appuser:appuser /app \
    && mkdir -p /home/appuser/.cache \
    && cp -r /root/.cache/ms-playwright /home/appuser/.cache/ms-playwright \
    && chown -R appuser:appuser /home/appuser/.cache
USER appuser

# Default command: run the scheduler
CMD ["python", "-m", "linauto", "run"]
