"""
Sync status keberadaan usaha per-assignment dari FASIH API.
Source: GET /app/api/assignment-general/api/assignment/get-by-assignment-id?assignmentId={id}
Parse answers → dataKey "keberadaan_usaha#{n}" untuk tiap assignment usaha.

Hanya assignment USAHA: data6 (skala_usaha_all) terisi (UMK/UMKM/UMB/dll) → non-empty = usaha

Selain jawaban keberadaan_usaha# itu sendiri, juga ditangkap:
  - gate_label: kalau alur kuesioner berhenti duluan di pertanyaan gate keluarga/bangunan
    (jawabannya ditandai FASIH dengan literal "(STOP)" di label), pertanyaan
    keberadaan_usaha# memang tidak pernah muncul — bukan berarti belum dikerjakan.
  - assignment_status: assignment_status_alias dari FASIH (OPEN, SUBMITTED BY Pencacah, dst).

DB table: keberadaan_usaha
  assignment_id, sls_id, nama, skala_usaha, keberadaan_kode, keberadaan_label,
  gate_label, assignment_status, synced_at
"""

import os, time, json, random
import pymysql
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_URL       = "https://fasih-sm.bps.go.id"
FASIH_USER      = os.getenv("FASIH_USER",      "agung.yuniarta")
FASIH_PASS      = os.getenv("FASIH_PASS",      "kelayu1998")
FASIH_PERIOD_ID = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
KODE_KAB        = os.getenv("KODE_KABUPATEN",  "5205")
HEADLESS        = os.getenv("HEADLESS",        "false").lower() == "true"

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

CHUNK_SIZE     = 1    # SLS per chunk sebelum re-login — dibikin 1 (bukan batch besar)
                       # supaya traffic-nya kurang "rapi"/predictable buat WAF, tiap SLS
                       # langsung disimpan ke DB & re-login sebelum lanjut ke SLS berikutnya
CHUNK_DELAY_MIN = 5    # jeda sebelum login berikutnya diacak (detik), bukan flat — lihat
CHUNK_DELAY_MAX = 15   # CHUNK_SIZE=1 di atas: makin gak beraturan, makin gak kayak bot
REQUEST_DELAY  = 0.4  # detik jeda antar request detail assignment (sequential — lihat
                       # _page_fetch_one: Promise.all/batch concurrent kena block WAF F5,
                       # walau cuma 5 sekaligus, jadi detail assignment diambil satu-satu)

# Cooldown kalau kena block bertubi-tubi — terbukti dari log: sequential SATU
# proses SENDIRIAN pun akhirnya diblokir setelah ~1177 request kumulatif (bukan
# soal tabrakan sesama proses/burst), jadi WAF FASIH kemungkinan menghitung
# volume request per sesi/IP dalam rentang waktu, bukan cuma laju sesaat. Kalau
# gagal beruntun COOLDOWN_FAIL_THRESHOLD kali, itu tanda sedang diblokir massal
# (bukan gagal acak) — berhenti nge-gas requestnya & kasih jeda panjang dulu,
# supaya rentang waktu WAF-nya sempat reset, daripada ngotot nembak sisa batch
# yang sudah pasti gagal semua.
COOLDOWN_FAIL_THRESHOLD = 5    # kegagalan berturut-turut sebelum dianggap "lagi diblokir"
COOLDOWN_BASE_SECONDS   = 90   # jeda dasar tiap kali cooldown terpicu (makin sering, makin lama)
COOLDOWN_MAX_CYCLES     = 3    # maksimal cooldown per batch — di atas ini, lanjut apa adanya
                                # (biar gak nyangkut selamanya kalau memang lagi diblokir lama)

WITA = timezone(timedelta(hours=8))


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


# ── DB ───────────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


# Named lock MySQL (bukan lock tabel) supaya proses INI dan sync_keberadaan_rev.py
# GANTIAN memakai koneksi ke FASIH — WAF FASIH nge-block berdasar IP sumber
# gabungan kedua proses (login pakai akun BEDA tetap kena, karena bukan
# per-akun/session, tapi per-IP), jadi request-nya sendiri yang harus dicegah
# tabrakan, bukan cuma proses-nya yang dianggap "independen".
FASIH_LOCK_NAME    = "fasih_fetch"
FASIH_LOCK_TIMEOUT = 30  # detik nunggu giliran kalau proses lawan sedang pegang lock


