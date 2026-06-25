FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync_fasih.py .

CMD ["sh", "-c", "python3 -u sync_fasih.py; while true; do sleep 2400 && python3 -u sync_fasih.py; done"]
