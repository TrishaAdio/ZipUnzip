FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FORCE_COLOR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY scripts ./scripts

# Runs as root on purpose: the archive files are written into the shared volume
# by the API server under its own uid, and this process has to read and delete them.
CMD ["python", "-m", "bot"]