def _fasih_lock(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s) AS got", (FASIH_LOCK_NAME, FASIH_LOCK_TIMEOUT))
        cur.fetchone()


def _fasih_unlock(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT RELEASE_LOCK(%s) AS released", (FASIH_LOCK_NAME,))
        cur.fetchone()


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS keberadaan_usaha (
              id                BIGINT NOT NULL AUTO_INCREMENT,
              sls_id            INT NOT NULL,
              assignment_id     VARCHAR(36) NOT NULL,
              nama              VARCHAR(255) DEFAULT NULL,
              skala_usaha       VARCHAR(100) DEFAULT NULL,
              keberadaan_kode   VARCHAR(10) DEFAULT NULL,
              keberadaan_label  VARCHAR(100) DEFAULT NULL,
              gate_label        VARCHAR(150) DEFAULT NULL,
              assignment_status VARCHAR(50) DEFAULT NULL,
              synced_at         DATETIME DEFAULT NULL,
              sync_keterangan   VARCHAR(255) DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uk_asgn (assignment_id),
              KEY idx_sls (sls_id),
              CONSTRAINT fk_kebrd_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def load_sls_map(conn):
    """kode_sls_16 → sls_id — dibatasi ke SLS yang punya usaha BKU tidak ditemukan
    (coverage_usaha_keluarga, kode_indikator 10247 'Jumlah Usaha Tidak Ditemukan
    (BKU)' > 0) — itu yang perlu di-recheck keberadaannya, bukan semua SLS."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.id, s.kode_sls
            FROM sls s
            JOIN coverage_usaha_keluarga c
              ON c.sls_id = s.id
             AND c.kode_indikator = '10247'
             AND c.total_value > 0
        """)
        return {r["kode_sls"]: r["id"] for r in cur.fetchall()}


def upsert_keberadaan(conn, sls_id, assignment_id, nama, skala_usaha,
                      kode, label, gate_label, assignment_status, synced_at,
                      sync_keterangan=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO keberadaan_usaha
              (sls_id, assignment_id, nama, skala_usaha,
               keberadaan_kode, keberadaan_label, gate_label, assignment_status, synced_at,
               sync_keterangan)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              sls_id            = VALUES(sls_id),
              nama              = IF(VALUES(nama) != '' AND VALUES(nama) IS NOT NULL,
                                     VALUES(nama), nama),
              skala_usaha       = IF(VALUES(skala_usaha) != '' AND VALUES(skala_usaha) IS NOT NULL,
                                     VALUES(skala_usaha), skala_usaha),
              keberadaan_kode   = IF(VALUES(keberadaan_kode) IS NOT NULL,
                                     VALUES(keberadaan_kode), keberadaan_kode),
              keberadaan_label  = IF(VALUES(keberadaan_label) IS NOT NULL,
                                     VALUES(keberadaan_label), keberadaan_label),
              gate_label        = IF(VALUES(gate_label) IS NOT NULL,
                                     VALUES(gate_label), gate_label),
              assignment_status = IF(VALUES(assignment_status) IS NOT NULL,
                                     VALUES(assignment_status), assignment_status),
              synced_at         = VALUES(synced_at),
              sync_keterangan   = VALUES(sync_keterangan)
        """, (sls_id, assignment_id, nama or "", skala_usaha or "",
              kode, label, gate_label, assignment_status, synced_at, sync_keterangan))
    conn.commit()


# ── Browser ──────────────────────────────────────────────────────────────────

def _make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )
    return browser, ctx


def login_fasih(ctx):
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)
    try:
        page.goto(f"{FASIH_URL}/oauth2/authorization/ics",
                  wait_until="networkidle", timeout=90_000)
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
    print(f"[FASIH] login OK → {page.url}")
    return page


def _page_fetch(page, url):
    """Fetch satu URL via browser context (bypass F5 bot protection)."""
    try:
        return page.evaluate(f"""async () => {{
            const r = await fetch('{url}', {{credentials:'include'}});
            if (!r.ok) return null;
            return await r.json();
        }}""")
    except Exception:
        return None


def _page_fetch_one(page, url):
    """Fetch SATU URL, sequential (bukan Promise.all/batch) — WAF F5 FASIH
    mendeteksi fetch() konkuren dari page yang sama sebagai bot, meskipun cuma
    5 sekaligus, dan diam-diam membalas HTTP 200 berisi HTML block-page alih-alih
    JSON (bukan 429 — makanya gak ketangkap pengecekan status). Jadi tiap
    assignment diambil satu-satu di sisi Python (lihat REQUEST_DELAY), dan di sini
    responsenya dicek content-type-nya juga, bukan cuma r.ok, supaya block-page
    ketahuan dan di-retry dengan backoff (bukan gagal diam-diam / exception mentah).
    Hasil gagal diisi {"__fetch_error": "<alasan>"} supaya caller bisa log alasannya."""
    try:
        return page.evaluate(f"""async () => {{
            const maxRetries = 5, baseDelay = 1500;
            for (let attempt = 0; attempt <= maxRetries; attempt++) {{
                try {{
                    const r = await fetch('{url}', {{credentials:'include'}});
                    if (r.status === 429) {{
                        if (attempt < maxRetries) {{
                            const retryAfter = parseFloat(r.headers.get('Retry-After'));
                            const jitter = 0.5 + Math.random();
                            const delay = retryAfter > 0 ? retryAfter * 1000 : baseDelay * Math.pow(2, attempt) * jitter;
                            await new Promise(res => setTimeout(res, delay));
                            continue;
                        }}
                        return {{__fetch_error: 'HTTP 429'}};
                    }}
                    if (!r.ok) return {{__fetch_error: 'HTTP ' + r.status}};
                    const ct = r.headers.get('content-type') || '';
                    if (!ct.includes('application/json')) {{
                        if (attempt < maxRetries) {{
                            const jitter = 0.5 + Math.random();
                            await new Promise(res => setTimeout(res, baseDelay * Math.pow(2, attempt) * jitter));
                            continue;
                        }}
                        return {{__fetch_error: 'Blocked (non-JSON, content-type: ' + ct + ')'}};
                    }}
                    return await r.json();
                }} catch (e) {{
                    if (attempt < maxRetries) {{
                        await new Promise(res => setTimeout(res, baseDelay * (0.5 + Math.random())));
                        continue;
                    }}
                    return {{__fetch_error: String(e)}};
                }}
            }}
        }}""")
    except Exception as e:
        return {"__fetch_error": f"fetch exception: {e}"}


# ── FASIH API ─────────────────────────────────────────────────────────────────

def fetch_assignments_per_sls(page, kode_sls, conn):
    """
    Kembalikan list usaha assignments untuk satu SLS.
    Response: {"success":true,"data":[{"assignmentId":...,"data1":nama,"data6":skala_usaha},...]}
    Filter: data6 terisi (UMK/UMKM/UMB/dll) → usaha; kosong → keluarga
    """
    url = (f"{FASIH_URL}/assignment-general/api/assignments"
           f"/get-principal-values-by-smallest-code/{FASIH_PERIOD_ID}/{kode_sls}")
    _fasih_lock(conn)
    try:
        raw = _page_fetch(page, url)
    finally:
        _fasih_unlock(conn)
    if not raw:
        return []
    items = raw.get("data") if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    results = []
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        asgn_id = item.get("assignmentId") or item.get("id") or ""
        if not asgn_id:
            continue
        skala = (item.get("data6") or "").strip()
        results.append({
            "assignment_id": asgn_id,
            "nama":          (item.get("data1") or "").strip(),
            "skala_usaha":   skala,
        })
    return results


#  ada_keluarga : gate keberadaan KELUARGA — jawaban "Tidak Ditemukan (STOP)" kalau
#                 keluarga sudah tidak ada di lokasi.
#  pilih_umkm   : gate keberadaan BANGUNAN/USAHA non-keluarga (bangunan khusus usaha,
#                 kantor, bangunan kosong, dll) — jawabannya cuma "Tidak Ditemukan"
#                 (tanpa "(STOP)" di labelnya, beda dari ada_keluarga).
# Keduanya sama-sama berarti: alur kuesioner berhenti di situ, pertanyaan
# keberadaan_usaha# tidak pernah muncul.
GATE_FIELDS = ("ada_keluarga", "pilih_umkm")


def _parse_assignment(raw_r):
    """
    Parse satu JSON response get-by-assignment-id →
    (kode, label, gate_label, assignment_status).

    kode/label        : jawaban pertanyaan keberadaan_usaha#N (existing behaviour).
    gate_label        : alasan berhenti di pertanyaan gate keluarga/bangunan — lihat
                         GATE_FIELDS. None kalau tidak ada gate-stop.
    assignment_status : assignment_status_alias dari FASIH (OPEN, SUBMITTED BY Pencacah,
                         APPROVED BY Pengawas, dst).
    """
    if not raw_r:
        return None, None, None, None
    data_obj = raw_r.get("data") if isinstance(raw_r, dict) else None
    if not data_obj:
        return None, None, None, None

    assignment_status = data_obj.get("assignment_status_alias")

    data_str = data_obj.get("data") or ""
    if not data_str:
        return None, None, None, assignment_status
    try:
        inner = json.loads(data_str) if isinstance(data_str, str) else data_str
    except Exception:
        return None, None, None, assignment_status

    kode, label, gate_label = None, None, None

    for item in (inner.get("answers") or []):
        if not isinstance(item, dict):
            continue
        key = item.get("dataKey", "") or ""
        ans = item.get("answer")
        if not ans:
            continue

        if key.startswith("keberadaan_usaha#"):
            if isinstance(ans, list) and ans:
                first = ans[0]
                if isinstance(first, dict):
                    label_raw   = (first.get("label") or "").strip()
                    value_raw   = str(first.get("value") or "").strip()
                    label_clean = label_raw.split(". ", 1)[1].strip() if ". " in label_raw else label_raw
                    kode, label = value_raw, label_clean
            elif isinstance(ans, str):
                kode, label = None, ans.strip()
            continue

        # Gate keluarga (ada_keluarga) / gate bangunan-usaha (pilih_umkm) — lihat GATE_FIELDS.
        # Jawabannya bisa "Tidak Ditemukan"/"...(STOP)" ATAU "Baru" (keluarga/bangunan baru
        # di luar prelisting) — keduanya berarti alur berhenti di gate ini, keberadaan_usaha#
        # tidak pernah muncul. Simpan APA ADANYA ke gate_label (bukan kode/label) supaya
        # "Baru" di level keluarga/bangunan tetap terpisah dari "Baru" di level roster usaha
        # (keberadaan_usaha# = Baru) — caller yang membedakan berdasar teksnya.
        if key in GATE_FIELDS and gate_label is None and isinstance(ans, list) and ans:
            first = ans[0]
            if isinstance(first, dict):
                label_raw = (first.get("label") or "").strip()
                gl_lower = label_raw.lower()
                if "(stop)" in gl_lower or "tidak ditemukan" in gl_lower or "baru" in gl_lower:
                    gate_label = label_raw

    return kode, label, gate_label, assignment_status


def fetch_keberadaan_batch(page, assignment_ids, conn):
    """
    Fetch keberadaan untuk banyak assignments SATU-SATU (sequential, lihat
    _page_fetch_one — Promise.all/batch konkuren kena block WAF F5 walau kecil).
    Tiap request dibungkus _fasih_lock/_fasih_unlock supaya proses ini gantian
    (bukan tabrakan) dengan sync_keberadaan_rev.py — WAF FASIH block berdasar IP
    gabungan kedua proses, bukan per-akun/session.
    Return list of (kode, label, gate_label, assignment_status, sync_keterangan) sesuai
    urutan assignment_ids. sync_keterangan None kalau fetch sukses, diisi pesan error
    kalau fetch gagal (beda dari "kode/label None karena memang belum diisi").
    """
    base = f"{FASIH_URL}/app/api/assignment-general/api/assignment/get-by-assignment-id?assignmentId="
    parsed = []
    consecutive_fail = 0
    cooldown_cycles  = 0
    for i, aid in enumerate(assignment_ids):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        _fasih_lock(conn)
        try:
            r = _page_fetch_one(page, base + aid)
        finally:
            _fasih_unlock(conn)
        if isinstance(r, dict) and "__fetch_error" in r:
            print(f"      [FETCH FAIL] {aid[:8]}… : {r['__fetch_error']}", flush=True)
            parsed.append((None, None, None, None, f"Gagal: {r['__fetch_error']}"))
            consecutive_fail += 1
            if consecutive_fail >= COOLDOWN_FAIL_THRESHOLD and cooldown_cycles < COOLDOWN_MAX_CYCLES:
                cooldown_cycles += 1
                wait = COOLDOWN_BASE_SECONDS * cooldown_cycles
                print(f"      [COOLDOWN] {consecutive_fail} gagal berturut-turut (diduga kena "
                      f"block massal) — jeda {wait}s (cycle {cooldown_cycles}/{COOLDOWN_MAX_CYCLES})",
                      flush=True)
                time.sleep(wait)
                consecutive_fail = 0
        else:
            consecutive_fail = 0
            kode, label, gate_label, assignment_status = _parse_assignment(r)
            parsed.append((kode, label, gate_label, assignment_status, None))
    return parsed


# ── Main ─────────────────────────────────────────────────────────────────────

# Sentinel "sudah selesai satu putaran penuh" — dipakai supaya status "selesai"
# beda dari "belum pernah jalan" (dulu keduanya sama-sama None karena barisnya
# dihapus, bikin proses satunya gagal mendeteksi auto-stop kalau dia baru ngecek
# SETELAH baris itu kehapus).
DONE = -1


def _save_progress(conn, sls_index):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sync_progress (job, sls_index) VALUES ('keberadaan', %s)
            ON DUPLICATE KEY UPDATE sls_index = %s
        """, (sls_index, sls_index))
    conn.commit()


def _load_progress(conn):
    """Posisi resume proses INI SENDIRI. DONE dianggap sama dengan "belum ada
    checkpoint" (putaran sebelumnya sudah tuntas, jadi mulai lagi dari awal)."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_progress (
                    job       VARCHAR(50) PRIMARY KEY,
                    sls_index INT NOT NULL DEFAULT 0
                ) ENGINE=InnoDB
            """)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT sls_index FROM sync_progress WHERE job='keberadaan'")
            row = cur.fetchone()
            if not row or row["sls_index"] == DONE:
                return 0
            return row["sls_index"]
    except Exception:
        return 0


