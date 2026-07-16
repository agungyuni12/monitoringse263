"""
Sync KBLI + Coverage Usaha & Keluarga per SLS dari Dashboard SE2026
→ tabel kbli_usaha & coverage_usaha_keluarga

Dua dataset, satu login/jadwal (digabung supaya cukup sekali login & sekali
jadwal sync per hari, bukan dua container/proses terpisah):
  1. KBLI       : jumlah usaha per kategori KBLI (A, B, C, ...) per SLS.
  2. Coverage   : status cakupan usaha mandiri (BKU) & usaha dalam keluarga
                  (ditemukan/baru/tutup/ganda/tidak ditemukan), plus breakdown
                  lengkap status keluarga (prelist/ditemukan/meninggal/tidak
                  eligible/tidak dapat ditemui/tidak ditemukan/baru/menolak
                  didata/bersedia didata/keluarga khusus).

Endpoint: GET /api/agregat/fasih?level=sub_sls&indikator=<kode1,kode2,...>&kabupaten=<kode>
  Response: JSON array, setiap item berisi:
    id_wilayah     : 16-digit kode SLS (SAMA PERSIS dengan sls.kode_sls kita —
                     "sub_sls" di dashboard ini bukan level baru, granularitasnya
                     identik dengan tabel sls yang sudah ada)
    nama_wilayah, nama_provinsi, nama_kabupaten, nama_kecamatan, nama_desa, nama_sls
    is_agregat     : null atau 1 (1 = ada data teragregasi utk wilayah+indikator ini)
    kode_indikator : kode indikator (beda arti tergantung dataset — lihat KBLI_INDIKATOR
                     vs COVERAGE_INDIKATOR di bawah)
    nama_indikator : label lengkap, apa adanya dari dashboard — TIDAK di-hardcode
                     di sini supaya tidak salah tebak kalau BPS ubah urutan/isi.
    satuan         : "Usaha"
    total_value    : jumlah (null berarti 0 / belum ada data)
    updated_at     : timestamp UTC dari dashboard

Env vars:
  DASH_USER            login Dashboard SE2026 (default: nurfitriati)
  DASH_PASS            password Dashboard SE2026 (default: triemam95)
  KODE_KABUPATEN       kode kabupaten 4-digit (default: 5205)
  KBLI_INDIKATOR       daftar kode indikator KBLI, dipisah koma
  COVERAGE_INDIKATOR   daftar kode indikator coverage, dipisah koma
  DB_HOST / DB_PORT / DB_USER / DB_PASS / DB_NAME
  HEADLESS             jalankan Chrome headless (default: false)
"""

import os, time
import pymysql
from datetime import datetime
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

try:
    import zoneinfo
    _wita_tz = zoneinfo.ZoneInfo("Asia/Makassar")
except Exception:
    import datetime as _dt
    _wita_tz = _dt.timezone(_dt.timedelta(hours=8))


def _now_wita():
    return datetime.now(_wita_tz)


DASH_URL = "https://dashboard-se2026.apps.bps.go.id"
SSO_USER = os.getenv("DASH_USER",      "nurfitriati")
SSO_PASS = os.getenv("DASH_PASS",      "triemam95")
KODE_KAB = os.getenv("KODE_KABUPATEN", "5205")
HEADLESS  = os.getenv("HEADLESS",      "false").lower() == "true"

KBLI_INDIKATOR = os.getenv(
    "KBLI_INDIKATOR",
    "60,63,66,69,72,75,78,81,84,87,90,93,96,10254,99,162,164,10260",
)

# Dikurasi dari daftar awal (47 kode) — cuma yang benar-benar dipakai utk
# "coverage usaha & keluarga": status cakupan usaha mandiri (BKU) & usaha
# dalam keluarga (ditemukan/baru/tutup/ganda/tidak ditemukan), dan breakdown
# lengkap status keluarga (level keluarga, bukan anggota keluarga — kode
# 24-30/112 sengaja tidak diikutkan krn satuannya beda, per orang bukan per
# keluarga). Dibuang: progres % (CAWI/CAPI/geotagging), nonrespon per skala,
# blasting, SLS admin stat (assign/sync/total), target non-prelist, matched
# pendataan, dan breakdown jaringan usaha (tunggal/kantor pusat/cabang/dst).
COVERAGE_INDIKATOR = os.getenv(
    "COVERAGE_INDIKATOR",
    "2,10247,10264,10265,10266,10268,"     # Usaha (BKU/mandiri): prelist, tidak ditemukan, ditemukan, ditutup, ganda, baru
    "10691,10693,10694,10695,10696,"       # Usaha dalam Keluarga: ditemukan, tutup, ganda, tidak ditemukan, baru
    "14,15,16,17,18,19,20,21,22,59",       # Keluarga: prelist, ditemukan, meninggal, tidak eligible,
                                            # tidak dapat ditemui s/d akhir pendataan, tidak ditemukan,
                                            # baru, menolak didata, bersedia didata, keluarga khusus
)

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")


# ── Browser ──────────────────────────────────────────────────────────────────

def _make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--no-sandbox"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )
    return browser, ctx


