FROM python:3.11-slim

WORKDIR /app

# 1. Install Playwright browser + system deps (rarely changes — cached)
RUN pip install --no-cache-dir playwright playwright-stealth \
    && playwright install --with-deps chromium

# 2. Install Python dependencies only (re-runs only when pyproject.toml changes)
COPY pyproject.toml .
COPY src/linauto/__init__.py src/linauto/__init__.py
RUN pip install --no-cache-dir .

# 3. Copy full application source (changes frequently)
COPY . .

# 4. Reinstall package so entry points pick up full source (deps already cached — fast)
RUN pip install --no-cache-dir --no-deps .

# 5. Create data directories
RUN mkdir -p data/browser_data data/logs

# 6. Run as non-root user — copy Playwright browsers to appuser's home
RUN useradd -m appuser && chown -R appuser:appuser /app \
    && mkdir -p /home/appuser/.cache \
    && cp -r /root/.cache/ms-playwright /home/appuser/.cache/ms-playwright \
    && chown -R appuser:appuser /home/appuser/.cache
USER appuser

CMD ["linauto", "run"]
