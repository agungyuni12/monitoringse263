"""
run_sync_all.py — orkestrator: jalankan sync_fasih lalu sync_listing
berurutan dalam satu siklus, satu proses. Sengaja TIDAK dijalankan sebagai
dua service/loop terpisah — dua browser/session FASIH nyala bersamaan
(apalagi pakai akun berbeda secara bersamaan) terbukti bikin WAF lebih gampang
memblokir (lihat catatan di sync_fasih.py & riwayat debugging). Dengan
digabung begini, tiap siklus cuma ada SATU login FASIH aktif.

Jadwal ikut _next_run() milik sync_fasih.py (tiap 2 jam, skip 22:00-06:30
WITA) — sync_listing dilakukan sebagai langkah tambahan di siklus yang sama,
bukan jadwal sendiri.

Env vars: sama seperti sync_fasih.py & sync_listing.py (FASIH_USER, FASIH_PASS,
DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME, HEADLESS, CHROME_PATH).
"""

import time
from datetime import timedelta

import sync_fasih
import sync_listing


def run_once():
    sync_fasih.run_once()
    time.sleep(5)  # jeda kecil sebelum login FASIH lagi, biar gak langsung nempel
    sync_listing.run_once()


if __name__ == "__main__":
    while True:
        try:
            run_once()
            nxt = sync_fasih._next_run()
        except Exception as e:
            print(f"[ERROR] Sync gagal: {e}", flush=True)
            # Retry lebih cepat drpd nunggu siklus _next_run() penuh (bisa
            # ~2 jam) — beda dari sync_fasih.py sendirian yang punya resume
            # checkpoint per-halaman, di sini cukup coba lagi dalam 10 menit.
            nxt = sync_fasih._now_wita() + timedelta(minutes=10)

        secs = max(0, (nxt - sync_fasih._now_wita()).total_seconds())
        print(f"[SCHEDULER] Sync berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} ({int(secs // 60)} menit)", flush=True)
        time.sleep(secs)
