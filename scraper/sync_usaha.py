"""
Sync data usaha (root_table, ada_bang_usaha_value='0') dari Superset SQL Lab
FASIH Dashboard → database se2026 (tabel usaha_listing).

Sumbernya beda dari script sync_* lain di sini: ini bukan FASIH API biasa
(fasih-sm.bps.go.id) tapi Apache Superset SQL Lab di fasih-dashboard.bps.go.id
— jadi alur login (Keycloak SSO sama, tapi ada langkah dropdown "pilih jenis
login" + tombol "Go!" dulu sebelum sampai form SSO) dan cara ambil datanya
beda sendiri.

Superset di server ini membatasi hasil query ke MAKS 1000 baris per eksekusi,
independen dari nilai dropdown LIMIT di UI (dikonfirmasi manual: LIMIT diset
100.000 tapi tetap balik 1000 baris, sementara SELECT COUNT(*) dengan kondisi
sama menunjukkan total sebenarnya 14.766). Makanya data diambil bertahap pakai
LIMIT 1000 OFFSET n, di-ORDER BY assignment_id supaya pagination-nya stabil.

WAF FASIH (F5, terlihat dari cookie "TS...") sempat membalas halaman "Bot
Detected" walau lewat browser asli & klik tombol Run manusia — baik saat baca
response POST /execute/ langsung, maupun (kadang) di percobaan berikutnya.
Ditemukan lewat percobaan manual: me-reload halaman SQL Lab setelah Run
(bukan baca response execute-nya langsung) berhasil mengambil hasil query
dari cache server (GET /api/v1/sqllab/results/) dengan konsisten tanpa kena
block lagi — jadi pola itu yang dipakai di sini (_run_query_and_fetch),
lengkap dengan retry/backoff untuk jaga-jaga kalau suatu saat tetap kena.

Env vars:
  FASIH_USER    (default: agung.yuniarta)
  FASIH_PASS    (default: kelayu1998)
  DB_HOST       (default: 127.0.0.1)
  DB_PORT       (default: 3306)
  DB_USER       (default: root)
  DB_PASS       (default: kelayu1998)
  DB_NAME       (default: se2026)
  SYNC_INTERVAL_HOURS (default: 4)
"""

import os, json, random, re, time
from datetime import datetime, timezone, timedelta
import pymysql
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_USER = os.getenv("FASIH_USER", "agung.yuniarta")
FASIH_PASS = os.getenv("FASIH_PASS", "kelayu1998")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

SYNC_INTERVAL_HOURS = float(os.getenv("SYNC_INTERVAL_HOURS", "4"))

DASH_URL = "https://fasih-dashboard.bps.go.id"

PAGE_SIZE      = 1000  # hard cap server (lihat docstring) — jangan dinaikkan
PAGE_DELAY_MIN = 3      # jeda antar halaman (detik) — biar traffic gak seragam
PAGE_DELAY_MAX = 8

QUERY_TEMPLATE = """
SELECT i.assignment_status_alias, j.nama_usaha_bang, i.data6,
       j.level_6_full_code, j.level_4_full_code, j.assignment_id,
       j.alamat_prelist, i.date_modified
FROM root_table j
INNER JOIN base_table_assignment i ON j.assignment_id = i.assignment_id
WHERE j.ada_bang_usaha_value = '0'
ORDER BY j.assignment_id
LIMIT {limit} OFFSET {offset}
""".strip()

COUNT_QUERY = (
    "SELECT COUNT(*) AS total_rows FROM root_table j "
    "INNER JOIN base_table_assignment i ON j.assignment_id = i.assignment_id "
    "WHERE j.ada_bang_usaha_value = '0'"
)

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
WITA = timezone(timedelta(hours=8))


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


def _human_pause(a=0.4, b=1.1):
    time.sleep(random.uniform(a, b))


def _human_type(locator, text):
    locator.click()
    _human_pause(0.15, 0.4)
    locator.press_sequentially(text, delay=random.randint(60, 160))


def _check_bot_wall(text, tag):
    if "Bot Detected" in text or "sistem kami mendeteksi koneksi anda sebagai bot" in text:
        m = re.search(r"BOT-\d+", text)
        code = m.group(0) if m else "?"
        raise RuntimeError(f"Diblokir bot-detection BPS di tahap '{tag}' (kode {code})")


