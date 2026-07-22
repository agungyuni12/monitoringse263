"""
Sync "Usaha Tidak Ditemukan" & "Keluarga Tidak Ditemukan" dari Superset SQL Lab
FASIH Dashboard → database se2026 (tabel tidak_ditemukan_usaha /
tidak_ditemukan_keluarga — dipakai LANGSUNG oleh web UI monitoringse, lihat
handlers/tidak_ditemukan.go, bukan tabel arsip terpisah).

Sumbernya beda dari script sync_* lain di sini: ini bukan FASIH API biasa
(fasih-sm.bps.go.id) tapi Apache Superset SQL Lab di fasih-dashboard.bps.go.id
— jadi alur login (Keycloak SSO sama, tapi ada langkah dropdown "pilih jenis
login" + tombol "Go!" dulu sebelum sampai form SSO) dan cara ambil datanya
beda sendiri.

Sumber data (ditelusuri manual lewat SQL Lab sebelum nulis ini — lihat
percakapan, bukan asumsi):
  - "Usaha tidak ditemukan" AWALNYA dikira cukup dari root_table.ada_bang_usaha_value
    ('0' = usaha bangunan mandiri tidak ditemukan), tapi itu KELEWAT usaha yang
    nempel di roster keluarga (usaha pertanian dari ST2023, disimpan sbg array
    di root_table.nama_usaha_prelist) — dan keliru juga diasumsikan cuma
    relevan kalau KELUARGA-nya juga tidak ditemukan (ada_keluarga_value='0'),
    padahal keluarga bisa DITEMUKAN sementara usahanya belum (6.667 dari
    10.132 kasus justru begini).
    Solusi: tabel `se2026_nested` sudah berisi SEMUA usaha (bangunan mandiri
    MAUPUN roster keluarga) dalam bentuk ter-unnest satu baris per usaha,
    lengkap dengan status per-usaha sendiri di kolom keberadaan_usaha_value
    ('00' = Tidak Ditemukan) — jauh lebih presisi drpd nebak dari jumlah
    prelist vs ditemukan di level keluarga. JOIN ke root_table.jenis_prelist
    (via assignment_id) buat tahu itu usaha bangunan mandiri (jenis_prelist
    != 'keluarga') atau usaha dalam keluarga (jenis_prelist = 'keluarga') —
    disimpan sbg kolom jenis_prelist di tidak_ditemukan_usaha, TIDAK dipisah
    jadi query/tabel sendiri2, krn sumber & bentuk query-nya sama persis.
  - "Keluarga tidak ditemukan": root_table.ada_keluarga_value = '0'.

Superset di server ini membatasi hasil query ke MAKS 1000 baris per eksekusi,
independen dari nilai dropdown LIMIT di UI. Data diambil PER DESA
(level_4_full_code, dicek manual max 897/desa utk usaha & 531/desa utk
keluarga — selalu di bawah cap 1000) supaya gak perlu OFFSET sama sekali.
LIMIT/OFFSET bertahap sempat dicoba duluan tapi bikin dua masalah: (1) OFFSET
makin dalam makin lambat, (2) reload halaman buat ambil hasil (workaround
bot-wall) punya race condition — kalau di-reload sebelum server sempat
menyimpan tab state query yang baru, hasil yang muncul malah cache query
SEBELUMNYA (bukan error, jadi kelewat gak ketahuan salah).

WAF FASIH (F5, terlihat dari cookie "TS...") sempat membalas halaman "Bot
Detected" waktu baca response POST /execute/ langsung TANPA nunggu apa pun
dulu (dua query berturut-turut secepat mungkin). Setelah dicek manual: kalau
ditunggu dulu sampai teks "N rows returned" muncul di UI (tanda Superset-nya
sendiri sudah selesai proses response), baca body /execute/ langsung itu
konsisten aman — gak perlu reload sama sekali.

Env vars:
  FASIH_USER    (default: agung.yuniarta)
  FASIH_PASS    (default: kelayu1998)
  DB_HOST       (default: 127.0.0.1)
  DB_PORT       (default: 3306)
  DB_USER       (default: root)
  DB_PASS       (default: kelayu1998)
  DB_NAME       (default: se2026)
  SYNC_HOUR     (default: 22) — jam WITA sync jalan tiap hari
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

SYNC_HOUR = int(os.getenv("SYNC_HOUR", "22"))  # jam WITA, sekali sehari

DASH_URL = "https://fasih-dashboard.bps.go.id"

DESA_DELAY_MIN = 3   # jeda antar desa (detik) — biar traffic gak seragam
DESA_DELAY_MAX = 8

# ── Usaha tidak ditemukan (bangunan mandiri + roster keluarga, digabung) ────

USAHA_DESA_LIST_QUERY = (
    "SELECT level_4_full_code, COUNT(*) AS n FROM se2026_nested "
    "WHERE keberadaan_usaha_value = '00' "
    "GROUP BY level_4_full_code ORDER BY n DESC LIMIT 500"
)

USAHA_QUERY_TEMPLATE = """
SELECT n.assignment_id, n.index1, n.nama_usaha, n.skala_usaha,
       n.alamat_usaha, n.alamat_usaha_utama, n.level_6_full_code,
       n.assignment_status_alias, n.assignment_date_modified, r.jenis_prelist
