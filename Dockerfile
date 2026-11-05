FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8070 \
    TZ=Asia/Kolkata \
    DOWNLOAD_DIR=/downloads \
    DATA_DIR=/appdata

# Install system dependencies including curl and git
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    git \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend source files
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create persistent storage directories
RUN mkdir -p /downloads /appdata

EXPOSE 8070

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8070"]