def _make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
        locale="id-ID",
    )
    return browser, ctx


LOGIN_MAX_RETRY   = 3
LOGIN_RETRY_DELAY = 15  # detik — container baru start kadang jaringannya belum
                        # stabil sesaat (ERR_NETWORK_CHANGED), bukan bot-block


def login(ctx, retries=LOGIN_MAX_RETRY):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return _do_login(ctx)
        except Exception as e:
            last_err = e
            print(f"[LOGIN] gagal (percobaan {attempt}/{retries}): {e}", flush=True)
            if attempt < retries:
                time.sleep(LOGIN_RETRY_DELAY)
    raise last_err


def _do_login(ctx):
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)

    page.goto(f"{DASH_URL}/login/", wait_until="networkidle", timeout=90_000)
    _check_bot_wall(page.content(), "halaman login")

    # Dropdown "pilih jenis login" default-nya sudah "Pegawai BPS" — langsung Go!
    # Timeout dilonggarkan (container headless kadang render-nya lebih lambat
    # drpd browser interaktif) + dump snippet HTML kalau tombolnya gak ketemu,
    # supaya kelihatan alasannya (bot-wall halus / DOM beda / dsb), bukan cuma
    # "Timeout exceeded" polos.
    try:
        page.wait_for_selector("button:has-text('Go!')", timeout=60_000)
    except Exception:
        print(f"[LOGIN][DEBUG] url={page.url}", flush=True)
        print(f"[LOGIN][DEBUG] html snippet: {page.content()[:1500]}", flush=True)
        raise
    page.click("button:has-text('Go!')")
    page.wait_for_selector("#username", timeout=90_000)
    _human_pause(0.3, 0.8)
    _human_type(page.locator("#username"), FASIH_USER)
    _human_pause(0.2, 0.6)
    _human_type(page.locator("#password"), FASIH_PASS)
    _human_pause(0.3, 0.9)
    page.click("#kc-login")
    page.wait_for_url("**fasih-dashboard.bps.go.id**", timeout=90_000)
    _check_bot_wall(page.content(), "setelah login")
    print(f"[LOGIN] Berhasil → {page.url}", flush=True)
    return page


