"""
Sync "Usaha Kategori A" (KBLI kategori A — Pertanian, Kehutanan, Perikanan)
dari Superset SQL Lab FASIH Dashboard → database se2026 (tabel
usaha_kategori_a — dipakai LANGSUNG oleh web UI monitoringse, lihat
handlers/usaha_kategori_a.go).

Sumbernya sama persis dengan scraper/sync_usaha.py (Superset SQL Lab di
fasih-dashboard.bps.go.id, BUKAN FASIH API biasa) — lihat docstring modul itu
kalau butuh detail alur login. Script ini dipisah sendiri (bukan digabung ke
sync_usaha.py) krn topiknya beda: bukan status keberadaan, tapi klasifikasi
KBLI + data ekonomi (pendapatan/pengeluaran) usaha.

Sumber data (ditelusuri manual lewat SQL Lab — lihat percakapan):
  - se2026_nested.kategori = 'A' → kategori KBLI 1-huruf (A/B/C/.../U). Dicek
    manual (GROUP BY kategori, kbli_label) kalau 'A' = Pertanian/Kehutanan/
    Perikanan, konsisten sama kbli_label yg ke-isi (mis. "[A][01111]Pertanian
    Jagung"). ~39.011 baris berkategori A, ~38.502 di antaranya (98,7%) sudah
    punya total_pendapatan/total_pengeluaran terisi — sisanya NULL (petugas
    belum sempat isi blok ekonomi), TETAP disimpan (ditampilkan "-" di UI)
    supaya usaha kategori A yg belum lengkap datanya juga kelihatan, bukan
    kehilangan.
  - total_pendapatan/total_pengeluaran: DOUBLE, satuan Rupiah per periode
    survei (bukan per bulan — ada juga total_pendapatan_bln/
    total_pengeluaran_bln terpisah tapi jarang terisi di sample manual,
    kemungkinan cuma kepake utk usaha yg lapor per bulan; keduanya tetap
    disimpan apa adanya).
  - kbli_label: label 5-digit KBLI lengkap dari FASIH (mis.
    "[A][01111]Pertanian Jagung"), NULL kalau usaha baru diklasifikasi
    kategori-nya doang (blm sampai ke KBLI 5 digit).
  - jenis_prelist (root_table, via JOIN assignment_id — sama pola dgn
    sync_usaha.py): bedain usaha bangunan mandiri (!= 'keluarga') vs usaha yg
    nempel roster keluarga ('keluarga'). nama_usaha & nama_kk DIPISAH jadi
    dua kolom sendiri2 (bukan digabung kayak tidak_ditemukan_usaha.nama) —
    nama_kk cuma keisi kalau jenis_prelist='keluarga' (biar jelas siapa
    kepala keluarga pemilik usaha itu, dari root_table.nama_kk/dtsen_nama_kk).

Pagination & anti-bot-wall: identik dgn sync_usaha.py (baca docstring modul
itu) — LIMIT/OFFSET langsung se-kabupaten, PAGE_SIZE dicek manual aman
sampai 5000 baris/eksekusi (lihat riwayat di sync_usaha.py, bukan 1000
seperti dokumentasi lama), ORDER BY assignment_id biar pagination stabil,
baca response /execute/ langsung tanpa reload biar gak kena WAF F5.

Env vars:
  FASIH_USER    (default: agung.yuniarta)
  FASIH_PASS    (default: kelayu1998)
  DB_HOST       (default: 127.0.0.1)
  DB_PORT       (default: 3306)
  DB_USER       (default: root)
  DB_PASS       (default: kelayu1998)
  DB_NAME       (default: se2026)
  SYNC_HOUR     (default: 23) — jam WITA sync jalan tiap hari (beda jam dari
                sync_usaha.py biar gak berebut sesi Superset bersamaan)
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

SYNC_HOUR = int(os.getenv("SYNC_HOUR", "23"))  # jam WITA, sekali sehari

DASH_URL = "https://fasih-dashboard.bps.go.id"

PAGE_SIZE = 5000      # dicek manual via raw SELECT (bukan COUNT) — lihat sync_usaha.py
PAGE_DELAY_MIN = 3    # jeda antar halaman (detik) — biar traffic gak seragam
PAGE_DELAY_MAX = 8

KATEGORI_A_COUNT_QUERY = "SELECT COUNT(*) AS n FROM se2026_nested WHERE kategori = 'A'"

KATEGORI_A_QUERY_TEMPLATE = """
SELECT n.assignment_id, n.index1, n.nama_usaha, n.kbli_label, n.skala_usaha,
       n.total_pendapatan, n.total_pendapatan_bln, n.total_pengeluaran, n.total_pengeluaran_bln,
       n.alamat_usaha, n.alamat_usaha_utama, n.level_6_full_code,
       n.assignment_status_alias, n.assignment_date_modified,
       r.jenis_prelist, r.nama_kk, r.dtsen_nama_kk, r.alamat_prelist, r.alamat_klrg