def login_dashboard(ctx):
    """Login ke Dashboard SE2026 via SSO BPS Keycloak (sama seperti sync_anomali.py)."""
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)

    LONG = 90_000
    print("[LOGIN] Membuka SSO Dashboard...", flush=True)
    try:
        page.goto(f"{DASH_URL}/api/auth/sso", wait_until="commit", timeout=LONG)
    except Exception:
        pass
    time.sleep(3)

    page.wait_for_selector("input[name='username']", timeout=LONG)
    page.fill("input[name='username']", SSO_USER)
    page.fill("input[name='password']", SSO_PASS)
    page.click("#kc-login, input[type='submit'], button[type='submit']")

    for _ in range(30):
        time.sleep(2)
        if "dashboard-se2026" in page.url and "callback" not in page.url:
            break

    time.sleep(5)
    page.close()
    print("[LOGIN] Berhasil.", flush=True)


def warmup_session(ctx):
    """
    Hit /api/admin/config setelah login — sync_anomali.py selalu melakukan ini
    (via fetch_anomali_config) sebagai request pertama setelah login berhasil.
    Tanpa langkah ini, request ke /api/agregat/fasih bisa balas 401 meskipun
    login form sudah "Berhasil" (sesi/cookie belum sepenuhnya settle).
    """
    try:
        r = ctx.request.get(f"{DASH_URL}/api/admin/config", timeout=30_000)
        print(f"[WARMUP] GET /api/admin/config -> HTTP {r.status}", flush=True)
    except Exception as e:
        print(f"[WARMUP] Gagal: {e}", flush=True)


# ── Fetch agregat (dipakai bareng utk KBLI & Coverage) ───────────────────────

def fetch_agregat(ctx, kode_kab, indikator, label, retries=3):
    """Fetch agregat per sub_sls (satu request untuk semua kode indikator sekaligus)."""
    url = (
        f"{DASH_URL}/api/agregat/fasih"
        f"?level=sub_sls&indikator={indikator}&kabupaten={kode_kab}"
    )
    for attempt in range(1, retries + 1):
        try:
            r = ctx.request.get(url, timeout=120_000)
            if r.status != 200:
                body = ""
                try:
                    body = r.text()[:500]
                except Exception:
                    pass
                print(f"  [{label}] [WARN] HTTP {r.status} — {body}", flush=True)
                if r.status == 401 and attempt < retries:
                    print(f"  [{label}] [RETRY {attempt}/{retries}] Login ulang & warmup...", flush=True)
                    login_dashboard(ctx)
                    warmup_session(ctx)
                    time.sleep(3)
                    continue
                return []
            items = r.json()
            if not isinstance(items, list):
                print(f"  [{label}] [WARN] Response bukan list", flush=True)
                return []
            print(f"  [{label}] {len(items)} baris diterima.", flush=True)
            return items
        except Exception as e:
            print(f"  [{label}] [RETRY {attempt}/{retries}] {e}", flush=True)
            if attempt < retries:
                time.sleep(5 * attempt)
    return []


def fetch_subsls_termin1(ctx, kode_wilayah, retries=3):
    """
    Fetch target Termin 1 per sub_sls dari /api/mikro/subsls-termin-1 — ini
    sumber OTORITATIF utk "Prelist Awal" (sls.target_prelist_resmi), BUKAN
    coverage_usaha_keluarga (itu ngukur cakupan usaha/keluarga, konsep
    beda — sempat salah dipakai). Response: {"success":true,"data":[...]},
    tiap item punya "kode_wilayah" (== sls.kode_sls persis, granularitas
    sub_sls) dan "target".
    """
    url = f"{DASH_URL}/api/mikro/subsls-termin-1?kode_wilayah={kode_wilayah}"
    for attempt in range(1, retries + 1):
        try:
            r = ctx.request.get(url, timeout=120_000)
            if r.status != 200:
                body = ""
                try:
                    body = r.text()[:500]
                except Exception:
                    pass
                print(f"  [TERMIN1] [WARN] HTTP {r.status} — {body}", flush=True)
                if r.status == 401 and attempt < retries:
                    print(f"  [TERMIN1] [RETRY {attempt}/{retries}] Login ulang & warmup...", flush=True)
                    login_dashboard(ctx)
                    warmup_session(ctx)
                    time.sleep(3)
                    continue
                return []
            payload = r.json()
            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                print(f"  [TERMIN1] [WARN] Response tidak sesuai format", flush=True)
                return []
            print(f"  [TERMIN1] {len(items)} baris diterima.", flush=True)
            return items
        except Exception as e:
            print(f"  [TERMIN1] [RETRY {attempt}/{retries}] {e}", flush=True)
            if attempt < retries:
                time.sleep(5 * attempt)
    return []


# ── Database ─────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        ssl={"ssl": False},
    )


def load_sls_map(conn):
    cur = conn.cursor()
    cur.execute("SELECT kode_sls, id FROM sls")
    result = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    print(f"[DB] SLS map: {len(result)} entri", flush=True)
    return result


