"""
Sync Anomali Custom — jalanin tiap query SQL ad-hoc dari
"Daftar Anomali Masing-masing Tim.xlsx" (rule di luar 15 rule bawaan FASIH
128-135/136-144 yang disinkron sync_anomali.py) lewat Superset SQL Lab FASIH
Dashboard → tabel anomali_custom & anomali_custom_rule di database se2026.

Sumbernya SAMA PERSIS dengan scraper/sync_usaha.py: Apache Superset SQL Lab di
fasih-dashboard.bps.go.id (BUKAN FASIH API biasa fasih-sm.bps.go.id, dan BUKAN
API /api/mikro/anomali-case-kab yang dipakai sync_anomali.py) — fungsi login &
eksekusi query (_do_login, _set_sql_editor, _run_query_and_fetch) disalin apa
adanya dari sync_usaha.py, JANGAN diubah tanpa alasan kuat karena sudah
terbukti aman dari bot-detection WAF FASIH di produksi.

Daftar rule (no, jenis, deskripsi, query) dibaca dari
scraper/anomali_custom_rules.json — file itu di-generate SEKALI dari
"Daftar Anomali Masing-masing Tim.xlsx" sheet "Anomali Final", difilter cuma
baris yang kolom "5205" (kode Kab. Dompu)-nya "Sepakat" DAN kolom Query-nya
terisi (34 dari 65 baris per snapshot xlsx yang dipakai). Kalau ada rule baru
disepakati atau xlsx-nya di-update, re-generate file itu (lihat komentar di
kepala file JSON-nya) — script ini TIDAK baca xlsx langsung supaya tidak perlu
nambah dependency openpyxl/pandas ke venv produksi & tidak parsing ulang ~1000
baris tiap run.

Skema hasil query TIDAK seragam antar rule (masing-masing tim nulis SELECT
sendiri-sendiri) — dua gaya yang ketemu di 34 rule ini:
  - Gaya "kolom bersih": level_6_full_code, level_6_name, nama_principal/
    nama_usaha/nama_kk sebagai kolom terpisah.
  - Gaya "blob teks": semuanya digabung CONCAT() jadi beberapa kolom teks
    panjang (wilayah, sls, identitas, data, catatan) — kode SLS ada di
    dalam teks kolom "sls" sbg "idsubsls:<16 digit>", nama ada di dalam teks
    kolom "data" sbg "kk:<nama>".
Karena itu kode_wilayah & nama diekstrak pakai heuristik (_extract_kode_wilayah
/ _extract_nama), BUKAN nama kolom tetap — lihat komentarnya. assignment_id
SELALU ada sbg kolom sendiri di semua 34 rule (sudah dicek manual), jadi itu
satu-satunya asumsi keras yang dipegang script ini.

Setiap baris hasil query disimpan APA ADANYA sbg JSON (kolom data_json) supaya
kolom-kolom spesifik tiap rule tetap bisa dilihat di UI (lihat
handlers/anomali_custom.go) tanpa perlu tabel/kolom terpisah per rule.

row_hash = identitas baris dalam SATU rule, dipakai sbg dedup key pengganti PK
alami (skema kolom gak seragam, gak ada 1 kolom yang pasti unik di semua
rule). Prioritas: assignment_id + kolom index/no_usaha kalau ada (rule Usaha
bisa >1 baris per assignment_id, satu per usaha) — fallback ke hash SELURUH
baris kalau gak ada kolom pembeda sama sekali. first_detected_at (kapan row_hash
ini PERTAMA muncul) sengaja TIDAK disentuh di ON DUPLICATE KEY UPDATE, sama
persis pola tabel `anomali` (lihat sync_anomali.py) — row_hash yang berubah
krn field volatile (mis. assignment_status_alias) akan kebaca sbg row "baru"
(first_detected_at reset), itu trade-off yang diterima utk rule tanpa kolom
pembeda eksplisit.

Env vars:
  FASIH_USER, FASIH_PASS   login Superset SQL Lab FASIH Dashboard
  DB_HOST / DB_PORT / DB_USER / DB_PASS / DB_NAME
  HEADLESS                 jalankan Chrome headless (default: false)
  ROW_LIMIT                LIMIT dibubuhkan ke query yg belum punya LIMIT sendiri
                            (default 9000 — sama dgn PAGE_SIZE tervalidasi di
                            sync_usaha.py; hasil anomali per rule per kabupaten
                            realistisnya jauh di bawah ini)

Dijalankan sbg service jangka panjang (systemd, lihat anomali-custom-sync.service
dan run_sync_anomali_custom.sh) — loop sendiri, sync 1x sehari jam 02:30 WITA
(lihat SYNC_TIMES/_next_run()). Sengaja selang 1 jam dari slot fasih-sync/
sync-usaha (01:30) supaya gak rebutan sesi Superset SQL Lab akun agung.yuniarta
yang sama — dua script yg jalan bersamaan pakai akun itu terbukti bikin
keduanya gagal (race autosave/tab Superset, lihat komentar _set_sql_editor).
"""

