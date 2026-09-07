#!/bin/bash
# Wrapper buat sync_anomali_custom.py di server: nunggu Xorg siap, ambil ulang
# XAUTHORITY-nya (berubah tiap Xorg restart, gak bisa di-hardcode), lalu
# jalanin python. Dipanggil systemd (lihat anomali-custom-sync.service) —
# sama pola persis dgn run_sync_usaha.sh.
set -e

cd "$(dirname "$0")"
source "$HOME/fasih-venv/bin/activate"

for i in $(seq 1 30); do
    XORG_PID=$(pgrep -x Xorg || true)
    [ -n "$XORG_PID" ] && break
    sleep 2
done
if [ -z "$XORG_PID" ]; then
    echo "[wrapper] Xorg gak pernah nyala, keluar." >&2
    exit 1
fi

XAUTH_FILE=$(ps -o args= -p "$XORG_PID" | grep -oP '(?<=-auth )\S+')
export DISPLAY=:0
export XAUTHORITY="$XAUTH_FILE"
echo "[wrapper] DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY"

set -a
source .env.usaha
set +a

exec python sync_anomali_custom.py
