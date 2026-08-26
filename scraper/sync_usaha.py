"""
Sync Usaha & Keluarga (SEMUA status, bukan cuma yg bermasalah — lihat
docstring "Sumber data" di bawah) dari Superset SQL Lab FASIH Dashboard →
database se2026 (tabel tidak_ditemukan_usaha / tidak_ditemukan_keluarga —
nama tabel peninggalan scope lama sebelum diperluas ke semua status, dipakai
LANGSUNG oleh web UI monitoringse, lihat handlers/tidak_ditemukan.go, bukan
tabel arsip terpisah).

Sumbernya beda dari script sync_* lain di sini: ini bukan FASIH API biasa
(fasih-sm.bps.go.id) tapi Apache Superset SQL Lab di fasih-dashboard.bps.go.id
— jadi alur login (Keycloak SSO sama, tapi ada langkah dropdown "pilih jenis
login" + tombol "Go!" dulu sebelum sampai form SSO) dan cara ambil datanya
beda sendiri.

Sumber data (ditelusuri manual lewat SQL Lab sebelum nulis ini — lihat
percakapan, bukan asumsi):
  - "Usaha" AWALNYA dikira cukup dari root_table.ada_bang_usaha_value
    ('0' = usaha bangunan mandiri tidak ditemukan), tapi itu KELEWAT usaha yang
    nempel di roster keluarga (usaha pertanian dari ST2023, disimpan sbg array
    di root_table.nama_usaha_prelist) — dan keliru juga diasumsikan cuma
    relevan kalau KELUARGA-nya juga tidak ditemukan (ada_keluarga_value='0'),
    padahal keluarga bisa DITEMUKAN sementara usahanya belum (6.667 dari
    10.132 kasus justru begini).
    Solusi: tabel `se2026_nested` sudah berisi SEMUA usaha (bangunan mandiri
    MAUPUN roster keluarga) dalam bentuk ter-unnest satu baris per usaha,
    lengkap dengan status per-usaha sendiri di kolom keberadaan_usaha_value —
    jauh lebih presisi drpd nebak dari jumlah prelist vs ditemukan di level
    keluarga. Scope-nya SEMUA status (TIDAK difilter status tertentu lagi) —
    dicek manual lewat SQL Lab (GROUP BY keberadaan_usaha_value): '1'=Ditemukan,
    '2'=Baru, '00'=Tidak Ditemukan, '3'=Tutup, '4'=Ganda, '9'=Non Respon.
    Awalnya cuma diambil '00'/'3'/'4' (Tidak Ditemukan/Tutup/Ganda), yg
    sebelumnya cuma keliatan sbg ANGKA agregat di tab "Rekap Keberadaan"
    (coverage_usaha_keluarga, lihat sync_kbli.py) tanpa detail nama/alamat per
    usaha — tabel ini isi kekosongan itu, disimpan di kolom keberadaan_usaha
    (label bersih, prefix angka "N. " dibuang). Sekarang SEMUA status diambil
    supaya tabel ini juga bisa jadi rujukan lengkap per-usaha, bukan cuma yg
    bermasalah.
    JOIN ke root_table.jenis_prelist (via assignment_id) buat tahu itu usaha
    bangunan mandiri (jenis_prelist != 'keluarga') atau usaha dalam keluarga
    (jenis_prelist = 'keluarga') — disimpan sbg kolom jenis_prelist di
    tidak_ditemukan_usaha, TIDAK dipisah jadi query/tabel sendiri2, krn
    sumber & bentuk query-nya sama persis.
  - "KBLI kategori prelist" (kbli_kategori_prelist): SEMUA varian KBLI hasil
    verifikasi 2026 (kbli_prelist/kbli_label/kbli_akhir/kbli_genai_*/kategori)
    0% keisi di scope usaha ini (baru keisi kalau usahanya sempat dikunjungi &
    diklasifikasi — usaha yg Tidak Ditemukan/Tutup/Ganda/Open ya jelas belum
    sempat). Yang KEISI (84%): se2026_nested.kategori_2025 — kategori 1-huruf
    dari klasifikasi TAHUN LALU (ST2023/prelist carry-over), gak bergantung
    progres kunjungan 2026. Ini yg dipakai, TIDAK ada versi detail 5-digit-nya
    (cuma field ini yg ada, dicek "_2025" di seluruh skema se2026_nested).
  - "Keluarga": root_table, SEMUA status ada_keluarga_value (TIDAK difilter
    lagi) — dicek via GROUP BY ada_keluarga_value: Ditemukan/Tidak Ditemukan/
    Baru/Meninggal/Tidak Eligible/Tidak Dapat Ditemui/Keluarga Khusus. TIDAK
    ada status Tutup/Ganda utk keluarga (itu cuma ada di usaha).
  - "Open" (usaha & keluarga yg assignment-nya belum pernah disentuh SAMA
    SEKALI): sumbernya beda lagi, base_table_assignment (bukan se2026_nested/
    root_table, yg TIDAK punya baris utk assignment yg belum ada progres apa
    pun) — lihat komentar di deket OPEN_USAHA_QUERY_TEMPLATE. Statusnya
    disimpan di kolom yg sama (keberadaan_usaha utk usaha, keberadaan_keluarga
    utk keluarga) sbg nilai "Open", digabung ke tabel yg sama jg.

Data diambil PAGINATED langsung se-kabupaten (bukan per desa lagi — lihat
riwayat sebelumnya di git log kalau butuh alasan kenapa awalnya per desa):
LIMIT {PAGE_SIZE} OFFSET bertahap, dengan ORDER BY assignment_id supaya
pagination-nya STABIL (tanpa ORDER BY eksplisit, urutan antar eksekusi query
tidak terjamin sama — bisa ada baris kelompok/ke-duplikat pas di-OFFSET).
(Sebelumnya didokumentasikan Superset di server ini membatasi hasil MAKS 1000
baris/eksekusi independen dari nilai LIMIT — dicek ULANG manual pakai raw
SELECT tanpa agregat (bukan cuma COUNT(*), yang SELALU balik 1 baris apapun
LIMIT-nya jadi gak valid buat tes ini) dan ternyata LIMIT 5000 memang balik
5000 baris utuh. Entah cap lamanya sudah dicabut atau spesifik ke pola query
lama — yang pasti PAGE_SIZE 5000 terbukti aman, dan belakangan dicek ulang
PAGE_SIZE 9000 juga balik 9000 baris utuh, jadi dipakai sekarang.)
Percobaan OFFSET bertahap versi awal (sebelum rewrite ini) sempat gagal krn
implementasi lamanya baca hasil lewat RELOAD halaman (race condition: reload
sebelum server nyimpen tab state query baru bisa balikin cache query
SEBELUMNYA). Versi sekarang baca response /execute/ langsung tanpa reload
(lihat _run_query_and_fetch), jadi masalah itu sudah tidak relevan lagi —
OFFSET makin dalam tetap bisa makin lambat (karakteristik DB, bukan bug),
tapi tidak akan salah data.

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
  Sync jalan 4x sehari, jam 01:30, 07:30, 13:30, 19:30 WITA (lihat SYNC_TIMES).
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

SYNC_TIMES = [(1, 30), (7, 30), (13, 30), (19, 30)]  # (jam, menit) WITA, 4x sehari

DASH_URL = "https://fasih-dashboard.bps.go.id"

PAGE_SIZE = 9000      # dicek manual via raw SELECT (bukan COUNT) — lihat docstring modul
PAGE_DELAY_MIN = 3    # jeda antar halaman (detik) — biar traffic gak seragam
PAGE_DELAY_MAX = 8

# ── Usaha: SEMUA status keberadaan_usaha_value ──────────────────────────────
# (bangunan mandiri + usaha yg nempel roster keluarga, digabung — lihat
# docstring modul soal kenapa jenis_prelist dipakai buat bedain, bukan tabel
# terpisah). Sebelumnya cuma diambil '00'/'3'/'4' (Tidak Ditemukan/Tutup/
# Ganda) — sekarang TANPA filter status sama sekali, jadi ikut juga
# '1'/'2'/'9' (Ditemukan/Baru/Non Respon).

USAHA_COUNT_QUERY = "SELECT COUNT(*) AS n FROM se2026_nested"

USAHA_QUERY_TEMPLATE = """
SELECT n.assignment_id, n.index1, n.nama_usaha, n.skala_usaha,
       n.alamat_usaha, n.alamat_usaha_utama, n.level_6_full_code,
       n.assignment_status_alias, n.assignment_date_modified, r.jenis_prelist,
       r.alamat_prelist, r.alamat_klrg, n.keberadaan_usaha_label, n.kategori_2025,
       n.hp, n.email
