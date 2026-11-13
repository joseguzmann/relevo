FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
# Captcha "live apply": a headed patchright browser Relevo fills, shown over VNC through the app (phone-only).
# pip first (clean), then the VNC apt packages (their python3-* land in dist-packages; pip's in /usr/local win).
RUN pip install --no-cache-dir --break-system-packages playwright==1.49.0 patchright fastapi "uvicorn[standard]" pywebpush cryptography pypdf
RUN apt-get update && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1500 relevo
USER relevo
RUN playwright install chromium chromium-headless-shell
RUN patchright install chromium
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH=/home/relevo/.local/bin:$PATH PYTHONPATH=/app/src DISPLAY=:99 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --chown=relevo src ./src
COPY --chown=relevo deploy/entrypoint.sh /entrypoint.sh
EXPOSE 8000
CMD ["/entrypoint.sh"]