def upsert_agregat(conn, sls_map, items, table_name):
    """
    Upsert baris agregat ke tabel yang dituju (kbli_usaha / coverage_usaha_keluarga).
    Kedua tabel skemanya identik. UNIQUE KEY (sls_id, kode_indikator) — total_value
    NULL disimpan apa adanya (berarti 0 utk indikator itu di SLS itu).
    """
    cur = conn.cursor()
    SQL = f"""
        INSERT INTO {table_name}
          (sls_id, kode_indikator, nama_indikator, satuan, total_value, is_agregat, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          nama_indikator = VALUES(nama_indikator),
          satuan         = VALUES(satuan),
          total_value    = VALUES(total_value),
          is_agregat     = VALUES(is_agregat),
          synced_at      = VALUES(synced_at)
    """
    # Container ini jalan di UTC (Docker default) — datetime.now() polos akan
    # menyimpan jam yang salah 8 jam kalau langsung dipakai sbg synced_at
    # (kolom itu diasumsikan selalu WITA).
    now = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
    upserted = skipped = 0
    for item in items:
        kode16 = str(item.get("id_wilayah") or "").strip()
        sls_id = sls_map.get(kode16)
        if sls_id is None:
            skipped += 1
            continue
        kode_indikator = str(item.get("kode_indikator") or "").strip()
        if not kode_indikator:
            continue
        nama_indikator = str(item.get("nama_indikator") or "").strip()
        satuan = str(item.get("satuan") or "").strip()
        total_value = item.get("total_value")
        is_agregat = item.get("is_agregat")
        try:
            cur.execute(SQL, (sls_id, kode_indikator, nama_indikator, satuan, total_value, is_agregat, now))
            upserted += 1
        except Exception as e:
            print(f"    [DB ERROR] {e}", flush=True)
    conn.commit()
    cur.close()
    return upserted, skipped


def sync_target_prelist_resmi(conn, items):
    """
    Update sls.target_prelist_resmi dari data /api/mikro/subsls-termin-1
    (field "target" per kode_wilayah == kode_sls) — sumber LIVE & otoritatif,
    menggantikan snapshot statis Excel yang dipakai migration awal
    (db/target_prelist_resmi_migration.sql). SLS yang kode_sls-nya tidak ada
    di response TIDAK diubah (tetap nilai lama), bukan di-nol-kan.
    """
    seen = {}
    for item in items:
        kode = str(item.get("kode_wilayah") or "").strip()
        target = item.get("target")
        if not kode or target is None:
            continue
        seen[kode] = target  # dedupe kode_wilayah duplikat (nilainya sama)

    cur = conn.cursor()
    SQL = "UPDATE sls SET target_prelist_resmi = %s WHERE kode_sls = %s"
    updated = 0
    for kode, target in seen.items():
        try:
            cur.execute(SQL, (target, kode))
            updated += cur.rowcount
        except Exception as e:
            print(f"    [DB ERROR] {e}", flush=True)
    conn.commit()
    cur.close()
    return updated


# ── Main ─────────────────────────────────────────────────────────────────────

def run_once():
    print("=" * 55)
    print(f"SYNC KBLI + COVERAGE SE2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]")
    print(f"Kabupaten: {KODE_KAB}")
    print("=" * 55)

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        try:
            login_dashboard(ctx)
            warmup_session(ctx)

            conn = _connect_db()
            sls_map = load_sls_map(conn)

            print("\n[KBLI] Mengambil data agregat...", flush=True)
            kbli_items = fetch_agregat(ctx, KODE_KAB, KBLI_INDIKATOR, "KBLI")
            kbli_up, kbli_skip = upsert_agregat(conn, sls_map, kbli_items, "kbli_usaha")
            print(f"[KBLI] Selesai: {kbli_up} diupsert, {kbli_skip} dilewati.", flush=True)

            print("\n[COVERAGE] Mengambil data agregat...", flush=True)
            cov_items = fetch_agregat(ctx, KODE_KAB, COVERAGE_INDIKATOR, "COVERAGE")
            cov_up, cov_skip = upsert_agregat(conn, sls_map, cov_items, "coverage_usaha_keluarga")
            print(f"[COVERAGE] Selesai: {cov_up} diupsert, {cov_skip} dilewati.", flush=True)

            print("\n[TARGET PRELIST] Mengambil target Termin 1 per SLS...", flush=True)
            termin1_items = fetch_subsls_termin1(ctx, KODE_KAB)
            tp_updated = sync_target_prelist_resmi(conn, termin1_items)
            print(f"[TARGET PRELIST] Selesai: {tp_updated} SLS ter-update.", flush=True)

            conn.close()
            print(f"\nSelesai semua! KBLI={kbli_up} baris, Coverage={cov_up} baris.", flush=True)
        finally:
            browser.close()


def _next_run():
    """Jadwal sync: setiap 12 jam (2x sehari), sama seperti sync_anomali.py."""
    from datetime import timedelta
    return _now_wita() + timedelta(hours=12)


if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] Sync gagal: {e}", flush=True)

        nxt = _next_run()
        secs = max(0, (nxt - _now_wita()).total_seconds())
        print(
            f"[SCHEDULER] Sync berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} "
            f"({int(secs // 60)} menit)",
            flush=True,
        )
        time.sleep(secs)
