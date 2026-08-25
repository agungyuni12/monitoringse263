"""
Sync "Usaha (Data Ekonomi)" — SEMUA usaha (semua kategori KBLI, bukan cuma
kategori A) lengkap dgn data ekonomi (pendapatan, pengeluaran, biaya, gaji,
tenaga kerja, dst) dari Superset SQL Lab FASIH Dashboard → database se2026
(tabel usaha_ekonomi — dipakai LANGSUNG oleh web UI monitoringse, lihat
handlers/usaha_ekonomi.go).

Awalnya scope-nya cuma kategori A (nama file lama: sync_kategori_a.py) —
diperluas jadi SEMUA usaha atas permintaan, tabel & file di-rename jadi
usaha_ekonomi biar gak menyesatkan (isinya udah bukan cuma kategori A lagi).

Sumbernya sama persis dengan scraper/sync_usaha.py (Superset SQL Lab di
fasih-dashboard.bps.go.id, BUKAN FASIH API biasa) — lihat docstring modul itu
kalau butuh detail alur login. Script ini dipisah sendiri (bukan digabung ke
sync_usaha.py) krn topiknya beda: bukan status keberadaan, tapi klasifikasi
KBLI + data ekonomi usaha.

Sumber data (ditelusuri manual lewat SQL Lab — lihat percakapan):
  - Scope: se2026_nested TANPA filter kategori, TAPI difilter
    keberadaan_usaha_value IN ('1','2') — cuma usaha yg Ditemukan ('1') atau
    Baru ('2'). Usaha yg Tidak Ditemukan/Tutup/Ganda ('00'/'3'/'4', sudah
    ditrack terpisah di tabel tidak_ditemukan_usaha via sync_usaha.py — lihat
    docstring modul itu soal kode keberadaan_usaha_value lengkap) atau Non
    Respon ('9') gak mungkin punya data ekonomi krn emang gak ada yg
    disurvei — DIBUANG dari scope tabel ini, bukan trap/bug, supaya usaha yg
    sengaja gak punya isian ekonomi gak numpuk jadi baris kosong.
  - Superset SQL Lab di FASIH Dashboard MEMBATASI hasil query maksimal 25
    kolom/query — query di bawah dipangkas biar pas di limit itu (lihat
    percakapan soal kolom mana yg dibuang & alasannya):
      - kategori & kbli_label digabung LANGSUNG DI SQL pakai COALESCE
        (bukan ambil 2 kolom mentah lalu gabung di Python kayak awalnya)
        biar hemat kolom:
          kategori: COALESCE(n.kategori, n.kategori_2025) — kategori (tahun
            ini, 58,9% terisi) diprioritaskan, fallback ke kategori_2025
            (carry-over ST2023/prelist, KOMPLEMENTER SEBAGIAN) → 93,4%.
          kbli_label: COALESCE(n.kbli_label, n.kbli_genai_label) — label
            manual (26,4% terisi) diprioritaskan, fallback ke kbli_genai_label
            (GenAI, KOMPLEMENTER 100%, 0 tumpang tindih) → 100% coverage.
          nama_kk: CASE jenis_prelist='keluarga' → COALESCE(r.nama_kk,
            r.dtsen_nama_kk), sama pola coalesce-di-SQL.
      - DIBUANG total (bukan cuma digabung): alamat_usaha/alamat_usaha_utama/
        alamat_prelist/alamat_klrg (lokasi udah kepake dari sls via sls_id,
        gak perlu duplikat alamat teks), skala_usaha, biaya_pembelian/
        biaya_pembelian_bln (cuma 9% terisi, cuma relevan usaha dagang/
        reseller yg beli barang buat dijual lagi — bukan semua jenis usaha),
        tk_laki/tk_pr/tk_tdk_dibayar (breakdown gender & status bayar pekerja
        — tk_dibayar tetap disimpan sbg angka tenaga kerja utama).
      - jenis_kegiatan — DICOBA, 0% terisi (field gak kepake), DIBUANG.
  - Data ekonomi yg TETAP (semua DOUBLE kecuali tk_dibayar & total_tk_jk yg
    INTEGER; ~57-59% coverage-nya DICEK SEBELUM filter keberadaan_usaha_value
    ditambahkan, thd 106.117 total usaha TANPA filter — jadi angka itu
    ketinggian pesimis, coverage sebenarnya thd scope Ditemukan/Baru yg
    dipakai SEKARANG harusnya lebih tinggi krn baris yg emang gak mungkin
    keisi (Tutup/Tidak Ditemukan/Ganda) udah dibuang duluan):
      total_pendapatan/total_pendapatan_bln, total_pengeluaran/
        total_pengeluaran_bln — versi bulanan (_bln) TETAP disimpan meski
        jarang terisi krn kepake khusus usaha baru yg lapor per bulan (bukan
        per periode survei).
      biaya_produksi/biaya_produksi_bln, gaji/gaji_bln, operasional/
        operasional_bln — satu blok kuesioner ekonomi yg sama.
      tk_dibayar (tenaga kerja DIBAYAR, INTEGER) — blok yg sama jg, TETAP
        disimpan apa adanya (bukan diganti).
      keg_utama (deskripsi kegiatan utama usaha).
      luas_tanah_bln/luas_tanah_thn — BUKAN dari blok ekonomi (makanya
        coverage-nya beda jauh): luas_tanah_thn 93,9% terisi (63.177/67.246,
        thd scope Ditemukan/Baru), luas_tanah_bln cuma 2,6% (jarang, sama pola
        _bln yg lain — khusus usaha yg lapor per bulan bukan per tahun).
        Ganti biaya_non_operasional/non_operasional_bln (dibuang) krn luas
        tanah jauh lebih lengkap datanya & lebih kepake buat profil usaha.
      total_tk_jk (total tenaga kerja SEMUA gender, INTEGER, DITAMBAHKAN —
        bukan pengganti tk_dibayar) — BUKAN dari blok ekonomi jg, coverage
        96,7% (71.481/73.951 thd scope Ditemukan/Baru). Kolom ke-25 (pas di
        limit MAKS 25 kolom/query Superset). Beda dari tk_dibayar (cuma yg
        DIBAYAR): total_tk_jk = SEMUA pekerja (dibayar + tdk dibayar) —
        dicek manual mis. total_tk_jk=7, tk_dibayar=6 (1 pekerja keluarga
        gak dibayar). Dua-duanya disimpan krn beda makna, bukan duplikat.
    Sisa kekosongan (di luar Tutup/Tidak Ditemukan/Ganda yg udah difilter)
    WAJAR (bukan trap kayak no_kk_prelist/kbli_prelist dulu) — blok ekonomi
    kuesioner emang belum semua usaha Ditemukan/Baru selesai diisi
    petugas, bukan field yg salah/gak kepake.
  - jenis_prelist (root_table, via JOIN assignment_id — sama pola dgn
    sync_usaha.py): bedain usaha bangunan mandiri (!= 'keluarga') vs usaha yg
    nempel roster keluarga ('keluarga'). nama_usaha & nama_kk DIPISAH jadi
    dua kolom sendiri2 — nama_kk cuma keisi kalau jenis_prelist='keluarga'.

Pagination & anti-bot-wall: identik dgn sync_usaha.py (baca docstring modul
itu) — LIMIT/OFFSET langsung se-kabupaten, PAGE_SIZE 9000/eksekusi, ORDER BY
assignment_id biar pagination stabil, baca response /execute/ langsung tanpa
reload biar gak kena WAF F5.

Browser: Chrome asli via CDP (bukan browser managed Playwright biasa) —
launch google-chrome-stable sbg proses terpisah dgn --remote-debugging-port
& user-data-dir sendiri (scraper/.chrome-profile-usaha-ekonomi/, TERPISAH
dari profile Chrome pribadi), lalu playwright connect_over_cdp ke situ.
Fingerprint-nya persis Chrome beneran (bukan managed browser Playwright yg
lebih gampang kena bot-wall F5), dan profile persisted di disk bikin cookie
sesi FASIH kepake lagi antar-run.

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

import os, json, random, re, subprocess, time, urllib.request
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

PAGE_SIZE = 9000      # dicek manual via raw SELECT (bukan COUNT) — lihat sync_usaha.py
PAGE_DELAY_MIN = 3    # jeda antar halaman (detik) — biar traffic gak seragam
PAGE_DELAY_MAX = 8

# Scope cuma keberadaan_usaha_value '1' (Ditemukan) & '2' (Baru) — usaha yg
# Tidak Ditemukan/Tutup/Ganda ('00'/'3'/'4', sudah ditrack terpisah di tabel
# tidak_ditemukan_usaha, lihat sync_usaha.py) atau Non Respon ('9') gak
# mungkin punya data ekonomi (gak ada yg disurvei), jadi cuma bikin baris
# kosong nambahin noise ke tabel ini kalau ikut ditarik.
USAHA_EKONOMI_STATUS_VALUES = "'1','2'"

USAHA_EKONOMI_COUNT_QUERY = (
    "SELECT COUNT(*) AS n FROM se2026_nested "
    f"WHERE keberadaan_usaha_value IN ({USAHA_EKONOMI_STATUS_VALUES})"
)

USAHA_EKONOMI_QUERY_TEMPLATE = """
SELECT n.assignment_id, n.index1, n.nama_usaha,
       COALESCE(n.kategori, n.kategori_2025) AS kategori,
       COALESCE(n.kbli_label, n.kbli_genai_label) AS kbli_label,
       n.total_pendapatan, n.total_pendapatan_bln, n.total_pengeluaran, n.total_pengeluaran_bln,
       n.biaya_produksi, n.biaya_produksi_bln,
       n.gaji, n.gaji_bln, n.operasional, n.operasional_bln,
       n.luas_tanah_bln, n.luas_tanah_thn,
       n.tk_dibayar, n.total_tk_jk, n.keg_utama,
       n.level_6_full_code, n.assignment_status_alias, n.assignment_date_modified,
       r.jenis_prelist,
       CASE WHEN r.jenis_prelist = 'keluarga' THEN COALESCE(r.nama_kk, r.dtsen_nama_kk) ELSE NULL END AS nama_kk