FROM se2026_nested n
INNER JOIN root_table r ON n.assignment_id = r.assignment_id
ORDER BY n.assignment_id, n.index1
LIMIT {limit} OFFSET {offset}
""".strip()

# ── Keluarga: SEMUA status ada_keluarga_value ───────────────────────────────
# Sebelumnya cuma diambil '0'/'1'/'2' (Tidak Ditemukan/Ditemukan/Baru) — sekarang
# TANPA filter status sama sekali, jadi ikut juga Meninggal/Tidak Eligible/
# Tidak Dapat Ditemui/Keluarga Khusus.
# root_table.no_kk vs dtsen_no_kk: dicek manual (GROUP BY ada_keluarga_value)
# — no_kk SELALU ada dari awal (termasuk 100% di kasus Tidak Ditemukan, jadi
# ini nomor KK PRELIST), dtsen_no_kk baru keisi kalau ada proses pemutakhiran/
# verifikasi DTSEN (0% di Tidak Ditemukan, ~100% di Ditemukan/Baru — nomor KK
# SEKARANG/hasil pemutakhiran). Keduanya identik kalau dua2nya ada (0 baris
# beda), konsisten sama teori ini: pemutakhiran cuma "mengkonfirmasi ulang"
# nomor yg sama, bukan ganti nomor baru.

KELUARGA_COUNT_QUERY = "SELECT COUNT(*) AS n FROM root_table"

KELUARGA_QUERY_TEMPLATE = """
SELECT assignment_id, nama_kk, dtsen_nama_kk, alamat_klrg, alamat_prelist,
       level_6_full_code, assignment_status_alias, assignment_date_modified,
       ada_keluarga_label, no_kk, dtsen_no_kk
