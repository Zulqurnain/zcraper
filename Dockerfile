FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt
# Install Firefox (primary — bypasses Cloudflare) + Chromium as fallback
RUN python -m playwright install firefox chromium --with-deps

RUN python manage.py migrate --no-input

EXPOSE 50051
CMD ["python", "run_server.py"]
