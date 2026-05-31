FROM python:3.11-slim

WORKDIR /app

# 1. Install Playwright browser + system deps + noVNC dependencies (rarely changes — cached)
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright playwright-stealth \
    && playwright install --with-deps chromium

# 2. Install Python dependencies only (re-runs only when pyproject.toml changes)
COPY pyproject.toml .
COPY src/releasi/__init__.py src/releasi/__init__.py
RUN pip install --no-cache-dir ".[api]"

# 3. Copy full application source (changes frequently)
COPY . .

# 4. Reinstall package so entry points pick up full source (deps already cached — fast)
RUN pip install --no-cache-dir --no-deps ".[api]"

# 5. Create data directories
RUN mkdir -p data/browser_data data/logs

# 6. Run as non-root user — copy Playwright browsers to appuser's home
RUN useradd -m appuser && chown -R appuser:appuser /app \
    && mkdir -p /home/appuser/.cache \
    && cp -r /root/.cache/ms-playwright /home/appuser/.cache/ms-playwright \
    && chown -R appuser:appuser /home/appuser/.cache
USER appuser

EXPOSE 8000

# Ensure the volume-mounted /app/src takes precedence over site-packages so
# that `git pull` on the host is sufficient for Python code changes without
# requiring an image rebuild.
ENV PYTHONPATH=/app/src

CMD ["releasi", "run"]
