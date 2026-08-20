FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync_fasih.py .

ENV DISPLAY=:99

# Start Xvfb langsung tanpa xvfb-run: xvfb-run macet total (puluhan menit)
# di dalam container ini, kemungkinan besar nunggu entropy buat generate
# xauth mcookie-nya. Display tetap (:99) + tanpa auth cookie (cukup buat
# Chrome & Xvfb yang jalan sebagai user sama di dalam satu container,
# gak ada X client dari luar) menghindari langkah yang macet itu sama sekali.
CMD Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp & \
    sleep 1 && \
    exec python3 -u sync_fasih.py