FROM se2026_nested n
INNER JOIN root_table r ON n.assignment_id = r.assignment_id
WHERE n.keberadaan_usaha_value = '00'
  AND n.level_4_full_code = '{desa_code}'
LIMIT 1000
""".strip()

# ── Keluarga tidak ditemukan ─────────────────────────────────────────────────

KELUARGA_DESA_LIST_QUERY = (
    "SELECT level_4_full_code, COUNT(*) AS n FROM root_table "
    "WHERE ada_keluarga_value = '0' "
    "GROUP BY level_4_full_code ORDER BY n DESC LIMIT 500"
)

KELUARGA_QUERY_TEMPLATE = """
SELECT assignment_id, nama_kk, dtsen_nama_kk, alamat_klrg, alamat_prelist,
       level_6_full_code, assignment_status_alias, assignment_date_modified
FROM root_table
WHERE ada_keluarga_value = '0'
  AND level_4_full_code = '{desa_code}'
LIMIT 1000
""".strip()

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


def _first(*vals):
    for v in vals:
        v = (v or "").strip() if isinstance(v, str) else v
        if v:
            return v
    return None


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

    page.goto(f"{DASH_URL}/login/", wait_until="networkidle", timeout=180_000)
    _check_bot_wall(page.content(), "halaman login")

    # Dropdown "pilih jenis login" default-nya sudah "Pegawai BPS" — langsung Go!
    try:
        page.wait_for_selector("button:has-text('Go!')", timeout=180_000)
    except Exception:
        print(f"[LOGIN][DEBUG] url={page.url}", flush=True)
        print(f"[LOGIN][DEBUG] html snippet: {page.content()[:1500]}", flush=True)
        raise
    page.click("button:has-text('Go!')")
    page.wait_for_selector("#username", timeout=180_000)
    _human_pause(0.3, 0.8)
    _human_type(page.locator("#username"), FASIH_USER)
    _human_pause(0.2, 0.6)
    _human_type(page.locator("#password"), FASIH_PASS)
    _human_pause(0.3, 0.9)
    page.click("#kc-login")
    page.wait_for_url("**fasih-dashboard.bps.go.id**", timeout=180_000)
    _check_bot_wall(page.content(), "setelah login")
    print(f"[LOGIN] Berhasil → {page.url}", flush=True)
    return page


def _run_query_and_fetch(page, sql, retries=5):
    """Jalankan sql di SQL Lab, tunggu UI-nya sendiri selesai render ("N rows
    returned"), baru baca response POST /execute/ langsung — lihat docstring
    modul kenapa TIDAK pakai reload (race condition) atau baca body sebelum
    UI selesai (kena bot-wall)."""
    for attempt in range(1, retries + 1):
        try:
            page.wait_for_selector(".ace_content", timeout=180_000)
            page.wait_for_selector('button:has-text("Run")', timeout=180_000)
            page.locator(".ace_content").click()
            page.keyboard.press("ControlOrMeta+A")
            page.locator("textarea.ace_text-input").fill(sql)

            with page.expect_response(
                lambda r: "/api/v1/sqllab/execute/" in r.url, timeout=180_000
            ) as exec_resp_info:
                page.locator('button:has-text("Run")').click()
                page.wait_for_selector("text=rows returned", timeout=180_000)

            resp = exec_resp_info.value
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
            print(f"    [DEBUG] url={page.url}", flush=True)
            try:
                snippet = page.content()[:800].replace("\n", " ")
                print(f"    [DEBUG] html: {snippet}", flush=True)
            except Exception as dump_err:
                print(f"    [DEBUG] gagal ambil html: {dump_err}", flush=True)
            time.sleep(wait)
            # Halaman kadang nyangkut di state yang gak bisa pulih sendiri
            # (query "running" gak pernah kelar, dsb) — ngulang aksi yang
            # sama di halaman yang sama percuma kalau begitu (terbukti: 5x
            # retry bisa gagal identik berturut-turut). Refresh dulu sebelum
            # attempt berikutnya supaya mulai dari state bersih.
            try:
                page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=180_000)
            except Exception as reload_err:
                print(f"    [DEBUG] gagal refresh halaman: {reload_err}", flush=True)
    raise RuntimeError("Gagal ambil hasil query setelah semua retry")


def get_desa_codes(page, list_query):
    data = _run_query_and_fetch(page, list_query)
    return [(r["level_4_full_code"], int(r["n"])) for r in data if r.get("level_4_full_code")]


def scrape_per_desa(page, desa_list, query_template, label):
    all_rows = []
    for i, (desa_code, expected_n) in enumerate(desa_list, start=1):
        sql = query_template.format(desa_code=desa_code)
        rows = _run_query_and_fetch(page, sql)
        all_rows.extend(rows)
        flag = "" if len(rows) == expected_n else f"  [WARN] beda dari hitungan awal ({expected_n})"
        print(f"  ({label}) [{i}/{len(desa_list)}] desa {desa_code} → {len(rows)} baris (total {len(all_rows)}){flag}", flush=True)
        if i < len(desa_list):
            _human_pause(DESA_DELAY_MIN, DESA_DELAY_MAX)
    return all_rows


# ── DB ───────────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tidak_ditemukan_usaha (
              id                INT NOT NULL AUTO_INCREMENT,
              sls_id            INT NOT NULL,
              assignment_id     VARCHAR(64) NOT NULL,
              nama              VARCHAR(255) DEFAULT NULL,
              skala_usaha       VARCHAR(50) DEFAULT NULL,
              jenis_prelist     VARCHAR(30) DEFAULT NULL,
              alamat            VARCHAR(255) DEFAULT NULL,
              assignment_status VARCHAR(50) DEFAULT NULL,
              tanggal_modified  DATETIME DEFAULT NULL,
              imported_at       DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_tdu_assignment (assignment_id),
              KEY idx_tdu_sls (sls_id),
              CONSTRAINT fk_tdu_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tidak_ditemukan_keluarga (
              id                INT NOT NULL AUTO_INCREMENT,
              sls_id            INT NOT NULL,
              assignment_id     VARCHAR(64) NOT NULL,
              nama              VARCHAR(255) DEFAULT NULL,
              alamat            VARCHAR(255) DEFAULT NULL,
              assignment_status VARCHAR(50) DEFAULT NULL,
              tanggal_modified  DATETIME DEFAULT NULL,
              imported_at       DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_tdk_assignment (assignment_id),
              KEY idx_tdk_sls (sls_id),
              CONSTRAINT fk_tdk_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def load_sls_map(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls")
        return {r["kode_sls"]: r["id"] for r in cur.fetchall()}


def upsert_usaha(conn, rows, sls_map, synced_at):
    skipped = 0
    with conn.cursor() as cur:
        for r in rows:
            sls_id = sls_map.get(r.get("level_6_full_code"))
            if sls_id is None:
                skipped += 1
                continue
            assignment_id = f"{r.get('assignment_id')}#{r.get('index1')}"
            cur.execute("""
                INSERT INTO tidak_ditemukan_usaha
                  (sls_id, assignment_id, nama, skala_usaha, jenis_prelist,
                   alamat, assignment_status, tanggal_modified, imported_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  sls_id            = VALUES(sls_id),
                  nama              = VALUES(nama),
                  skala_usaha       = VALUES(skala_usaha),
                  jenis_prelist     = VALUES(jenis_prelist),
                  alamat            = VALUES(alamat),
                  assignment_status = VALUES(assignment_status),
                  tanggal_modified  = VALUES(tanggal_modified),
                  imported_at       = VALUES(imported_at)
            """, (
                sls_id, assignment_id, r.get("nama_usaha"), r.get("skala_usaha"),
                r.get("jenis_prelist"), _first(r.get("alamat_usaha"), r.get("alamat_usaha_utama")),
                r.get("assignment_status_alias"), r.get("assignment_date_modified"), synced_at,
            ))
    conn.commit()
    if skipped:
        print(f"[DB] usaha: {skipped} baris tanpa sls_id (kode_sls tidak ketemu di tabel sls)", flush=True)


def upsert_keluarga(conn, rows, sls_map, synced_at):
    skipped = 0
    with conn.cursor() as cur:
        for r in rows:
            sls_id = sls_map.get(r.get("level_6_full_code"))
            if sls_id is None:
                skipped += 1
                continue
            cur.execute("""
                INSERT INTO tidak_ditemukan_keluarga
                  (sls_id, assignment_id, nama, alamat, assignment_status, tanggal_modified, imported_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  sls_id            = VALUES(sls_id),
                  nama              = VALUES(nama),
                  alamat            = VALUES(alamat),
                  assignment_status = VALUES(assignment_status),
                  tanggal_modified  = VALUES(tanggal_modified),
                  imported_at       = VALUES(imported_at)
            """, (
                sls_id, r.get("assignment_id"), _first(r.get("nama_kk"), r.get("dtsen_nama_kk")),
                _first(r.get("alamat_klrg"), r.get("alamat_prelist")),
                r.get("assignment_status_alias"), r.get("assignment_date_modified"), synced_at,
            ))
    conn.commit()
    if skipped:
        print(f"[DB] keluarga: {skipped} baris tanpa sls_id (kode_sls tidak ketemu di tabel sls)", flush=True)


def run_once():
    print("=" * 50, flush=True)
    print(f"SYNC TIDAK DITEMUKAN (FASIH Dashboard SQL Lab) → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print("=" * 50, flush=True)

    conn = _connect_db()
    ensure_tables(conn)
    sls_map = load_sls_map(conn)

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        try:
            page = login(ctx)
            page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=180_000)
            _check_bot_wall(page.content(), "buka SQL Lab")

            # Fase 1: usaha tidak ditemukan (bangunan mandiri + roster keluarga)
            print("\n[FASE 1] Usaha tidak ditemukan...", flush=True)
            usaha_desa = get_desa_codes(page, USAHA_DESA_LIST_QUERY)
            print(f"[FASE 1] {len(usaha_desa)} desa, total baris (perkiraan): {sum(n for _, n in usaha_desa)}", flush=True)
            usaha_rows = scrape_per_desa(page, usaha_desa, USAHA_QUERY_TEMPLATE, "usaha")
            synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[FASE 1] Upsert {len(usaha_rows)} baris usaha ke DB...", flush=True)
            upsert_usaha(conn, usaha_rows, sls_map, synced_at)
            print(f"[FASE 1] Selesai: {len(usaha_rows)} baris usaha di-sync.", flush=True)

            # Fase 2: keluarga tidak ditemukan
            print("\n[FASE 2] Keluarga tidak ditemukan...", flush=True)
            keluarga_desa = get_desa_codes(page, KELUARGA_DESA_LIST_QUERY)
            print(f"[FASE 2] {len(keluarga_desa)} desa, total baris (perkiraan): {sum(n for _, n in keluarga_desa)}", flush=True)
            keluarga_rows = scrape_per_desa(page, keluarga_desa, KELUARGA_QUERY_TEMPLATE, "keluarga")
            synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[FASE 2] Upsert {len(keluarga_rows)} baris keluarga ke DB...", flush=True)
            upsert_keluarga(conn, keluarga_rows, sls_map, synced_at)
            print(f"[FASE 2] Selesai: {len(keluarga_rows)} baris keluarga di-sync.", flush=True)
        finally:
            browser.close()

    conn.close()
    print(f"\nSelesai semua fase!", flush=True)


def _next_run():
    # Sekali sehari jam SYNC_HOUR WITA — percobaan dini hari (mis. 03:00)
    # terbukti gagal terus (server FASIH kemungkinan maintenance/tidak
    # stabil jam segitu), jadi dijadwalkan tetap malam saja.
    now = _now_wita()
    nxt = now.replace(hour=SYNC_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt


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
