FROM python:3.11-slim

WORKDIR /app

# 1. System deps + noVNC dependencies (rarely changes — cached)
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify \
        libgtk-3-0t64 \
    && rm -rf /var/lib/apt/lists/*

# 2. Install all Python dependencies from pinned+hashed lockfile (re-runs only when lockfile changes)
COPY requirements-api.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements-api.lock \
    && playwright install --with-deps chromium

# 3. Install package metadata only (entry points) — deps already above
COPY pyproject.toml .
COPY src/releasi/__init__.py src/releasi/__init__.py
RUN pip install --no-cache-dir --no-deps ".[api]"

# 4. Copy full application source (changes frequently)
COPY . .

# 5. Reinstall package metadata so entry points pick up full source (deps already above — fast)
RUN pip install --no-cache-dir --no-deps ".[api]"

# 6. Create data directories
RUN mkdir -p data/browser_data data/logs

# 7. Run as non-root user — copy Playwright browsers to appuser's home
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
