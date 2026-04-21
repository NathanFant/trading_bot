FROM python:3.12-slim

WORKDIR /app

# Install deps first so this layer is cached unless requirements change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source (no .env.local — secrets come from the platform)
COPY *.py ./

# Persistent data lives on a mounted volume; DATA_DIR is overridden at runtime
ENV DATA_DIR=/data \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

# Ensure the data dir exists at build time as a fallback
RUN mkdir -p /data

CMD ["python", "main.py"]