FROM se2026_nested n
INNER JOIN root_table r ON n.assignment_id = r.assignment_id
WHERE n.keberadaan_usaha_value IN (""" + USAHA_EKONOMI_STATUS_VALUES + """)
ORDER BY n.assignment_id, n.index1
LIMIT {limit} OFFSET {offset}
""".strip()

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
WITA = timezone(timedelta(hours=8))

# Chrome asli via CDP (bukan browser managed Playwright) — fingerprint jauh
# lebih mirip pemakai beneran, jadi lebih tahan bot-wall F5 BPS. Profile
# terpisah dari Chrome pribadi (bukan .config/google-chrome), disk-persisted
# biar cookie sesi FASIH kepake lagi antar-run (login lebih jarang ke-trigger
# bot detection).
CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
CDP_PROFILE_DIR = os.getenv(
    "CDP_PROFILE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chrome-profile-usaha-ekonomi"),
)

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


def _check_bot_wall(text, tag):
    if "Bot Detected" in text or "sistem kami mendeteksi koneksi anda sebagai bot" in text:
        m = re.search(r"BOT-\d+", text)
        code = m.group(0) if m else "?"
        raise RuntimeError(f"Diblokir bot-detection BPS di tahap '{tag}' (kode {code})")


def _wait_cdp_ready(port, proc, timeout=45):
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Chrome mati sebelum CDP siap (exit code {proc.returncode})")
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"Chrome CDP tidak siap di port {port} setelah {timeout}s")


def _make_browser(pw):
    chrome_path = os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable")
    os.makedirs(CDP_PROFILE_DIR, exist_ok=True)
    args = [
        chrome_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={CDP_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        # --no-sandbox & --disable-dev-shm-usage: WAJIB di container Docker —
        # Chrome sandbox butuh capability yg gak ada di container biasa (bikin
        # gagal start sama sekali), dan /dev/shm default Docker cuma 64MB
        # (kekecilan buat Chrome, bikin crash/hang) — lihat sync_usaha.py yg
        # pakai flag sama persis via pw.chromium.launch().
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--window-size=1280,800",
        "--lang=id-ID",
    ]
    if HEADLESS:
        args.append("--headless=new")
    # stderr ke file (bukan PIPE) — kalau PIPE gak pernah didrain selama proses
    # jalan lama, buffer OS-nya (~64KB) bisa penuh & bikin Chrome nge-block pas
    # nulis stderr (Chrome headless lumayan berisik soal GL/ANGLE warnings).
    stderr_log = open(os.path.join(CDP_PROFILE_DIR, "chrome_stderr.log"), "w")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr_log)
    try:
        _wait_cdp_ready(CDP_PORT, proc)
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    except Exception:
        proc.terminate()
        stderr_log.close()
        try:
            with open(stderr_log.name) as f:
                tail = f.read()[-2000:]
            if tail.strip():
                print(f"[CHROME][STDERR] {tail}", flush=True)
        except Exception:
            pass
        raise
    return browser, ctx, proc


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


def _already_authenticated(page):
    return "fasih-dashboard.bps.go.id" in page.url and "/login" not in page.url


def _do_login(ctx):
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)

    page.goto(f"{DASH_URL}/login/", wait_until="networkidle", timeout=180_000)
    _check_bot_wall(page.content(), "halaman login")

    # Profile Chrome via CDP persisted di disk (lihat CDP_PROFILE_DIR) — kalau
    # sesi FASIH dari run sebelumnya masih valid, /login/ langsung redirect ke
    # dashboard tanpa nunjukin form sama sekali. Jangan maksa nunggu form yg
    # emang gak bakal muncul.
    if _already_authenticated(page):
        print(f"[LOGIN] Sesi tersimpan (profile CDP) masih valid, skip form login → {page.url}", flush=True)
        return page

    try:
        page.wait_for_selector("button:has-text('Go!')", timeout=180_000)
    except Exception:
        print(f"[LOGIN][DEBUG] url={page.url}", flush=True)
        print(f"[LOGIN][DEBUG] html snippet: {page.content()[:1500]}", flush=True)
        raise
    page.click("button:has-text('Go!')")

    try:
        page.wait_for_selector("#username", timeout=30_000)
    except Exception:
        # Sama kasusnya: klik "Go!" bisa langsung redirect balik ke dashboard
        # (skip Keycloak) kalau sesi masih valid — bukan kegagalan beneran.
        if _already_authenticated(page):
            print(f"[LOGIN] Sesi tersimpan (profile CDP) masih valid, skip form login → {page.url}", flush=True)
            return page
        print(f"[LOGIN][DEBUG] url={page.url}", flush=True)
        print(f"[LOGIN][DEBUG] html snippet: {page.content()[:1500]}", flush=True)
        raise
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


def _set_sql_editor(page, sql, attempts=3):
    # Ace editor gak selalu bersih pas di-clear+fill — kalau attempt ini abis
    # reload halaman (retry di _run_query_and_fetch), draft-autosave Superset
    # bisa balapan nyisipin query lama ke editor SETELAH kita fill, hasilnya
    # nyambung jadi 1 string invalid (mis. "...OFFSET 30000SELECT ...") yg
    # ditolak parser SQL. Verifikasi jumlah "SELECT" di editor match sama
    # query yg diminta — kalau enggak, ulang clear+fill.
    expected_selects = sql.upper().count("SELECT")
    for attempt in range(1, attempts + 1):
        page.locator(".ace_content").click()
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.press("Delete")
        page.locator("textarea.ace_text-input").fill(sql)
        _human_pause(0.2, 0.4)
        current = page.locator(".ace_content").inner_text()
        if current.upper().count("SELECT") == expected_selects:
            return
        print(f"    [WARN] Editor SQL Lab kemungkinan belum bersih (percobaan {attempt}/{attempts}) — ulang clear+fill...", flush=True)
    raise RuntimeError("Editor SQL Lab gak sinkron sama query yg diminta (race sama draft-autosave Superset)")


def _run_query_and_fetch(page, sql, retries=5):
    """Sama persis dgn sync_usaha.py — lihat docstring di sana."""
    for attempt in range(1, retries + 1):
        try:
            page.wait_for_selector(".ace_content", timeout=180_000)
            page.wait_for_selector('button:has-text("Run")', timeout=180_000)
            _set_sql_editor(page, sql)

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


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usaha_ekonomi (
              id                  INT NOT NULL AUTO_INCREMENT,
              sls_id              INT NOT NULL,
              assignment_id       VARCHAR(64) NOT NULL,
              nama_usaha          VARCHAR(255) DEFAULT NULL,
              nama_kk             VARCHAR(255) DEFAULT NULL,
              jenis_prelist       VARCHAR(30) DEFAULT NULL,
              kategori            VARCHAR(5) DEFAULT NULL,
              kbli_label          TEXT DEFAULT NULL,
              pendapatan          DOUBLE DEFAULT NULL,
              pendapatan_bln      DOUBLE DEFAULT NULL,
              pengeluaran         DOUBLE DEFAULT NULL,
              pengeluaran_bln     DOUBLE DEFAULT NULL,
              biaya_produksi      DOUBLE DEFAULT NULL,
              biaya_produksi_bln  DOUBLE DEFAULT NULL,
              gaji                DOUBLE DEFAULT NULL,
              gaji_bln            DOUBLE DEFAULT NULL,
              operasional         DOUBLE DEFAULT NULL,
              operasional_bln     DOUBLE DEFAULT NULL,
              luas_tanah_bln      DOUBLE DEFAULT NULL,
              luas_tanah_thn      DOUBLE DEFAULT NULL,
              tk_dibayar          INT DEFAULT NULL,
              total_tk_jk         INT DEFAULT NULL,
              keg_utama           TEXT DEFAULT NULL,
              assignment_status   VARCHAR(50) DEFAULT NULL,
              tanggal_modified    DATETIME DEFAULT NULL,
              imported_at         DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_ue_assignment (assignment_id),
              KEY idx_ue_sls (sls_id),
              CONSTRAINT fk_ue_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def load_sls_map(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls")
        return {r["kode_sls"]: r["id"] for r in cur.fetchall()}


def upsert_usaha_ekonomi(conn, rows, sls_map, synced_at):
    skipped = 0
    with conn.cursor() as cur:
        for r in rows:
            sls_id = sls_map.get(r.get("level_6_full_code"))
            if sls_id is None:
                skipped += 1
                continue
            assignment_id = f"{r.get('assignment_id')}#{r.get('index1')}"
            cur.execute("""
                INSERT INTO usaha_ekonomi
                  (sls_id, assignment_id, nama_usaha, nama_kk, jenis_prelist, kategori, kbli_label,
                   pendapatan, pendapatan_bln, pengeluaran, pengeluaran_bln,
                   biaya_produksi, biaya_produksi_bln,
                   gaji, gaji_bln, operasional, operasional_bln, luas_tanah_bln, luas_tanah_thn,
                   tk_dibayar, total_tk_jk, keg_utama,
                   assignment_status, tanggal_modified, imported_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  sls_id              = VALUES(sls_id),
                  nama_usaha          = VALUES(nama_usaha),
                  nama_kk             = VALUES(nama_kk),
                  jenis_prelist       = VALUES(jenis_prelist),
                  kategori            = VALUES(kategori),
                  kbli_label          = VALUES(kbli_label),
                  pendapatan          = VALUES(pendapatan),
                  pendapatan_bln      = VALUES(pendapatan_bln),
                  pengeluaran         = VALUES(pengeluaran),
                  pengeluaran_bln     = VALUES(pengeluaran_bln),
                  biaya_produksi      = VALUES(biaya_produksi),
                  biaya_produksi_bln  = VALUES(biaya_produksi_bln),
                  gaji                = VALUES(gaji),
                  gaji_bln            = VALUES(gaji_bln),
                  operasional         = VALUES(operasional),
                  operasional_bln     = VALUES(operasional_bln),
                  luas_tanah_bln      = VALUES(luas_tanah_bln),
                  luas_tanah_thn      = VALUES(luas_tanah_thn),
                  tk_dibayar          = VALUES(tk_dibayar),
                  total_tk_jk         = VALUES(total_tk_jk),
                  keg_utama           = VALUES(keg_utama),
                  assignment_status   = VALUES(assignment_status),
                  tanggal_modified    = VALUES(tanggal_modified),
                  imported_at         = VALUES(imported_at)
            """, (
                sls_id, assignment_id, r.get("nama_usaha"),
                # nama_kk & kategori & kbli_label udah di-COALESCE langsung di
                # SQL (lihat USAHA_EKONOMI_QUERY_TEMPLATE) — hemat kolom biar
                # muat di limit 25 kolom/query Superset SQL Lab.
                r.get("nama_kk"),
                r.get("jenis_prelist"),
                r.get("kategori"),
                r.get("kbli_label"),
                r.get("total_pendapatan"), r.get("total_pendapatan_bln"),
                r.get("total_pengeluaran"), r.get("total_pengeluaran_bln"),
                r.get("biaya_produksi"), r.get("biaya_produksi_bln"),
                r.get("gaji"), r.get("gaji_bln"),
                r.get("operasional"), r.get("operasional_bln"),
                r.get("luas_tanah_bln"), r.get("luas_tanah_thn"),
                r.get("tk_dibayar"), r.get("total_tk_jk"),
                r.get("keg_utama"),
                r.get("assignment_status_alias"), r.get("assignment_date_modified"), synced_at,
            ))
    conn.commit()
    if skipped:
        print(f"[DB] usaha_ekonomi: {skipped} baris tanpa sls_id (kode_sls tidak ketemu di tabel sls)", flush=True)


def delete_stale(conn, table, synced_at):
    """Hapus baris yg gak ke-refresh di run ini — lihat sync_usaha.py soal
    alasannya."""
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE imported_at < %s", (synced_at,))
        deleted = cur.rowcount
    conn.commit()
    if deleted:
        print(f"[DB] {table}: {deleted} baris basi dihapus (sudah tidak masuk kriteria lagi).", flush=True)


def run_once():
    print("=" * 50, flush=True)
    print(f"SYNC USAHA (DATA EKONOMI) (FASIH Dashboard SQL Lab) → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print("=" * 50, flush=True)

    conn = _connect_db()
    ensure_tables(conn)
    sls_map = load_sls_map(conn)

    rows = _load_checkpoint("usaha_ekonomi")

    if rows is None:
        with sync_playwright() as pw:
            browser, ctx, chrome_proc = _make_browser(pw)
            try:
                page = login(ctx)
                page.goto(f"{DASH_URL}/superset/sqllab/", wait_until="networkidle", timeout=180_000)
                _check_bot_wall(page.content(), "buka SQL Lab")

                print("\n[SCRAPE] Semua usaha (data ekonomi)...", flush=True)
                total = get_count(page, USAHA_EKONOMI_COUNT_QUERY)
                print(f"[SCRAPE] Total baris (perkiraan): {total}", flush=True)
                rows = scrape_paginated(page, USAHA_EKONOMI_QUERY_TEMPLATE, "usaha-ekonomi", total)
                _save_checkpoint("usaha_ekonomi", rows)
            finally:
                # browser.close() lewat CDP cuma disconnect, gak matiin proses
                # Chrome-nya — proc.terminate() yg beneran nutup.
                try:
                    browser.close()
                except Exception:
                    pass
                chrome_proc.terminate()
                try:
                    chrome_proc.wait(timeout=10)
                except Exception:
                    chrome_proc.kill()
    else:
        print(f"\n[SCRAPE] Pakai checkpoint tersimpan ({len(rows)} baris) — skip scraping ulang.", flush=True)

    synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[DB] Upsert {len(rows)} baris usaha ke DB...", flush=True)
    upsert_usaha_ekonomi(conn, rows, sls_map, synced_at)
    delete_stale(conn, "usaha_ekonomi", synced_at)
    _clear_checkpoint("usaha_ekonomi")
    print(f"[DB] Selesai: {len(rows)} baris usaha di-sync.", flush=True)

    conn.close()
    print(f"\nSelesai!", flush=True)


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