FROM se2026_nested n
INNER JOIN root_table r ON n.assignment_id = r.assignment_id
WHERE n.kategori = 'A'
ORDER BY n.assignment_id, n.index1
LIMIT {limit} OFFSET {offset}
""".strip()

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
WITA = timezone(timedelta(hours=8))

# Checkpoint hasil scrape, ditulis SEBELUM upsert ke DB — lihat sync_usaha.py
# soal alasannya (upsert gagal di tengah run besar mahal utk diulang scrape-nya).
CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".checkpoints")
CHECKPOINT_MAX_AGE = timedelta(hours=6)


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


def _checkpoint_path(label):
    return os.path.join(CHECKPOINT_DIR, f"{label}_rows.json")


def _save_checkpoint(label, rows):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    with open(_checkpoint_path(label), "w") as f:
        json.dump({"scraped_at": _now_wita().isoformat(), "rows": rows}, f)


def _load_checkpoint(label):
    path = _checkpoint_path(label)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    scraped_at = datetime.fromisoformat(data["scraped_at"])
    if _now_wita() - scraped_at > CHECKPOINT_MAX_AGE:
        os.remove(path)
        return None
    return data["rows"]


def _clear_checkpoint(label):
    path = _checkpoint_path(label)
    if os.path.exists(path):
        os.remove(path)


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
LOGIN_RETRY_DELAY = 15


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
    """Sama persis dgn sync_usaha.py — lihat docstring di sana."""
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
            try:
                page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=180_000)
            except Exception as reload_err:
                print(f"    [DEBUG] gagal refresh halaman: {reload_err}", flush=True)
    raise RuntimeError("Gagal ambil hasil query setelah semua retry")


def get_count(page, count_query):
    data = _run_query_and_fetch(page, count_query)
    return int(data[0]["n"]) if data else 0


def scrape_paginated(page, query_template, label, total_hint=None):
    all_rows = []
    offset = 0
    while True:
        sql = query_template.format(limit=PAGE_SIZE, offset=offset)
        rows = _run_query_and_fetch(page, sql)
        all_rows.extend(rows)
        hint = f"/{total_hint}" if total_hint is not None else ""
        print(f"  ({label}) offset {offset} → {len(rows)} baris (total {len(all_rows)}{hint})", flush=True)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        _human_pause(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
    return all_rows


# ── DB ───────────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def _ensure_column(conn, table, column, add_ddl):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (DB_NAME, table, column),
        )
        exists = cur.fetchone()["n"] > 0
        if not exists:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {add_ddl}")
            conn.commit()
            print(f"[DB] Kolom '{column}' ditambahkan ke {table} (auto-migrate).", flush=True)


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usaha_kategori_a (
              id                INT NOT NULL AUTO_INCREMENT,
              sls_id            INT NOT NULL,
              assignment_id     VARCHAR(64) NOT NULL,
              nama_usaha        VARCHAR(255) DEFAULT NULL,
              nama_kk           VARCHAR(255) DEFAULT NULL,
              jenis_prelist     VARCHAR(30) DEFAULT NULL,
              kbli_label        VARCHAR(255) DEFAULT NULL,
              skala_usaha       VARCHAR(50) DEFAULT NULL,
              pendapatan        DOUBLE DEFAULT NULL,
              pendapatan_bln    DOUBLE DEFAULT NULL,
              pengeluaran       DOUBLE DEFAULT NULL,
              pengeluaran_bln   DOUBLE DEFAULT NULL,
              alamat            VARCHAR(255) DEFAULT NULL,
              assignment_status VARCHAR(50) DEFAULT NULL,
              tanggal_modified  DATETIME DEFAULT NULL,
              imported_at       DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_uka_assignment (assignment_id),
              KEY idx_uka_sls (sls_id),
              CONSTRAINT fk_uka_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()

    # Auto-migrate buat DB yg tabelnya sudah kepalang dibuat sebelum kolom2 ini
    # ditambahkan (lihat sync_usaha.py soal alasan pola ini).
    _ensure_column(conn, "usaha_kategori_a", "nama_kk",
                    "nama_kk VARCHAR(255) DEFAULT NULL AFTER nama_usaha")
    _ensure_column(conn, "usaha_kategori_a", "jenis_prelist",
                    "jenis_prelist VARCHAR(30) DEFAULT NULL AFTER nama_kk")


def load_sls_map(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls")
        return {r["kode_sls"]: r["id"] for r in cur.fetchall()}


def upsert_kategori_a(conn, rows, sls_map, synced_at):
    skipped = 0
    with conn.cursor() as cur:
        for r in rows:
            sls_id = sls_map.get(r.get("level_6_full_code"))
            if sls_id is None:
                skipped += 1
                continue
            assignment_id = f"{r.get('assignment_id')}#{r.get('index1')}"
            cur.execute("""
                INSERT INTO usaha_kategori_a
                  (sls_id, assignment_id, nama_usaha, nama_kk, jenis_prelist, kbli_label, skala_usaha,
                   pendapatan, pendapatan_bln, pengeluaran, pengeluaran_bln,
                   alamat, assignment_status, tanggal_modified, imported_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  sls_id            = VALUES(sls_id),
                  nama_usaha        = VALUES(nama_usaha),
                  nama_kk           = VALUES(nama_kk),
                  jenis_prelist     = VALUES(jenis_prelist),
                  kbli_label        = VALUES(kbli_label),
                  skala_usaha       = VALUES(skala_usaha),
                  pendapatan        = VALUES(pendapatan),
                  pendapatan_bln    = VALUES(pendapatan_bln),
                  pengeluaran       = VALUES(pengeluaran),
                  pengeluaran_bln   = VALUES(pengeluaran_bln),
                  alamat            = VALUES(alamat),
                  assignment_status = VALUES(assignment_status),
                  tanggal_modified  = VALUES(tanggal_modified),
                  imported_at       = VALUES(imported_at)
            """, (
                sls_id, assignment_id, r.get("nama_usaha"),
                # nama_kk cuma relevan kalau usaha ini nempel roster keluarga
                # (jenis_prelist='keluarga') — biarin NULL kalau usaha bangunan
                # mandiri, jangan dipaksa isi dari field yg gak relevan.
                _first(r.get("nama_kk"), r.get("dtsen_nama_kk")) if r.get("jenis_prelist") == "keluarga" else None,
                r.get("jenis_prelist"), r.get("kbli_label"), r.get("skala_usaha"),
                r.get("total_pendapatan"), r.get("total_pendapatan_bln"),
                r.get("total_pengeluaran"), r.get("total_pengeluaran_bln"),
                # sama kayak sync_usaha.py: alamat_usaha* di se2026_nested sering
                # kosong, fallback ke alamat prelisting root_table.
                _first(r.get("alamat_usaha"), r.get("alamat_usaha_utama"),
                       r.get("alamat_prelist"), r.get("alamat_klrg")),
                r.get("assignment_status_alias"), r.get("assignment_date_modified"), synced_at,
            ))
    conn.commit()
    if skipped:
        print(f"[DB] usaha_kategori_a: {skipped} baris tanpa sls_id (kode_sls tidak ketemu di tabel sls)", flush=True)


def delete_stale(conn, table, synced_at):
    """Hapus baris yg gak ke-refresh di run ini — lihat sync_usaha.py soal
    alasannya (usaha yg statusnya berubah keluar dari kriteria, atau
    kategorinya sendiri berubah dari A ke kategori lain)."""
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE imported_at < %s", (synced_at,))
        deleted = cur.rowcount
    conn.commit()
    if deleted:
        print(f"[DB] {table}: {deleted} baris basi dihapus (sudah tidak masuk kriteria lagi).", flush=True)


def run_once():
    print("=" * 50, flush=True)
    print(f"SYNC USAHA KATEGORI A (FASIH Dashboard SQL Lab) → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print("=" * 50, flush=True)

    conn = _connect_db()
    ensure_tables(conn)
    sls_map = load_sls_map(conn)

    rows = _load_checkpoint("kategori_a")

    if rows is None:
        with sync_playwright() as pw:
            browser, ctx = _make_browser(pw)
            try:
                page = login(ctx)
                page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=180_000)
                _check_bot_wall(page.content(), "buka SQL Lab")

                print("\n[SCRAPE] Usaha kategori A...", flush=True)
                total = get_count(page, KATEGORI_A_COUNT_QUERY)
                print(f"[SCRAPE] Total baris (perkiraan): {total}", flush=True)
                rows = scrape_paginated(page, KATEGORI_A_QUERY_TEMPLATE, "kategori_a", total)
                _save_checkpoint("kategori_a", rows)
            finally:
                browser.close()
    else:
        print(f"\n[SCRAPE] Pakai checkpoint tersimpan ({len(rows)} baris) — skip scraping ulang.", flush=True)

    synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[DB] Upsert {len(rows)} baris usaha kategori A ke DB...", flush=True)
    upsert_kategori_a(conn, rows, sls_map, synced_at)
    delete_stale(conn, "usaha_kategori_a", synced_at)
    _clear_checkpoint("kategori_a")
    print(f"[DB] Selesai: {len(rows)} baris usaha kategori A di-sync.", flush=True)

    conn.close()
    print(f"\nSelesai!", flush=True)


def _next_run():
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
