#!/bin/bash
# ZCraper VPS Setup Script
# Run once on a fresh Linux VPS (Ubuntu/Debian) to install all dependencies
# Usage: bash setup_vps.sh

set -e
echo "=== ZCraper VPS Setup ==="

# 1. System packages
echo "[1/5] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    wget curl git \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 \
    fonts-liberation fonts-noto-color-emoji \
    xvfb

# 2. Python venv
echo "[2/5] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# 3. Python dependencies
echo "[3/5] Installing Python packages..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 4. Playwright browsers
echo "[4/5] Installing Playwright browsers (Firefox + Chromium)..."
playwright install firefox chromium --with-deps

# 5. Django setup
echo "[5/5] Running Django migrations..."
cp .env.example .env
# Generate a random secret key
SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
sed -i "s/change-me-in-production/$SECRET/" .env
python manage.py migrate --no-input

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Start the gRPC server:"
echo "  source venv/bin/activate && python run_server.py"
echo ""
echo "Test it:"
echo "  python -m client.client https://www.propertyguru.com.my/property-listing/skyline-kl-for-rent-by-edward-tan-501266704"