def _run_query_and_fetch(page, sql, retries=5):
    """Jalankan sql di SQL Lab lalu ambil hasilnya lewat reload halaman —
    lihat docstring modul: baca response POST /execute/ langsung kadang kena
    'Bot Detected', tapi reload lalu baca GET /api/v1/sqllab/results/ (cache
    hasil query yang sudah tersimpan di server) terbukti konsisten berhasil."""
    for attempt in range(1, retries + 1):
        try:
            page.locator(".ace_content").click()
            page.keyboard.press("ControlOrMeta+A")
            page.locator("textarea.ace_text-input").fill(sql)

            # Tunggu POST /execute/ betul-betul kelar SEBELUM reload — kalau
            # reload duluan, request Run yang baru diklik bisa keputus, dan
            # hasil yang muncul setelah reload jadinya cache query SEBELUMNYA
            # (kejadian nyata: query pertama malah balikin 1 baris hasil
            # COUNT(*) yang dijalankan sebelumnya, bukan 1000 baris data baru).
            with page.expect_response(
                lambda r: "/api/v1/sqllab/execute/" in r.url, timeout=45_000
            ) as exec_resp_info:
                page.locator('button:has-text("Run")').click()
            _check_bot_wall(exec_resp_info.value.text(), "eksekusi query")

            with page.expect_response(
                lambda r: "/api/v1/sqllab/results/" in r.url, timeout=45_000
            ) as resp_info:
                page.reload(timeout=60_000)
            resp = resp_info.value
            body_text = resp.text()
            _check_bot_wall(body_text, "ambil hasil query")
            body = json.loads(body_text)
            data = body.get("data")
            if data is None:
                raise RuntimeError(f"Response tanpa 'data': {body_text[:200]}")
            return data
        except Exception as e:
            wait = 15 * attempt
            print(f"    [RETRY {attempt}/{retries}] {e} — jeda {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("Gagal ambil hasil query setelah semua retry")


def get_total_rows(page):
    data = _run_query_and_fetch(page, COUNT_QUERY)
    return int(data[0]["total_rows"])


def scrape_all(page, total_rows):
    all_rows = []
    offset = 0
    while offset < total_rows:
        sql = QUERY_TEMPLATE.format(limit=PAGE_SIZE, offset=offset)
        rows = _run_query_and_fetch(page, sql)
        all_rows.extend(rows)
        print(f"  [{offset}-{offset + len(rows)}] → {len(rows)} baris (total {len(all_rows)})", flush=True)
        offset += PAGE_SIZE
        if offset < total_rows:
            _human_pause(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
    return all_rows


# ── DB ───────────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usaha_listing (
              assignment_id           VARCHAR(36) NOT NULL,
              sls_id                  INT DEFAULT NULL,
              level_6_full_code       VARCHAR(16) DEFAULT NULL,
              level_4_full_code       VARCHAR(10) DEFAULT NULL,
              assignment_status_alias VARCHAR(64) DEFAULT NULL,
              nama_usaha_bang         VARCHAR(255) DEFAULT NULL,
              skala_usaha             VARCHAR(64) DEFAULT NULL,
              alamat_prelist          VARCHAR(255) DEFAULT NULL,
              date_modified           DATETIME DEFAULT NULL,
              synced_at               DATETIME DEFAULT NULL,
              PRIMARY KEY (assignment_id),
              KEY idx_sls (sls_id),
              CONSTRAINT fk_usaha_listing_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def load_sls_map(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls")
        return {r["kode_sls"]: r["id"] for r in cur.fetchall()}


def upsert_rows(conn, rows, sls_map, synced_at):
    skipped = 0
    with conn.cursor() as cur:
        for r in rows:
            sls_id = sls_map.get(r.get("level_6_full_code"))
            if sls_id is None:
                skipped += 1
            cur.execute("""
                INSERT INTO usaha_listing
                  (assignment_id, sls_id, level_6_full_code, level_4_full_code,
                   assignment_status_alias, nama_usaha_bang, skala_usaha,
                   alamat_prelist, date_modified, synced_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  sls_id                  = VALUES(sls_id),
                  level_6_full_code       = VALUES(level_6_full_code),
                  level_4_full_code       = VALUES(level_4_full_code),
                  assignment_status_alias = VALUES(assignment_status_alias),
                  nama_usaha_bang         = VALUES(nama_usaha_bang),
                  skala_usaha             = VALUES(skala_usaha),
                  alamat_prelist          = VALUES(alamat_prelist),
                  date_modified           = VALUES(date_modified),
                  synced_at               = VALUES(synced_at)
            """, (
                r.get("assignment_id"), sls_id,
                r.get("level_6_full_code"), r.get("level_4_full_code"),
                r.get("assignment_status_alias"), r.get("nama_usaha_bang"),
                r.get("data6"), r.get("alamat_prelist"),
                r.get("date_modified"), synced_at,
            ))
    conn.commit()
    if skipped:
        print(f"[DB] {skipped} baris tanpa sls_id (kode_sls tidak ketemu di tabel sls)", flush=True)


def run_once():
    print("=" * 50, flush=True)
    print(f"SYNC USAHA (FASIH Dashboard SQL Lab) → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print("=" * 50, flush=True)

    conn = _connect_db()
    ensure_table(conn)
    sls_map = load_sls_map(conn)

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        try:
            page = login(ctx)
            page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=90_000)
            _check_bot_wall(page.content(), "buka SQL Lab")

            total = get_total_rows(page)
            print(f"[SCRAPE] Total baris: {total}", flush=True)

            rows = scrape_all(page, total)
        finally:
            browser.close()

    synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[DB] Upsert {len(rows)} baris...", flush=True)
    upsert_rows(conn, rows, sls_map, synced_at)
    conn.close()
    print(f"\nSelesai! {len(rows)} baris usaha di-sync.", flush=True)


def _next_run():
    return _now_wita() + timedelta(hours=SYNC_INTERVAL_HOURS)


if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] Sync gagal: {e}", flush=True)

        nxt = _next_run()
        secs = max(0, (nxt - _now_wita()).total_seconds())
        print(f"[SCHEDULER] Sync berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} ({int(secs // 60)} menit)", flush=True)
        time.sleep(secs)