def _mark_done(conn):
    """Tandai proses ini selesai satu putaran (natural end atau ketemu proses lawan)
    — TIDAK menghapus baris, supaya proses lawan yang baru cek belakangan tetap
    bisa lihat "sudah selesai", bukan disangka "belum pernah jalan"."""
    _save_progress(conn, DONE)


def _read_frontier(conn, job):
    """Baca posisi sls_index job lain (dipakai buat deteksi auto-stop saat forward &
    reverse ketemu di tengah). None kalau job itu belum pernah jalan sama sekali.
    Bisa juga bernilai DONE kalau job itu sudah menyelesaikan satu putaran penuh —
    caller yang menafsirkan DONE sebagai "berhenti juga, semua area sudah tercover"."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sls_index FROM sync_progress WHERE job=%s", (job,))
            row = cur.fetchone()
            return row["sls_index"] if row else None
    except Exception:
        return None


def run_once():
    synced_at = _now_wita()
    conn      = _connect_db()
    ensure_table(conn)
    sls_map   = load_sls_map(conn)
    sls_list  = list(sls_map.items())
    total_sls = len(sls_list)
    total_chunks = (total_sls + CHUNK_SIZE - 1) // CHUNK_SIZE

    resume_from = _load_progress(conn)
    if resume_from > 0:
        print(f"[{_now_wita()}] Resume dari SLS {resume_from} (chunk {resume_from//CHUNK_SIZE + 1}/{total_chunks})", flush=True)
    else:
        print(f"[{_now_wita()}] Mulai sync keberadaan — {total_sls} SLS, {total_chunks} chunk (@{CHUNK_SIZE} SLS)", flush=True)

    ok          = 0
    null_count  = 0
    total_asgn  = 0

    with sync_playwright() as pw:
        browser, _ = _make_browser(pw)

        for chunk_i, chunk_start in enumerate(range(0, total_sls, CHUNK_SIZE)):
            if chunk_start < resume_from:
                continue

            # Auto-stop kalau sudah ketemu proses REV (dia jalan dari SLS terakhir
            # ke awal) — hindari dua proses balapan memproses ulang area yang sama.
            # rev_frontier == DONE berarti REV sudah menuntaskan satu putaran penuh
            # duluan (sampai SLS 0) — otomatis semua area sudah tercover juga.
            rev_frontier = _read_frontier(conn, "keberadaan_rev")
            if rev_frontier is not None and (rev_frontier == DONE or chunk_start >= rev_frontier):
                alasan = "REV sudah selesai satu putaran penuh" if rev_frontier == DONE else f"REV sudah sampai {rev_frontier}"
                print(f"\n[{_now_wita()}] Ketemu proses REV di SLS {chunk_start} "
                      f"({alasan}) — auto-stop, gabungan sudah cover semua SLS.", flush=True)
                break

            chunk = sls_list[chunk_start : chunk_start + CHUNK_SIZE]
            print(f"\n[chunk {chunk_i+1}/{total_chunks}] Login...", flush=True)

            # Context baru + login ulang tiap chunk
            ctx  = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            page = login_fasih(ctx)

            pending = []  # kumpulkan semua assignment dari 5 SLS

            for j, (kode_sls, sls_id) in enumerate(chunk):
                global_i = chunk_start + j
                items    = fetch_assignments_per_sls(page, kode_sls, conn)
                usaha    = [it for it in items if it.get("assignment_id")]
                for it in usaha:
                    it["sls_id"] = sls_id
                    pending.append(it)
                print(f"  [{global_i}/{total_sls}] {kode_sls} → {len(usaha)} asgn (buffer={len(pending)})", flush=True)

            # Fetch keberadaan satu-satu (sequential) untuk semua pending
            chunk_ok   = 0
            chunk_null = 0
            if pending:
                ids             = [a["assignment_id"] for a in pending]
                keberadaan_list = fetch_keberadaan_batch(page, ids, conn)
                for asgn, (kode, label, gate_label, assignment_status, sync_keterangan) in zip(pending, keberadaan_list):
                    if kode is None and label is None and gate_label is None:
                        null_count += 1
                        chunk_null += 1
                    else:
                        ok       += 1
                        chunk_ok += 1
                    upsert_keberadaan(
                        conn,
                        sls_id            = asgn["sls_id"],
                        assignment_id     = asgn["assignment_id"],
                        nama              = asgn["nama"],
                        skala_usaha       = asgn["skala_usaha"],
                        kode              = kode,
                        label             = label,
                        gate_label        = gate_label,
                        assignment_status = assignment_status,
                        synced_at         = synced_at,
                        sync_keterangan   = sync_keterangan,
                    )
                total_asgn += len(pending)

            print(f"  → simpan {len(pending)} asgn | ok={chunk_ok} null={chunk_null} | total ok={ok}", flush=True)

            try:
                ctx.close()
            except Exception:
                pass

            # Simpan posisi chunk berikutnya ke DB supaya bisa resume kalau restart
            next_start = chunk_start + CHUNK_SIZE
            _save_progress(conn, next_start)

            if next_start < total_sls:
                delay = random.uniform(CHUNK_DELAY_MIN, CHUNK_DELAY_MAX)
                print(f"  [jeda {delay:.1f}s]", flush=True)
                time.sleep(delay)

        browser.close()

    _mark_done(conn)  # bukan hapus baris — biar proses REV yg cek belakangan tetap tahu ini sudah kelar
    conn.close()
    print(f"\n[{_now_wita()}] Selesai: total={total_asgn} ok={ok} null={null_count}", flush=True)


if __name__ == "__main__":
    print("=== sync_keberadaan.py ===")
    try:
        run_once()
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback; traceback.print_exc()
    print(f"[{_now_wita()}] Done.")