import hashlib
import json
import os
import random
import re
import time
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

DASH_URL = "https://fasih-dashboard.bps.go.id"
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
WITA = timezone(timedelta(hours=8))
ROW_LIMIT = int(os.getenv("ROW_LIMIT", "9000"))
SYNC_TIMES = [(2, 30)]  # (jam, menit) WITA, 1x sehari — lihat _next_run()

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anomali_custom_rules.json")

# Kolom-kolom yg dicoba sbg "nama" secara berurutan, tergantung mana yg ada di
# hasil query rule tsb (gaya kolom bersih).
_NAMA_CANDIDATE_COLS = [
    "nama_usaha", "nama_principal", "nama_kk", "dtsen_nama_kk", "nama_dtsen", "nama",
]
# Kolom-kolom yg dicoba sbg kode SLS 16-digit (gaya kolom bersih).
_WILAYAH_CANDIDATE_COLS = ["level_6_full_code", "level_6_code", "kode_wilayah"]
# Kolom pembeda >1 baris per assignment_id yg sama (rule Usaha: bisa >1 usaha
# per assignment keluarga).
_DISCRIMINATOR_COLS = ["no_usaha", "index1", "index", "no_art"]


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


LOGIN_MAX_RETRY = 3
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


def _ace_full_value(page):
    """Ambil ISI PENUH editor Ace lewat API JS-nya (element.env.editor.getValue()),
    BUKAN .inner_text() — Ace me-render VIRTUAL (cuma baris yg keliatan di
    viewport yg ada di DOM), jadi utk query panjang (>1 layar, banyak rule
    anomali_custom_rules.json yg 50-80+ baris/2-3 SELECT bersarang) inner_text()
    cuma balikin potongan yg lagi ke-scroll, bikin validasi SELECT-count di
    bawah SELALU gagal walau isinya sebenarnya sudah benar (terbukti manual:
    query jalan sukses & "N rows returned" tampil, tapi validasi ini bilang
    'belum bersih' terus sampai 5x retry). None kalau API-nya gak ketemu
    (fallback ke inner_text di pemanggil)."""
    try:
        return page.evaluate("""
            () => {
                const el = document.querySelector('.ace_editor');
                if (el && el.env && el.env.editor) return el.env.editor.getValue();
                return null;
            }
        """)
    except Exception:
        return None


def _set_sql_editor(page, sql, attempts=3):
    expected_selects = sql.upper().count("SELECT")
    for attempt in range(1, attempts + 1):
        page.locator(".ace_content").click()
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.press("Delete")
        page.locator("textarea.ace_text-input").fill(sql)
        _human_pause(0.2, 0.4)
        current = _ace_full_value(page)
        if current is None:
            current = page.locator(".ace_content").inner_text()
        if current.upper().count("SELECT") == expected_selects:
            return
        print(f"    [WARN] Editor SQL Lab kemungkinan belum bersih (percobaan {attempt}/{attempts}) — ulang clear+fill...", flush=True)
    raise RuntimeError("Editor SQL Lab gak sinkron sama query yg diminta (race sama draft-autosave Superset)")


def _run_query_and_fetch(page, sql, retries=5):
    for attempt in range(1, retries + 1):
        try:
            page.wait_for_selector(".ace_content", timeout=180_000)
            page.wait_for_selector('button:has-text("Run")', timeout=180_000)
            _set_sql_editor(page, sql)

            with page.expect_response(
                lambda r: "/api/v1/sqllab/execute/" in r.url, timeout=180_000
            ) as exec_resp_info:
                page.locator('button:has-text("Run")').click()

            # SEBELUMNYA nunggu teks UI ("N rows returned" / "returned no
            # data") sebelum baca response — ternyata GAK RELIABLE: terbukti
            # manual (screenshot) hasilnya sudah tampil sempurna di layar
            # ("471 rows returned") tapi wait_for_selector tetap timeout,
            # entah krn teksnya kepecah di beberapa text node atau race
            # rendering React. Sumber kebenaran yang PASTI itu response
            # /api/v1/sqllab/execute/ sendiri (exec_resp_info.value di bawah,
            # nunggu network response beneran, bukan tebak-tebak DOM). Tetap
            # kasih jeda dikit dulu sebelum baca body — itu bagian yang
            # penting dari langkah "tunggu UI selesai" aslinya: biar gak
            # kebaca "buru-buru" sama WAF FASIH (lihat docstring sync_usaha.py
            # soal insiden "Bot Detected").
            resp = exec_resp_info.value
            time.sleep(2)
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


