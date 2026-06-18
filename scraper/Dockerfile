FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync_fasih.py .

# Jalankan sekali lalu keluar (one-shot sync)
CMD ["python3", "-u", "sync_fasih.py"]
