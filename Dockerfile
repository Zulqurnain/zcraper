FROM python:3.11-slim

# System deps required by Firefox and Chromium headless on Linux
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl git \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 \
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install firefox chromium --with-deps
RUN python manage.py migrate --no-input

EXPOSE 50051
CMD ["python", "run_server.py"]