# ── Rules & ekstraksi ────────────────────────────────────────────────────────

def load_rules():
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _query_with_limit(sql):
    if re.search(r"\bLIMIT\b", sql, re.I):
        return sql
    return sql.rstrip().rstrip(";") + f"\nLIMIT {ROW_LIMIT}"


_IDSUBSLS_RE = re.compile(r"idsubsls:\s*(\d{6,20})")
_KODE16_RE = re.compile(r"\b(\d{16})\b")
_KK_BLOB_RE = re.compile(r"kk:\s*([^\n]*)")


def _extract_kode_wilayah(row):
    for col in _WILAYAH_CANDIDATE_COLS:
        v = row.get(col)
        if v:
            return re.sub(r"\D", "", str(v))[:20]
    # Gaya blob teks: kode SLS nyempil di dalam salah satu kolom teks
    # (biasanya kolom "sls") sbg "idsubsls:<16 digit>".
    for v in row.values():
        if isinstance(v, str):
            m = _IDSUBSLS_RE.search(v)
            if m:
                return m.group(1)
    for v in row.values():
        if isinstance(v, str):
            m = _KODE16_RE.search(v)
            if m:
                return m.group(1)
    return ""


def _extract_nama(row):
    for col in _NAMA_CANDIDATE_COLS:
        v = row.get(col)
        if v and str(v).strip():
            return str(v).strip()[:255]
    # Gaya blob teks: nama nyempil di dalam kolom "data" sbg "kk:<nama>".
    for v in row.values():
        if isinstance(v, str):
            m = _KK_BLOB_RE.search(v)
            if m and m.group(1).strip():
                return m.group(1).strip()[:255]
    return ""


def _row_hash(rule_no, row):
    assignment_id = str(row.get("assignment_id") or "").strip()
    parts = [str(rule_no), assignment_id]
    discriminated = False
    for col in _DISCRIMINATOR_COLS:
        if row.get(col) not in (None, ""):
            parts.append(f"{col}={row[col]}")
            discriminated = True
    if not discriminated:
        # Gak ada kolom pembeda eksplisit — fallback ke hash isi baris supaya
        # tetap unik kalau memang >1 baris beda per assignment_id yg sama,
        # dgn trade-off first_detected_at reset kalau isinya berubah (lihat
        # docstring modul).
        parts.append(json.dumps(row, sort_keys=True, default=str))
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


# ── DB ───────────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=15, read_timeout=60, write_timeout=60,
    )


def _ensure_table(cur, ddl, label):
    """CREATE TABLE IF NOT EXISTS harusnya no-op cepat kalau tabelnya sudah ada
    (migration db/anomali_custom_migration.sql sudah dijalankan manual duluan
    — lihat docstring modul), TAPI DDL yang punya FOREIGN KEY tetap butuh
    metadata lock ke tabel yang direferensikan (sls, atau anomali_custom_rule)
    — kalau ada koneksi lain yang nahan transaksi lama-lama di tabel itu
    (pernah kejadian nyata di produksi: proses scraper lain yang jalan
    lama/nge-hang), DDL ini bisa nge-hang nunggu lock TANPA batas waktu kalau
    dibiarkan. Set innodb_lock_wait_timeout pendek supaya gagal cepat & jelas
    alih2 nge-hang, lalu kalau memang gagal krn lock (bukan krn error skema
    beneran) anggap tabelnya sudah ada & lanjut jalan — bukan alasan buat
    stop total, cuma bikin auto-migrate kolom baru (kalau ada) telat kejalan
    sampai run berikutnya."""
    try:
        # lock_wait_timeout (BUKAN innodb_lock_wait_timeout, yang cuma bound
        # row-lock InnoDB) yang bound metadata-lock (MDL) buat statement DDL
        # kayak CREATE TABLE — default-nya 1 TAHUN kalau gak di-set.
        cur.execute("SET SESSION lock_wait_timeout = 8")
        cur.execute(ddl)
    except pymysql.err.OperationalError as e:
        # Cek pesan error, bukan cuma kode 1205 — dipakai baik utk row-lock
        # (innodb_lock_wait_timeout) maupun metadata-lock (lock_wait_timeout)
        # timeout, pesannya sama2 mengandung "Lock wait timeout".
        msg = str(e).lower()
        if "lock wait timeout" in msg:
            print(f"    [WARN] {label}: lock wait timeout — asumsi tabel sudah ada (lihat migration), lanjut.", flush=True)
        else:
            raise


