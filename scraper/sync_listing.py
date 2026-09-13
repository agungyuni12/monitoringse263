"""
Sync status listing & tarik sample FASIH → database se2026 (tabel listing_status)

Endpoint: POST /app/api/assignment-general/api/assignment-region/datatable?periodeId=...
Beda dengan sync_fasih.py (yang harus klik UI asli tab "Pencacah" krn WAF nolak
panggilan API langsung dari luar halaman), endpoint ini dipanggil lewat
page.evaluate(fetch(...)) — fetch yang dijalankan di dalam context halaman FASIH
yang sudah login (pola sama seperti sync_keberadaan_kilo.py) — dan itu sudah
cukup, gak perlu simulasi klik tombol apa pun. Satu request (length besar)
langsung balikin semua ~1661 baris (satu row per SLS/wilayah terkecil di Dompu,
plus beberapa row rollup kecamatan/kabupaten yang kode-nya gak match ke sls
manapun — otomatis kelewat pas JOIN ke tabel sls).

Field yang dipakai per row:
  smallestRegionFullCode → kode_sls (16 digit)
  doneListing            → sudah selesai listing (boolean)
  doneTarikSample        → sudah selesai tarik sample (boolean)

Env vars:
  FASIH_USER    (default: agung.yuniarta)
  FASIH_PASS    (default: kelayu1998)
  DB_HOST       (default: 127.0.0.1)
  DB_PORT       (default: 3306)
  DB_USER       (default: root)
  DB_PASS       (default: kelayu1998)
  DB_NAME       (default: se2026)
  HEADLESS      (default: false)
"""

import os, time, json
from datetime import datetime, timezone, timedelta
import pymysql
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_URL        = "https://fasih-sm.bps.go.id"
FASIH_USER       = os.getenv("FASIH_USER", "agung.yuniarta")
FASIH_PASS       = os.getenv("FASIH_PASS", "kelayu1998")
FASIH_PERIOD_ID  = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
DOMPU_REGION2_ID = "546a26bf-e388-41ab-9083-e02cbbc093d4"
HEADLESS         = os.getenv("HEADLESS", "false").lower() == "true"

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

WITA = timezone(timedelta(hours=8))


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listing_status (
              sls_id            INT NOT NULL,
              done_listing      TINYINT(1) NOT NULL DEFAULT 0,
              done_tarik_sample TINYINT(1) NOT NULL DEFAULT 0,
              synced_at         DATETIME DEFAULT NULL,
              PRIMARY KEY (sls_id),
              CONSTRAINT fk_listing_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def _make_browser(pw):
    return pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
    )


LOGIN_MAX_RETRY   = 3
LOGIN_RETRY_DELAY = 15


def _do_login(ctx):
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)
    try:
        page.goto(f"{FASIH_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=90_000)
    except Exception:
        pass
    time.sleep(3)
    page.wait_for_selector("input[name='username']", timeout=90_000)
    page.fill("input[name='username']", FASIH_USER)
    page.fill("input[name='password']", FASIH_PASS)
    page.click("#kc-login, input[type='submit']")
    for _ in range(30):
        time.sleep(2)
        u = page.url
        if "fasih-sm.bps.go.id" in u and "login" not in u and "oauth2" not in u:
            break
    time.sleep(3)
    print(f"[LOGIN] Berhasil → {page.url}", flush=True)
    return page


def login_fasih(browser):
    last_err = None
    for attempt in range(1, LOGIN_MAX_RETRY + 1):
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 720},
        )
        try:
            page = _do_login(ctx)
            return page, ctx
        except Exception as e:
            last_err = e
            try:
                ctx.close()
            except Exception:
                pass
            print(f"[LOGIN] gagal (percobaan {attempt}/{LOGIN_MAX_RETRY}): {e}", flush=True)
            if attempt < LOGIN_MAX_RETRY:
                print(f"  [jeda {LOGIN_RETRY_DELAY}s sebelum coba lagi]", flush=True)
                time.sleep(LOGIN_RETRY_DELAY)
    raise last_err


