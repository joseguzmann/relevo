FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
RUN pip install --no-cache-dir --break-system-packages playwright==1.49.0 fastapi "uvicorn[standard]" pywebpush cryptography pypdf
RUN useradd -m -u 1500 relevo
USER relevo
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH=/home/relevo/.local/bin:$PATH PYTHONPATH=/app/src
WORKDIR /app
COPY --chown=relevo src ./src
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "relevo.api:app", "--host", "0.0.0.0", "--port", "8000"]