def ensure_tables(conn):
    with conn.cursor() as cur:
        _ensure_table(cur, """
            CREATE TABLE IF NOT EXISTS anomali_custom_rule (
              rule_no          INT PRIMARY KEY,
              jenis            VARCHAR(20) NOT NULL DEFAULT '',
              deskripsi        VARCHAR(255) NOT NULL DEFAULT '',
              pengusul         VARCHAR(100) NOT NULL DEFAULT '',
              keterangan       TEXT,
              last_synced_at   DATETIME DEFAULT NULL,
              last_row_count   INT NOT NULL DEFAULT 0,
              last_error       TEXT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """, "anomali_custom_rule")
        _ensure_table(cur, """
            CREATE TABLE IF NOT EXISTS anomali_custom (
              id                INT AUTO_INCREMENT PRIMARY KEY,
              rule_no           INT NOT NULL,
              row_hash          CHAR(32) NOT NULL,
              assignment_id     CHAR(36) NOT NULL DEFAULT '',
              nama              VARCHAR(255) NOT NULL DEFAULT '',
              kode_wilayah      VARCHAR(30) NOT NULL DEFAULT '',
              sls_id            INT DEFAULT NULL,
              data_json         JSON,
              first_detected_at DATETIME DEFAULT NULL,
              synced_at         DATETIME DEFAULT NULL,
              UNIQUE KEY uk_rule_row (rule_no, row_hash),
              KEY idx_rule_no (rule_no),
              KEY idx_sls_id (sls_id),
              KEY idx_assignment (assignment_id),
              CONSTRAINT fk_anomali_custom_rule FOREIGN KEY (rule_no) REFERENCES anomali_custom_rule(rule_no)
              -- SENGAJA tidak FK ke sls(id) — lihat komentar di
              -- db/anomali_custom_migration.sql: sls dibaca terus-menerus
              -- oleh script sync lain, CREATE TABLE dgn FK ke situ gampang
              -- nyangkut lama nunggu metadata lock kosong (terbukti di
              -- produksi). sls_id tetap diisi dari lookup yang valid.
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """, "anomali_custom")
    conn.commit()