def fetch_region_datatable(page, ctx):
    xsrf = next((c["value"] for c in ctx.cookies() if c["name"] == "XSRF-TOKEN"), "")
    if not xsrf:
        raise RuntimeError("XSRF-TOKEN tidak ditemukan setelah login")

    url = (f"{FASIH_URL}/app/api/assignment-general/api/assignment-region/datatable"
           f"?periodeId={FASIH_PERIOD_ID}")
    payload = {
        "draw": 1,
        "start": 0,
        "length": 5000,  # buffer di atas total SLS Dompu (~1661) — satu request, gak perlu paginasi
        "search": {"value": "", "regex": False},
        "region2Id": DOMPU_REGION2_ID,
    }
    payload_json = json.dumps(payload)
    result = page.evaluate(f"""async () => {{
        try {{
            const r = await fetch('{url}', {{
                method: 'POST',
                credentials: 'include',
                headers: {{'Content-Type': 'application/json', 'X-XSRF-TOKEN': '{xsrf}'}},
                body: {json.dumps(payload_json)}
            }});
            if (!r.ok) return {{__error: 'HTTP ' + r.status}};
            return await r.json();
        }} catch (e) {{
            return {{__error: String(e)}};
        }}
    }}""")
    if not isinstance(result, dict) or "__error" in (result or {}):
        raise RuntimeError(f"Gagal ambil datatable: {result.get('__error') if isinstance(result, dict) else result}")
    return result.get("data") or []


def upsert_listing(conn, rows, synced_at):
    slsmap_rows = None
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls")
        slsmap_rows = {r["kode_sls"]: r["id"] for r in cur.fetchall()}

    n = 0
    with conn.cursor() as cur:
        for row in rows:
            kode = row.get("smallestRegionFullCode") or ""
            sls_id = slsmap_rows.get(kode)
            if not sls_id:
                continue  # row rollup kecamatan/kabupaten, bukan SLS individual
            cur.execute("""
                INSERT INTO listing_status (sls_id, done_listing, done_tarik_sample, synced_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  done_listing      = VALUES(done_listing),
                  done_tarik_sample = VALUES(done_tarik_sample),
                  synced_at         = VALUES(synced_at)
            """, (sls_id, 1 if row.get("doneListing") else 0,
                  1 if row.get("doneTarikSample") else 0, synced_at))
            n += 1
    conn.commit()
    return n


def run_once():
    print("=" * 50, flush=True)
    print(f"SYNC LISTING FASIH → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print("=" * 50, flush=True)

    synced_at = _now_wita()
    conn = _connect_db()
    ensure_table(conn)

    with sync_playwright() as pw:
        browser = _make_browser(pw)
        try:
            page, ctx = login_fasih(browser)
            rows = fetch_region_datatable(page, ctx)
            print(f"[FETCH] {len(rows)} baris diterima dari datatable", flush=True)
            n = upsert_listing(conn, rows, synced_at)
            done_listing = sum(1 for r in rows if r.get("doneListing"))
            done_tarik   = sum(1 for r in rows if r.get("doneTarikSample"))
            print(f"[UPLOAD] {n} SLS diupdate | doneListing={done_listing} doneTarikSample={done_tarik}", flush=True)
        finally:
            browser.close()

    conn.close()
    print(f"Selesai! [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]\n", flush=True)


_wita_tz = WITA


def _next_run():
    now = _now_wita()
    h, m = now.hour, now.minute
    if h >= 22 or h < 6 or (h == 6 and m < 30):
        nxt = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if h >= 22:
            nxt += timedelta(days=1)
        return nxt
    return now + timedelta(hours=2)


if __name__ == "__main__":
    while True:
        try:
            run_once()
            nxt = _next_run()
        except Exception as e:
            print(f"[ERROR] Sync gagal: {e}", flush=True)
            nxt = _now_wita() + timedelta(minutes=10)

        secs = max(0, (nxt - _now_wita()).total_seconds())
        print(f"[SCHEDULER] Sync berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} ({int(secs // 60)} menit)", flush=True)
        time.sleep(secs)
