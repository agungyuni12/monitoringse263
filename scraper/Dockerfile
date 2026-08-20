FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync_fasih.py .

CMD ["xvfb-run", "-a", "python3", "-u", "sync_fasih.py"]