def load_sls_map(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls")
        return {r["kode_sls"]: r["id"] for r in cur.fetchall()}


def upsert_rule_meta(conn, rule, synced_at, row_count, error):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO anomali_custom_rule (rule_no, jenis, deskripsi, pengusul, keterangan, last_synced_at, last_row_count, last_error)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              jenis          = VALUES(jenis),
              deskripsi      = VALUES(deskripsi),
              pengusul       = VALUES(pengusul),
              keterangan     = VALUES(keterangan),
              last_synced_at = VALUES(last_synced_at),
              last_row_count = VALUES(last_row_count),
              last_error     = VALUES(last_error)
        """, (
            rule["rule_no"], rule["jenis"], rule["deskripsi"], rule["pengusul"], rule.get("keterangan") or "",
            synced_at, row_count, error,
        ))
    conn.commit()


def upsert_rows(conn, rule_no, rows, sls_map, synced_at):
    """Upsert semua baris hasil satu rule + hapus baris lama rule ini yg gak
    ke-refresh di run ini (row_hash-nya gak ada lagi di hasil query sekarang —
    berarti anomalinya sudah tidak terdeteksi lagi). Sama pola dgn tabel
    `anomali` di sync_anomali.py: hapus HANYA kalau fetch untuk rule ini
    berhasil (rows bukan exception), supaya gagal-fetch sesaat tidak
    menghapus data secara salah."""
    cur = conn.cursor()
    current_hashes = set()

    SQL = """
        INSERT INTO anomali_custom
          (rule_no, row_hash, assignment_id, nama, kode_wilayah, sls_id, data_json, first_detected_at, synced_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          nama         = VALUES(nama),
          kode_wilayah = VALUES(kode_wilayah),
          sls_id       = VALUES(sls_id),
          data_json    = VALUES(data_json),
          synced_at    = VALUES(synced_at)
    """

    for row in rows:
        assignment_id = str(row.get("assignment_id") or "").strip()
        if not assignment_id:
            continue
        row_hash = _row_hash(rule_no, row)
        current_hashes.add(row_hash)
        kode_wilayah = _extract_kode_wilayah(row)
        sls_id = sls_map.get(kode_wilayah)
        nama = _extract_nama(row)
        try:
            cur.execute(SQL, (
                rule_no, row_hash, assignment_id[:36], nama, kode_wilayah, sls_id,
                json.dumps(row, ensure_ascii=False, default=str),
                synced_at, synced_at,
            ))
        except Exception as e:
            print(f"      [DB ERROR] rule {rule_no}: {e}", flush=True)

    if current_hashes:
        placeholders = ",".join(["%s"] * len(current_hashes))
        cur.execute(
            f"DELETE FROM anomali_custom WHERE rule_no = %s AND row_hash NOT IN ({placeholders})",
            (rule_no, *current_hashes),
        )
    else:
        # Hasil query rule ini kosong (semua anomalinya sudah beres) — hapus semua baris lama rule ini.
        cur.execute("DELETE FROM anomali_custom WHERE rule_no = %s", (rule_no,))
    deleted = cur.rowcount
    conn.commit()
    if deleted:
        print(f"      [DB] rule {rule_no}: {deleted} baris basi dihapus.", flush=True)


# ── Main ─────────────────────────────────────────────────────────────────────

def run_once():
    print("=" * 50, flush=True)
    print(f"SYNC ANOMALI CUSTOM (FASIH Dashboard SQL Lab) → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print("=" * 50, flush=True)

    rules = load_rules()
    print(f"[RULES] {len(rules)} rule dimuat dari {RULES_PATH}", flush=True)

    print(f"[DB] Konek ke {DB_HOST}:{DB_PORT}/{DB_NAME}...", flush=True)
    conn = _connect_db()
    print("[DB] Konek OK. ensure_tables()...", flush=True)
    ensure_tables(conn)
    print("[DB] ensure_tables() OK. load_sls_map()...", flush=True)
    sls_map = load_sls_map(conn)
    print(f"[DB] {len(sls_map)} SLS dimuat.", flush=True)

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        try:
            page = login(ctx)
            page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=180_000)

            for i, rule in enumerate(rules, 1):
                rule_no = rule["rule_no"]
                synced_at = _now_wita()
                print(f"[{i}/{len(rules)}] Rule #{rule_no} ({rule['jenis']}): {rule['deskripsi'][:70]}", flush=True)
                try:
                    sql = _query_with_limit(rule["query"])
                    rows = _run_query_and_fetch(page, sql)
                    if len(rows) >= ROW_LIMIT:
                        print(f"    [WARN] rule {rule_no}: hasil = ROW_LIMIT ({ROW_LIMIT}) — kemungkinan terpotong.", flush=True)
                    # upsert_rule_meta DULU — anomali_custom.rule_no FK ke
                    # anomali_custom_rule(rule_no), jadi baris induknya harus
                    # ada dulu sebelum upsert_rows insert baris anak.
                    upsert_rule_meta(conn, rule, synced_at, len(rows), None)
                    upsert_rows(conn, rule_no, rows, sls_map, synced_at)
                    print(f"    → {len(rows)} baris", flush=True)
                except Exception as e:
                    print(f"    [ERROR] rule {rule_no} gagal: {e}", flush=True)
                    upsert_rule_meta(conn, rule, synced_at, 0, str(e)[:2000])
                    # Rule ini gagal fetch — JANGAN hapus baris lamanya (lihat
                    # docstring upsert_rows), lanjut ke rule berikutnya.
                    continue
                _human_pause(1.5, 3.5)
        finally:
            browser.close()
            conn.close()

    print("[DONE]", flush=True)


def _next_run():
    # 1x sehari jam 02:30 WITA (lihat SYNC_TIMES) — sengaja selang 1 jam dari
    # slot fasih-sync/sync-usaha (01:30) biar gak rebutan sesi Superset SQL
    # Lab akun agung.yuniarta yang sama (terbukti di produksi: dua script
    # yang jalan bersamaan bikin keduanya gagal — lihat komentar modul).
    # 34 rule di sini juga jauh lebih ringan drpd sync_usaha_ekonomi.py yg
    # scrape puluhan ribu baris, jadi gak perlu 4x/hari.
    now = _now_wita()
    candidates = [now.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in SYNC_TIMES]
    upcoming = [c for c in candidates if c > now]
    if upcoming:
        return min(upcoming)
    return min(c + timedelta(days=1) for c in candidates)


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