FROM root_table
ORDER BY assignment_id
LIMIT {limit} OFFSET {offset}
""".strip()

# ── "Open" (assignment belum pernah disentuh sama sekali) ───────────────────
# BEDA SUMBER dari dua di atas: se2026_nested/root_table cuma berisi baris yg
# SUDAH ada progres (minimal submit sekali) — assignment yg beneran belum
# disentuh (status OPEN) TIDAK muncul di situ sama sekali (dicek manual: 0
# baris WHERE assignment_status_alias IS NULL di kedua tabel itu). Sumber yg
# benar: base_table_assignment (roster mentah SEMUA assignment, termasuk yg
# belum jalan). Tabel ini gak punya nama_usaha/kbli/jenis_prelist — cuma
# data1 (nama, apa adanya dari prelist) & data2 (alamat prelist), krn belum
# ada kunjungan lapangan. code_identity formatnya
# "{kode_sls} - {TIPE} - {no}" — dicek manual (GROUP BY tipe di baris OPEN):
# DTSEN = keluarga, UMK/UM/UB = usaha (3 skala usaha, gabung sbg satu scope
# "usaha open" spt sync_kbli.py gabungin usaha BKU per skala). "DUMMY" (64
# baris) sengaja DIBUANG — bukan assignment usaha/keluarga asli.
# jenis_prelist dibiarkan NULL (dianggap bangunan mandiri): usaha yg nempel
# roster KELUARGA baru "ada" sbg assignment terpisah SETELAH keluarganya
# dikunjungi, jadi gak mungkin usaha-dalam-keluarga berstatus OPEN sendirian.

OPEN_USAHA_TIPE = "'UMK','UM','UB'"

OPEN_USAHA_COUNT_QUERY = (
    "SELECT COUNT(*) AS n FROM base_table_assignment "
    f"WHERE assignment_status_alias='OPEN' AND SUBSTRING_INDEX(SUBSTRING_INDEX(code_identity,' - ',2),' - ',-1) IN ({OPEN_USAHA_TIPE})"
)

OPEN_USAHA_QUERY_TEMPLATE = ("""
SELECT assignment_id, data1 AS nama_usaha, data2 AS alamat_usaha,
       level_6_full_code, assignment_status_alias, assignment_date_modified
FROM base_table_assignment
WHERE assignment_status_alias='OPEN' AND SUBSTRING_INDEX(SUBSTRING_INDEX(code_identity,' - ',2),' - ',-1) IN (""" + OPEN_USAHA_TIPE + """)
ORDER BY assignment_id
LIMIT {limit} OFFSET {offset}
""").strip()

OPEN_KELUARGA_COUNT_QUERY = (
    "SELECT COUNT(*) AS n FROM base_table_assignment "
    "WHERE assignment_status_alias='OPEN' AND SUBSTRING_INDEX(SUBSTRING_INDEX(code_identity,' - ',2),' - ',-1) = 'DTSEN'"
)

OPEN_KELUARGA_QUERY_TEMPLATE = """
SELECT assignment_id, data1 AS nama_kk, data2 AS alamat_klrg,
       level_6_full_code, assignment_status_alias, assignment_date_modified
FROM base_table_assignment
WHERE assignment_status_alias='OPEN' AND SUBSTRING_INDEX(SUBSTRING_INDEX(code_identity,' - ',2),' - ',-1) = 'DTSEN'
ORDER BY assignment_id
LIMIT {limit} OFFSET {offset}
""".strip()

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
WITA = timezone(timedelta(hours=8))

# Checkpoint hasil scrape per fase, ditulis SEBELUM upsert ke DB. Kalau upsert
# gagal (mis. skema tabel belum di-migrate, DB down sesaat), run berikutnya
# baca dari sini dulu alih2 login+scrape ulang dari nol — scrape per desa
# lambat (jeda manusiawi tiap desa) dan tiap request nambah risiko kena
# bot-detection WAF, jadi sayang diulang kalau cuma DB-nya yang bermasalah.
CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".checkpoints")
CHECKPOINT_MAX_AGE = timedelta(hours=6)  # lebih tua dari ini dianggap basi (bukan buat lintas hari)


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


def _clean_label(label):
    """FASIH nyimpen label kayak '3. Tutup' atau '0. Tidak Ditemukan (STOP)' —
    buang prefix angka+titik DAN suffix "(STOP)" (root_table.ada_keluarga_label
    khusus utk status 0 pakai suffix ini, gak konsisten sama label lain)."""
    if not label:
        return None
    cleaned = re.sub(r"^\d+\.\s*", "", label)
    cleaned = re.sub(r"\s*\(STOP\)\s*$", "", cleaned)
    return cleaned.strip() or None


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


def get_count(page, count_query):
    data = _run_query_and_fetch(page, count_query)
    return int(data[0]["n"]) if data else 0


def scrape_paginated(page, query_template, label, total_hint=None):
    """Tarik semua baris se-kabupaten pakai LIMIT/OFFSET bertahap (PAGE_SIZE per
    eksekusi). ORDER BY di query_template WAJIB ada supaya urutan antar
    eksekusi stabil — lihat docstring modul."""
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
    """Tambah kolom yg belum ada lewat ALTER TABLE — dicek dulu via
    information_schema (bukan cuma andalin CREATE TABLE IF NOT EXISTS, yang
    TIDAK nge-alter tabel lama yg sudah ada duluan dgn skema beda — kejadian
    nyata: kolom jenis_prelist ketinggalan di DB lama, upsert gagal di
    tengah sync besar yg mahal diulang)."""
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
            CREATE TABLE IF NOT EXISTS tidak_ditemukan_usaha (
              id                    INT NOT NULL AUTO_INCREMENT,
              sls_id                INT NOT NULL,
              assignment_id         VARCHAR(64) NOT NULL,
              nama                  VARCHAR(255) DEFAULT NULL,
              skala_usaha           VARCHAR(50) DEFAULT NULL,
              jenis_prelist         VARCHAR(30) DEFAULT NULL,
              keberadaan_usaha      VARCHAR(50) DEFAULT NULL,
              kbli_kategori_prelist VARCHAR(5) DEFAULT NULL,
              hp                    VARCHAR(30) DEFAULT NULL,
              email                 VARCHAR(150) DEFAULT NULL,
              alamat                VARCHAR(255) DEFAULT NULL,
              assignment_status     VARCHAR(50) DEFAULT NULL,
              tanggal_modified      DATETIME DEFAULT NULL,
              imported_at           DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_tdu_assignment (assignment_id),
              KEY idx_tdu_sls (sls_id),
              CONSTRAINT fk_tdu_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tidak_ditemukan_keluarga (
              id                  INT NOT NULL AUTO_INCREMENT,
              sls_id              INT NOT NULL,
              assignment_id       VARCHAR(64) NOT NULL,
              nama                VARCHAR(255) DEFAULT NULL,
              keberadaan_keluarga VARCHAR(50) DEFAULT NULL,
              nomor_kk_prelist    VARCHAR(20) DEFAULT NULL,
              nomor_kk_sekarang   VARCHAR(20) DEFAULT NULL,
              alamat              VARCHAR(255) DEFAULT NULL,
              assignment_status   VARCHAR(50) DEFAULT NULL,
              tanggal_modified    DATETIME DEFAULT NULL,
              imported_at         DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_tdk_assignment (assignment_id),
              KEY idx_tdk_sls (sls_id),
              CONSTRAINT fk_tdk_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # Kandidat usaha yg nempel roster keluarga (jenis_prelist='keluarga')
        # tapi hp/email-nya SAMA PERSIS dengan usaha BKU (mandiri) yg sudah
        # ada — indikasi usaha yg sama sudah tercatat dobel, satu di keluarga
        # satu lagi sudah berdiri sendiri sbg BKU. Petugas perlu memindahkan/
        # menutup yg di keluarga lewat FASIH-mobile masing2 (lihat
        # sync_duplikat_bku). Baris TIDAK dihapus saat sudah tidak terdeteksi
        # lagi (artinya sudah dipindah) — cuma ditandai resolved_at, supaya
        # tetap ada riwayatnya (lihat menu Riwayat di admin).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usaha_keluarga_bku_duplikat (
              id                      INT NOT NULL AUTO_INCREMENT,
              sls_id                  INT NOT NULL,
              assignment_id_keluarga  VARCHAR(64) NOT NULL,
              nama_usaha_keluarga     VARCHAR(255) DEFAULT NULL,
              assignment_id_bku       VARCHAR(64) NOT NULL,
              nama_usaha_bku          VARCHAR(255) DEFAULT NULL,
              match_field             VARCHAR(10) NOT NULL,
              match_value             VARCHAR(150) NOT NULL,
              first_detected_at       DATETIME NOT NULL,
              synced_at               DATETIME NOT NULL,
              resolved_at             DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_ukbd_pair (assignment_id_keluarga, assignment_id_bku, match_field),
              KEY idx_ukbd_sls (sls_id),
              KEY idx_ukbd_resolved (resolved_at),
              CONSTRAINT fk_ukbd_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()

    # Auto-migrate kolom buat DB lama yg tabelnya sudah ada duluan dgn skema
    # sebelum kolom2 ini ditambahkan — CREATE TABLE IF NOT EXISTS di atas gak
    # nyentuh tabel yg udah ada.
    _ensure_column(conn, "tidak_ditemukan_usaha", "jenis_prelist",
                    "jenis_prelist VARCHAR(30) DEFAULT NULL AFTER skala_usaha")
    _ensure_column(conn, "tidak_ditemukan_usaha", "keberadaan_usaha",
                    "keberadaan_usaha VARCHAR(50) DEFAULT NULL AFTER jenis_prelist")
    _ensure_column(conn, "tidak_ditemukan_usaha", "kbli_kategori_prelist",
                    "kbli_kategori_prelist VARCHAR(5) DEFAULT NULL AFTER keberadaan_usaha")
    _ensure_column(conn, "tidak_ditemukan_usaha", "hp",
                    "hp VARCHAR(30) DEFAULT NULL AFTER kbli_kategori_prelist")
    _ensure_column(conn, "tidak_ditemukan_usaha", "email",
                    "email VARCHAR(150) DEFAULT NULL AFTER hp")
    _ensure_column(conn, "tidak_ditemukan_keluarga", "keberadaan_keluarga",
                    "keberadaan_keluarga VARCHAR(50) DEFAULT NULL AFTER nama")
    _ensure_column(conn, "tidak_ditemukan_keluarga", "nomor_kk_prelist",
                    "nomor_kk_prelist VARCHAR(20) DEFAULT NULL AFTER keberadaan_keluarga")
    _ensure_column(conn, "tidak_ditemukan_keluarga", "nomor_kk_sekarang",
                    "nomor_kk_sekarang VARCHAR(20) DEFAULT NULL AFTER nomor_kk_prelist")


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
                   keberadaan_usaha, kbli_kategori_prelist, hp, email, alamat, assignment_status, tanggal_modified, imported_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  sls_id                = VALUES(sls_id),
                  nama                  = VALUES(nama),
                  skala_usaha           = VALUES(skala_usaha),
                  jenis_prelist         = VALUES(jenis_prelist),
                  keberadaan_usaha      = VALUES(keberadaan_usaha),
                  kbli_kategori_prelist = VALUES(kbli_kategori_prelist),
                  hp                    = VALUES(hp),
                  email                 = VALUES(email),
                  alamat                = VALUES(alamat),
                  assignment_status     = VALUES(assignment_status),
                  tanggal_modified      = VALUES(tanggal_modified),
                  imported_at           = VALUES(imported_at)
            """, (
                sls_id, assignment_id, r.get("nama_usaha"), r.get("skala_usaha"),
                r.get("jenis_prelist"), _clean_label(r.get("keberadaan_usaha_label")),
                r.get("kategori_2025"), r.get("hp"), r.get("email"),
                # se2026_nested.alamat_usaha* nyaris selalu kosong utk usaha yg
                # TIDAK DITEMUKAN (petugas belum sempat catat alamat detail di
                # lapangan) — fallback ke alamat prelisting di root_table:
                # alamat_prelist (usaha bangunan mandiri) atau alamat_klrg
                # (usaha yg nempel keluarga, pakai alamat keluarganya).
                _first(r.get("alamat_usaha"), r.get("alamat_usaha_utama"),
                       r.get("alamat_prelist"), r.get("alamat_klrg")),
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
                  (sls_id, assignment_id, nama, keberadaan_keluarga, nomor_kk_prelist, nomor_kk_sekarang,
                   alamat, assignment_status, tanggal_modified, imported_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  sls_id               = VALUES(sls_id),
                  nama                 = VALUES(nama),
                  keberadaan_keluarga  = VALUES(keberadaan_keluarga),
                  nomor_kk_prelist     = VALUES(nomor_kk_prelist),
                  nomor_kk_sekarang    = VALUES(nomor_kk_sekarang),
                  alamat               = VALUES(alamat),
                  assignment_status    = VALUES(assignment_status),
                  tanggal_modified     = VALUES(tanggal_modified),
                  imported_at          = VALUES(imported_at)
            """, (
                sls_id, r.get("assignment_id"), _first(r.get("nama_kk"), r.get("dtsen_nama_kk")),
                r.get("keberadaan_keluarga", "Tidak Ditemukan"),
                r.get("no_kk"), r.get("dtsen_no_kk"),
                _first(r.get("alamat_klrg"), r.get("alamat_prelist")),
                r.get("assignment_status_alias"), r.get("assignment_date_modified"), synced_at,
            ))
    conn.commit()
    if skipped:
        print(f"[DB] keluarga: {skipped} baris tanpa sls_id (kode_sls tidak ketemu di tabel sls)", flush=True)


def delete_stale(conn, table, synced_at):
    """Hapus baris yg gak ke-refresh di run ini (imported_at < synced_at) —
    artinya usaha/keluarga itu SUDAH TIDAK masuk kriteria manapun lagi di FASIH
    sekarang (mis. tadinya Tidak Ditemukan, sekarang sudah Ditemukan/Baru).
    Tanpa ini baris lama numpuk selamanya krn upsert doang gak pernah hapus —
    nyata kejadian: 20.244 baris basi ketemu tanggal 2026-08-04 dari sync
    terakhir 2026-07-22, gara2 gak pernah dibersihkan."""
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {table} WHERE imported_at < %s", (synced_at,)
        )
        deleted = cur.rowcount
    conn.commit()
    if deleted:
        print(f"[DB] {table}: {deleted} baris basi dihapus (sudah tidak masuk kriteria lagi).", flush=True)


# ── Deteksi usaha keluarga yg perlu dipindah ke BKU ─────────────────────────
# Dijalankan SETELAH tidak_ditemukan_usaha ter-upsert (butuh hp/email yg baru
# disync di situ) — cocokkan usaha jenis_prelist='keluarga' dengan usaha BKU
# (jenis_prelist mandiri) yg hp ATAU email-nya SAMA PERSIS. Kalau ketemu,
# berarti usaha yg sama sudah tercatat dobel: sekali nempel di roster
# keluarga, sekali lagi berdiri sendiri sbg BKU — yg di keluarga itu duplikat
# yg perlu dipindahkan/ditutup petugas via FASIH-mobile.

_DUP_BKU_MATCH_SQL = """
    SELECT k.sls_id AS sls_id,
           k.assignment_id AS aid_keluarga, k.nama AS nama_keluarga,
           b.assignment_id AS aid_bku, b.nama AS nama_bku,
           %s AS match_field, k.{col} AS match_value
    FROM tidak_ditemukan_usaha k
    INNER JOIN tidak_ditemukan_usaha b
            ON b.{col} = k.{col}
           AND b.assignment_id != k.assignment_id
           AND (b.jenis_prelist IS NULL OR b.jenis_prelist != 'keluarga')
    WHERE k.jenis_prelist = 'keluarga'
      AND k.{col} IS NOT NULL AND k.{col} != ''
"""


def find_duplikat_bku(conn):
    """Cari pasangan (usaha keluarga, usaha BKU) yg hp atau email-nya sama
    persis. Dua query terpisah (hp, email) lalu digabung di Python, bukan
    UNION SQL — supaya kalau SATU pasang assignment cocok di hp MAUPUN email
    sekaligus, keduanya tetap tercatat sbg 2 bukti match yg beda (lihat
    UNIQUE KEY (assignment_id_keluarga, assignment_id_bku, match_field) di
    usaha_keluarga_bku_duplikat)."""
    pairs = []
    with conn.cursor() as cur:
        for field in ("hp", "email"):
            cur.execute(_DUP_BKU_MATCH_SQL.format(col=field), (field,))
            pairs.extend(cur.fetchall())
    return pairs


def sync_duplikat_bku(conn, synced_at):
    pairs = find_duplikat_bku(conn)
    with conn.cursor() as cur:
        for p in pairs:
            cur.execute("""
                INSERT INTO usaha_keluarga_bku_duplikat
                  (sls_id, assignment_id_keluarga, nama_usaha_keluarga,
                   assignment_id_bku, nama_usaha_bku, match_field, match_value,
                   first_detected_at, synced_at, resolved_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                ON DUPLICATE KEY UPDATE
                  sls_id              = VALUES(sls_id),
                  nama_usaha_keluarga = VALUES(nama_usaha_keluarga),
                  nama_usaha_bku      = VALUES(nama_usaha_bku),
                  match_value         = VALUES(match_value),
                  synced_at           = VALUES(synced_at),
                  resolved_at         = NULL
            """, (
                p["sls_id"], p["aid_keluarga"], p["nama_keluarga"],
                p["aid_bku"], p["nama_bku"], p["match_field"], p["match_value"],
                synced_at, synced_at,
            ))
        # Pasangan yg dulu terdeteksi tapi baris ini TIDAK ikut ke-refresh
        # (synced_at masih yg lama) berarti sudah tidak cocok lagi sekarang —
        # bisa krn petugas sudah memindahkan/menutup usaha di keluarga, atau
        # salah satu sisi berubah datanya. Tandai resolved (bukan dihapus,
        # supaya ada riwayatnya).
        cur.execute("""
            UPDATE usaha_keluarga_bku_duplikat
            SET resolved_at = %s
            WHERE resolved_at IS NULL AND synced_at < %s
        """, (synced_at, synced_at))
        resolved_now = cur.rowcount
    conn.commit()
    print(f"[DB] duplikat usaha keluarga→BKU: {len(pairs)} pasangan aktif, {resolved_now} baru selesai (sudah dipindah).", flush=True)


def run_once():
    print("=" * 50, flush=True)
    print(f"SYNC TIDAK DITEMUKAN (FASIH Dashboard SQL Lab) → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print("=" * 50, flush=True)

    conn = _connect_db()
    ensure_tables(conn)
    sls_map = load_sls_map(conn)

    usaha_rows = _load_checkpoint("usaha")
    keluarga_rows = _load_checkpoint("keluarga")

    if usaha_rows is None or keluarga_rows is None:
        with sync_playwright() as pw:
            browser, ctx = _make_browser(pw)
            try:
                page = login(ctx)
                page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=180_000)
                _check_bot_wall(page.content(), "buka SQL Lab")

                # Fase 1: usaha semua status (bangunan mandiri + roster keluarga
                # digabung) + usaha Open (belum disentuh)
                if usaha_rows is None:
                    print("\n[FASE 1] Usaha (semua status)...", flush=True)
                    usaha_total = get_count(page, USAHA_COUNT_QUERY)
                    print(f"[FASE 1] Total baris (perkiraan): {usaha_total}", flush=True)
                    usaha_rows = scrape_paginated(page, USAHA_QUERY_TEMPLATE, "usaha", usaha_total)

                    print("\n[FASE 1b] Usaha Open (belum disentuh)...", flush=True)
                    open_usaha_total = get_count(page, OPEN_USAHA_COUNT_QUERY)
                    print(f"[FASE 1b] Total baris (perkiraan): {open_usaha_total}", flush=True)
                    open_usaha_rows = scrape_paginated(page, OPEN_USAHA_QUERY_TEMPLATE, "usaha-open", open_usaha_total)
                    for r in open_usaha_rows:
                        r["index1"] = "open"
                        r["keberadaan_usaha_label"] = "Open"
                    usaha_rows = usaha_rows + open_usaha_rows

                    _save_checkpoint("usaha", usaha_rows)
                else:
                    print(f"\n[FASE 1] Pakai checkpoint tersimpan ({len(usaha_rows)} baris) — skip scraping ulang.", flush=True)

                # Fase 2: keluarga semua status + keluarga Open (belum disentuh)
                if keluarga_rows is None:
                    print("\n[FASE 2] Keluarga (semua status)...", flush=True)
                    keluarga_total = get_count(page, KELUARGA_COUNT_QUERY)
                    print(f"[FASE 2] Total baris (perkiraan): {keluarga_total}", flush=True)
                    keluarga_rows = scrape_paginated(page, KELUARGA_QUERY_TEMPLATE, "keluarga", keluarga_total)
                    for r in keluarga_rows:
                        r["keberadaan_keluarga"] = _clean_label(r.get("ada_keluarga_label")) or "Tidak Ditemukan"

                    print("\n[FASE 2b] Keluarga Open (belum disentuh)...", flush=True)
                    open_keluarga_total = get_count(page, OPEN_KELUARGA_COUNT_QUERY)
                    print(f"[FASE 2b] Total baris (perkiraan): {open_keluarga_total}", flush=True)
                    open_keluarga_rows = scrape_paginated(page, OPEN_KELUARGA_QUERY_TEMPLATE, "keluarga-open", open_keluarga_total)
                    for r in open_keluarga_rows:
                        r["keberadaan_keluarga"] = "Open"
                    keluarga_rows = keluarga_rows + open_keluarga_rows

                    _save_checkpoint("keluarga", keluarga_rows)
                else:
                    print(f"\n[FASE 2] Pakai checkpoint tersimpan ({len(keluarga_rows)} baris) — skip scraping ulang.", flush=True)
            finally:
                browser.close()
    else:
        print("\n[FASE 1+2] Pakai checkpoint tersimpan untuk usaha & keluarga — skip scraping, langsung upsert.", flush=True)

    synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[FASE 1] Upsert {len(usaha_rows)} baris usaha ke DB...", flush=True)
    upsert_usaha(conn, usaha_rows, sls_map, synced_at)
    delete_stale(conn, "tidak_ditemukan_usaha", synced_at)
    _clear_checkpoint("usaha")
    print(f"[FASE 1] Selesai: {len(usaha_rows)} baris usaha di-sync.", flush=True)

    print(f"\n[FASE 1c] Deteksi usaha keluarga duplikat vs usaha BKU (cocok hp/email)...", flush=True)
    sync_duplikat_bku(conn, synced_at)

    print(f"\n[FASE 2] Upsert {len(keluarga_rows)} baris keluarga ke DB...", flush=True)
    upsert_keluarga(conn, keluarga_rows, sls_map, synced_at)
    delete_stale(conn, "tidak_ditemukan_keluarga", synced_at)
    _clear_checkpoint("keluarga")
    print(f"[FASE 2] Selesai: {len(keluarga_rows)} baris keluarga di-sync.", flush=True)

    conn.close()
    print(f"\nSelesai semua fase!", flush=True)


def _next_run():
    # 4x sehari jam 01:30, 07:30, 13:30, 19:30 WITA (lihat SYNC_TIMES).
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
