"""
Verifikasi ground-truth SLS yang status OPEN/DRAFT-nya dicurigai basi —
DIPISAH dari sync_fasih.py supaya sync utama (tiap 2 jam) tetap ringan &
cepat, tidak lagi menunggu proses lambat/rate-limited ini di tengah
siklusnya. Jalan independen, jadwal sendiri (tiap 8 jam).

Latar belakang bug: endpoint report-progress-by-responsibility (dipakai
sync_fasih.py) ternyata dibaca dari index pencarian internal FASIH (service
"analytic") yang kadang telat sinkron dari database asli — sebuah assignment
yang di FASIH sendiri sudah "SUBMITTED BY Pencacah" berjam-jam lalu bisa saja
masih kebaca OPEN/DRAFT dari index ini (dikonfirmasi manual dengan
membandingkan panel "Assignment Detail" FASIH vs list Data yang difilter
DRAFT — bug ada di index internal FASIH, bukan di sync).

Sumber yang benar-benar akurat adalah endpoint riwayat per-assignment
(assignment-general/api/assignment-history), tapi itu butuh assignmentId
satu-satu — tidak praktis dipanggil untuk semua ~136rb assignment tiap
siklus. Jadi verifikasi ini dibatasi hanya untuk SLS yang "mencurigakan":
yang di tabel progress kita punya assignment OPEN/DRAFT TAPI juga sudah ada
progres lain di SLS yang sama (submit/approved/dst). SLS yang 100% OPEN
dianggap memang belum disentuh sama sekali dan dilewati (bukan indikasi bug).

Alur:
  1. Query kandidat langsung dari tabel progress+sls (bukan scrape ulang
     FASIH) — sync_fasih.py sudah menjaga tabel ini cukup segar tiap 2 jam.
  2. Utk tiap kandidat, ambil status ground-truth per-assignment & hitung
     ulang agregatnya.
  3. Kalau beda, UPDATE langsung baris progress SLS itu (efek instan, tidak
     perlu menunggu sync_fasih.py jalan lagi) DAN simpan hasilnya ke cache
     (tabel sync_state, job='fasih_verify') — dibaca sync_fasih.py tiap
     siklusnya (apply_verified_cache) supaya koreksi ini tidak ketimpa balik
     oleh aggregate() yang selalu baca ulang data mentah (mungkin masih
     basi) dari FASIH.

Env vars: sama seperti sync_fasih.py (FASIH_USER/PASS, DB_HOST/PORT/USER/PASS/NAME).
"""

import json
import math
import os
import time
from datetime import datetime, timedelta

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

BASE_URL = "https://fasih-sm.bps.go.id"
SURVEY_ID = "a0429e96-51a5-477b-a415-485f9c153004"
PERIOD_ID = "fd68e454-ba45-4b85-8205-f3bf777ded24"
DOMPU_REGION2_ID = "546a26bf-e388-41ab-9083-e02cbbc093d4"

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

# Status yang dihitung sebagai "submit" — sama persis dgn sync_fasih.py,
# disalin (bukan di-import) supaya file ini tetap berdiri sendiri/independen.
SUBMIT_STATUSES = frozenset({
    "SUBMITTED BY Pencacah", "SUBMITTED RESPONDENT",
    "APPROVED BY Pengawas",  "REJECTED BY Pengawas",  "REVOKED BY Pengawas",
    "EDITED BY Admin Kabupaten",   "COMPLETED BY Admin Kabupaten",
    "APPROVED BY Admin Kabupaten", "REJECTED BY Admin Kabupaten",
    "EDITED BY Admin Provinsi",    "COMPLETED BY Admin Provinsi",
    "APPROVED BY Admin Provinsi",  "REJECTED BY Admin Provinsi",
    "EDITED BY Admin Pusat",       "COMPLETED BY Admin Pusat",
    "APPROVED BY Admin Pusat",     "REJECTED BY Admin Pusat",
})

VERIFY_INTERVAL_HOURS = 8    # jadwal normal kalau verifikasi tuntas semua kandidat
RETRY_INTERVAL_HOURS  = 2    # jadwal lebih cepat kalau belum tuntas (budget habis/error)

MAX_VERIFY_CALLS   = 6000   # batas panggilan assignment-history per siklus — dijaga
                             # konservatif krn sesi ini pernah kena bot-detection FASIH
                             # waktu request beruntun terlalu banyak. Backlog besar
                             # dicicil beberapa siklus berturut-turut (RETRY_INTERVAL_HOURS),
                             # bukan nunggu VERIFY_INTERVAL_HOURS penuh per percobaan.
CHUNK_SIZE_VERIFY  = 5      # SLS per chunk sebelum context baru + login ulang
CHUNK_DELAY_VERIFY = 5      # detik istirahat antar chunk
BATCH_SIZE_VERIFY  = 20     # assignment per Promise.all batch (browser-side, jauh lebih cepat dari sequential)

_last_verify_at = None
_verified_cache = {}  # kode_sls -> dict hasil agregat SLS yang sudah diverifikasi


try:
    import zoneinfo
    _wita_tz = zoneinfo.ZoneInfo("Asia/Makassar")
except Exception:
    import datetime as _dt
    _wita_tz = _dt.timezone(_dt.timedelta(hours=8))


def _now_wita():
    return datetime.now(_wita_tz)


def _new_sls_agg():
    return {
        "jumlah_submit": 0, "jumlah_draft": 0, "fasih_open": 0, "fasih_submitted": 0,
        "fasih_approved_pengawas": 0, "fasih_rejected_pengawas": 0, "fasih_revoked_pengawas": 0,
        "fasih_approved_kabupaten": 0, "fasih_rejected_kabupaten": 0,
        "fasih_approved_provinsi": 0, "fasih_rejected_provinsi": 0,
        "fasih_approved_pusat": 0, "fasih_rejected_pusat": 0,
        "fasih_edited_admin": 0, "fasih_completed_admin": 0, "fasih_total": 0,
    }


def apply_status(a, status, cnt):
    """Sama persis dgn apply_status di sync_fasih.py (disalin, bukan
    di-import, supaya file ini independen) — tanpa tracking unknown_statuses
    krn di sini cuma dipakai utk status per-assignment hasil verifikasi."""
    a["fasih_total"] += cnt
    if status in SUBMIT_STATUSES:
        a["jumlah_submit"] += cnt
    if status == "DRAFT":
        a["jumlah_draft"] += cnt
    su = status.upper()
    if status == "OPEN":
        a["fasih_open"] += cnt
    elif status == "DRAFT":
        pass
    elif "SUBMITTED" in su:
        a["fasih_submitted"] += cnt
    elif "PENGAWAS" in su:
        if "APPROVED" in su:
            a["fasih_approved_pengawas"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_pengawas"] += cnt
        elif "REVOKED" in su:
            a["fasih_revoked_pengawas"] += cnt
    elif "KABUPATEN" in su:
        if "APPROVED" in su:
            a["fasih_approved_kabupaten"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_kabupaten"] += cnt
        elif "EDITED" in su:
            a["fasih_edited_admin"] += cnt
        elif "COMPLETED" in su:
            a["fasih_completed_admin"] += cnt
    elif "PROVINSI" in su:
        if "APPROVED" in su:
            a["fasih_approved_provinsi"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_provinsi"] += cnt
        elif "EDITED" in su:
            a["fasih_edited_admin"] += cnt
        elif "COMPLETED" in su:
            a["fasih_completed_admin"] += cnt
    elif "PUSAT" in su:
        if "APPROVED" in su:
            a["fasih_approved_pusat"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_pusat"] += cnt
        elif "EDITED" in su:
            a["fasih_edited_admin"] += cnt
        elif "COMPLETED" in su:
            a["fasih_completed_admin"] += cnt


# ── Browser & login (disalin dari sync_fasih.py — file ini independen) ─────

def _make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--no-sandbox"],
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720},
    )
    return browser, ctx


def login(ctx):
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)
    LONG = 90_000
    print("[LOGIN] Membuka halaman challenge...", flush=True)
    try:
        page.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    except Exception:
        pass
    active = page
    if page.url in ("about:blank", ""):
        active = ctx.new_page()
        _stealth.apply_stealth_sync(active)
    active.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    active.wait_for_selector("#kc-form-login", timeout=LONG)
    active.fill("#username", FASIH_USER)
    active.fill("#password", FASIH_PASS)
    active.click("#kc-login")
    active.wait_for_url("**fasih-sm.bps.go.id**", timeout=LONG)
    cookies = ctx.cookies()
    xsrf = next((c["value"] for c in cookies if c["name"] == "XSRF-TOKEN"), "")
    if not xsrf:
        raise RuntimeError("Login gagal – tidak ada XSRF token")
    print("[LOGIN] Berhasil.", flush=True)
    return active, xsrf


# ── State (sync_state.job='fasih_verify' — sama persis dgn yang dibaca
#    apply_verified_cache() di sync_fasih.py) ────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4", ssl={"ssl": False},
    )


def _ensure_sync_state_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                job        VARCHAR(50) PRIMARY KEY,
                state_json LONGTEXT NOT NULL,
                updated_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def save_verify_state():
    try:
        conn = _connect_db()
        _ensure_sync_state_table(conn)
        payload = json.dumps({
            "last_verify_at": _last_verify_at.strftime("%Y-%m-%d %H:%M:%S") if _last_verify_at else None,
            "cache": _verified_cache,
        })
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sync_state (job, state_json, updated_at)
                VALUES ('fasih_verify', %s, NOW())
                ON DUPLICATE KEY UPDATE state_json = VALUES(state_json), updated_at = NOW()
            """, (payload,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[VERIFY-STATE] gagal simpan state ke DB: {e}", flush=True)


def load_verify_state():
    global _last_verify_at, _verified_cache
    try:
        conn = _connect_db()
        _ensure_sync_state_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT state_json FROM sync_state WHERE job = 'fasih_verify'")
            row = cur.fetchone()
        conn.close()
        if not row:
            print("[VERIFY-STATE] belum ada state tersimpan di DB, mulai dari awal", flush=True)
            return
        data = json.loads(row[0])
        lva = data.get("last_verify_at")
        if lva:
            _last_verify_at = datetime.strptime(lva, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_wita_tz)
        _verified_cache = data.get("cache") or {}
        print(f"[VERIFY-STATE] dimuat dari DB: last_verify_at={_last_verify_at}, {len(_verified_cache)} SLS di cache", flush=True)
    except Exception as e:
        print(f"[VERIFY-STATE] gagal muat state dari DB ({e}), mulai dari awal", flush=True)


def _should_verify_now():
    if _last_verify_at is None:
        return True
    return (_now_wita() - _last_verify_at) >= timedelta(hours=VERIFY_INTERVAL_HOURS)


# ── Kandidat (langsung dari tabel progress+sls, bukan scrape ulang FASIH) ──

def query_candidates():
    """SLS yang jumlah_draft>0, atau ada fasih_open TAPI SLS itu juga sudah
    punya progres lain (submit/approved/dst) — indikasi status OPEN/DRAFT-nya
    basi. SLS yang 100% OPEN (belum disentuh sama sekali) dilewati."""
    conn = _connect_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.id, s.kode_sls,
                   p.jumlah_submit, p.jumlah_draft, p.fasih_open, p.fasih_submitted,
                   p.fasih_approved_pengawas, p.fasih_rejected_pengawas, p.fasih_revoked_pengawas,
                   p.fasih_approved_kabupaten, p.fasih_rejected_kabupaten,
                   p.fasih_approved_provinsi, p.fasih_rejected_provinsi,
                   p.fasih_approved_pusat, p.fasih_rejected_pusat,
                   p.fasih_edited_admin, p.fasih_completed_admin, p.fasih_total
            FROM sls s
            JOIN progress p ON p.sls_id = s.id
            WHERE p.jumlah_draft > 0
               OR (p.fasih_open > 0 AND (p.fasih_total - p.fasih_open - p.jumlah_draft) > 0)
        """)
        rows = cur.fetchall()
    conn.close()

    candidates = {}
    for row in rows:
        (sls_id, kode, jumlah_submit, jumlah_draft, fasih_open, fasih_submitted,
         appr_peng, rej_peng, rev_peng, appr_kab, rej_kab, appr_prov, rej_prov,
         appr_pusat, rej_pusat, edited_admin, completed_admin, fasih_total) = row
        candidates[kode] = {
            "sls_id": sls_id,
            "jumlah_submit": jumlah_submit, "jumlah_draft": jumlah_draft,
            "fasih_open": fasih_open, "fasih_submitted": fasih_submitted,
            "fasih_approved_pengawas": appr_peng, "fasih_rejected_pengawas": rej_peng,
            "fasih_revoked_pengawas": rev_peng,
            "fasih_approved_kabupaten": appr_kab, "fasih_rejected_kabupaten": rej_kab,
            "fasih_approved_provinsi": appr_prov, "fasih_rejected_provinsi": rej_prov,
            "fasih_approved_pusat": appr_pusat, "fasih_rejected_pusat": rej_pusat,
            "fasih_edited_admin": edited_admin, "fasih_completed_admin": completed_admin,
            "fasih_total": fasih_total,
        }
    return candidates


def list_sls_assignments(ctx, xsrf, kode_sls, retries=2):
    """Ambil semua assignment record (mentah, per-record) untuk satu kode_sls
    lewat analytic/api/v2/assignment/datatable-all-user-survey-periode.
    Return None kalau gagal total (caller harus skip, bukan anggap 0)."""
    payload = {
        "draw": 1,
        "columns": [{"data": c, "name": "", "searchable": True,
                     "orderable": c not in ("id", "codeIdentity"),
                     "search": {"value": "", "regex": False}}
                    for c in ["id", "codeIdentity", "data1", "data2", "data3", "data4",
                              "data5", "data6", "data7", "data8", "data9"]],
        "order": [{"column": 0, "dir": "asc"}],
        "start": 0, "length": 1000,
        "search": {"value": kode_sls, "regex": False},
        "assignmentExtraParam": {
            "region1Id": None, "region2Id": DOMPU_REGION2_ID, "region3Id": None, "region4Id": None,
            "region5Id": None, "region6Id": None, "region7Id": None, "region8Id": None,
            "region9Id": None, "region10Id": None,
            "surveyPeriodId": PERIOD_ID, "assignmentErrorStatusType": -1,
            "assignmentStatusAlias": None,
            "data1": None, "data2": None, "data3": None, "data4": None, "data5": None,
            "data6": None, "data7": None, "data8": None, "data9": None, "data10": None,
            "userIdResponsibility": None, "currentUserId": None, "regionId": None,
            "filterTargetType": "TARGET_ONLY",
        },
    }
    hdrs = {
        "Accept": "application/json, */*", "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Referer": f"{BASE_URL}/survey-collection/collect/{SURVEY_ID}", "Origin": BASE_URL,
    }
    for attempt in range(1, retries + 1):
        try:
            r = ctx.request.post(
                f"{BASE_URL}/analytic/api/v2/assignment/datatable-all-user-survey-periode",
                data=json.dumps(payload), headers=hdrs, timeout=25000,
            )
            if r.status != 200:
                return None
            d = r.json()
            records = d.get("searchData", []) or []
            return [rec for rec in records if str(rec.get("codeIdentity", "")).startswith(kode_sls)]
        except Exception as e:
            print(f"    [VERIFY] list SLS {kode_sls} lambat/gagal (percobaan {attempt}/{retries}): {e}", flush=True)
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


def _verify_fetch_batch(page, urls):
    """Fetch banyak assignment-history sekaligus via Promise.all (fetch di sisi
    browser, bukan ctx.request Python) — retry otomatis kalau kena HTTP 429
    dengan hormat header Retry-After, backoff eksponensial kalau tidak ada
    header itu. Pola identik dgn _page_fetch_batch di sync_keberadaan.py yang
    sudah terbukti aman dari rate limit FASIH."""
    urls_js = json.dumps(urls)
    try:
        return page.evaluate(f"""async () => {{
            const urls = {urls_js};
            async function fetchWithRetry(u, maxRetries=4, baseDelay=2000) {{
                for (let attempt = 0; attempt <= maxRetries; attempt++) {{
                    try {{
                        const r = await fetch(u, {{credentials:'include'}});
                        if (r.ok) return await r.json();
                        if (r.status === 429 && attempt < maxRetries) {{
                            const retryAfter = parseFloat(r.headers.get('Retry-After'));
                            const delay = retryAfter > 0 ? retryAfter * 1000 : baseDelay * Math.pow(2, attempt);
                            await new Promise(res => setTimeout(res, delay));
                            continue;
                        }}
                        return {{__fetch_error: 'HTTP ' + r.status}};
                    }} catch (e) {{
                        if (attempt < maxRetries) {{
                            await new Promise(res => setTimeout(res, baseDelay));
                            continue;
                        }}
                        return {{__fetch_error: String(e)}};
                    }}
                }}
            }}
            return await Promise.all(urls.map(u => fetchWithRetry(u)));
        }}""")
    except Exception as e:
        return [{"__fetch_error": f"batch exception: {e}"}] * len(urls)


def _true_status_from_history(raw):
    """Ekstrak status_alias terkini dari response assignment-history. None kalau
    gagal/kosong (caller fallback ke status lama, bukan anggap 0)."""
    if not isinstance(raw, dict) or "__fetch_error" in raw:
        return None
    events = raw.get("data") or []
    if not events:
        return None
    latest = max(events, key=lambda e: e.get("date_created") or "")
    alias = latest.get("status_alias") or ""
    if alias.startswith("ASSIGNED TO"):
        return "OPEN"
    return alias


def update_progress_row(sls_id, a):
    """Tulis langsung hasil agregat ground-truth ke baris progress SLS ini —
    efeknya instan, tidak perlu menunggu sync_fasih.py jalan lagi."""
    conn = _connect_db()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE progress
            SET jumlah_submit = %s, jumlah_draft = %s, fasih_open = %s, fasih_submitted = %s,
                fasih_approved_pengawas = %s, fasih_rejected_pengawas = %s, fasih_revoked_pengawas = %s,
                fasih_approved_kabupaten = %s, fasih_rejected_kabupaten = %s,
                fasih_approved_provinsi = %s, fasih_rejected_provinsi = %s,
                fasih_approved_pusat = %s, fasih_rejected_pusat = %s,
                fasih_edited_admin = %s, fasih_completed_admin = %s,
                fasih_total = %s, updated_at = NOW()
            WHERE sls_id = %s
        """, (
            a["jumlah_submit"], a["jumlah_draft"], a["fasih_open"], a["fasih_submitted"],
            a["fasih_approved_pengawas"], a["fasih_rejected_pengawas"], a["fasih_revoked_pengawas"],
            a["fasih_approved_kabupaten"], a["fasih_rejected_kabupaten"],
            a["fasih_approved_provinsi"], a["fasih_rejected_provinsi"],
            a["fasih_approved_pusat"], a["fasih_rejected_pusat"],
            a["fasih_edited_admin"], a["fasih_completed_admin"],
            a["fasih_total"], sls_id,
        ))
    conn.commit()
    conn.close()


def verify_stale_sls(candidates, browser):
    """Utk tiap SLS kandidat, cek ulang status per-assignment ke sumber
    ground truth dan timpa baris progress-nya kalau ternyata beda.

    Diproses per-chunk (CHUNK_SIZE_VERIFY SLS, context+login baru tiap chunk,
    jeda antar chunk) dan pakai fetch batch (Promise.all + retry-on-429) di
    sisi browser — pola yang sama dipakai sync_keberadaan.py, sudah terbukti
    aman dari rate limit FASIH."""
    kodes = list(candidates.keys())
    total = len(kodes)
    print(f"[VERIFY] {total} SLS kandidat perlu verifikasi ground-truth...", flush=True)
    if not candidates:
        return True

    calls = 0
    corrected = 0
    list_failed = 0
    budget_hit = False
    total_chunks = math.ceil(total / CHUNK_SIZE_VERIFY)

    for chunk_start in range(0, total, CHUNK_SIZE_VERIFY):
        if budget_hit:
            break
        chunk = kodes[chunk_start:chunk_start + CHUNK_SIZE_VERIFY]
        chunk_no = chunk_start // CHUNK_SIZE_VERIFY + 1
        print(f"  [VERIFY] chunk {chunk_no}/{total_chunks} ({len(chunk)} SLS, {calls} panggilan sejauh ini) — login ulang...", flush=True)

        page = xsrf = None
        for login_attempt in range(1, 3):
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
            )
            try:
                page, xsrf = login(ctx)
                break
            except Exception as e:
                print(f"  [VERIFY] login chunk {chunk_no} gagal (percobaan {login_attempt}/2): {e}", flush=True)
                try:
                    ctx.close()
                except Exception:
                    pass
                if login_attempt < 2:
                    time.sleep(CHUNK_DELAY_VERIFY)

        if page is None:
            print(f"  [VERIFY] chunk {chunk_no} dilewati (login gagal 2x), {len(chunk)} SLS dicoba lagi siklus berikutnya", flush=True)
            time.sleep(CHUNK_DELAY_VERIFY)
            continue

        for kode in chunk:
            records = list_sls_assignments(ctx, xsrf, kode)
            if records is None:
                list_failed += 1
                continue  # gagal ambil, biarkan data lama (fail-safe, jangan dianggap 0)

            suspect = [r for r in records if r.get("assignmentStatusAlias") in ("OPEN", "DRAFT")]
            trusted = [r for r in records if r.get("assignmentStatusAlias") not in ("OPEN", "DRAFT")]
            if not suspect:
                continue

            remaining_budget = MAX_VERIFY_CALLS - calls
            if remaining_budget <= 0:
                budget_hit = True
                break
            to_check = suspect[:remaining_budget]

            new_a = _new_sls_agg()
            for r in trusted:
                apply_status(new_a, r.get("assignmentStatusAlias", ""), 1)

            for bstart in range(0, len(to_check), BATCH_SIZE_VERIFY):
                batch = to_check[bstart:bstart + BATCH_SIZE_VERIFY]
                urls = [f"{BASE_URL}/assignment-general/api/assignment-history/get-by-assignment-id?assignmentId={r.get('id')}"
                        for r in batch]
                raws = _verify_fetch_batch(page, urls)
                calls += len(batch)
                for r, raw in zip(batch, raws):
                    true_status = _true_status_from_history(raw)
                    if true_status is None:
                        true_status = r.get("assignmentStatusAlias", "")  # gagal verif -> fallback ke status lama
                    apply_status(new_a, true_status, 1)

            if len(to_check) < len(suspect):
                # sisa suspect di SLS ini belum sempat dicek (budget habis di tengah) —
                # jangan timpa data lama dgn hasil separuh, lebih baik dicoba utuh nanti.
                continue

            old = candidates[kode]
            if new_a["jumlah_draft"] != old["jumlah_draft"] or new_a["fasih_open"] != old["fasih_open"]:
                corrected += 1
                print(f"    [VERIFY] {kode}: draft {old['jumlah_draft']}->{new_a['jumlah_draft']}  open {old['fasih_open']}->{new_a['fasih_open']}", flush=True)
            update_progress_row(old["sls_id"], new_a)
            _verified_cache[kode] = dict(new_a)  # dibaca sync_fasih.py tiap siklus (apply_verified_cache)

        try:
            ctx.close()
        except Exception:
            pass

        # Simpan progress per-chunk (bukan cuma di akhir) — supaya kalau proses
        # ini di-restart di TENGAH verifikasi, chunk yang sudah kelar tidak
        # hilang & tidak perlu diulang dari SLS pertama lagi.
        save_verify_state()

        if budget_hit:
            sisa = total - chunk_start - len(chunk)
            print(f"  [VERIFY] budget {MAX_VERIFY_CALLS} panggilan habis, sisa {sisa} SLS dilanjut siklus berikutnya ({RETRY_INTERVAL_HOURS} jam lagi, bukan {VERIFY_INTERVAL_HOURS} jam — belum benar-benar selesai)", flush=True)
            break
        if chunk_start + CHUNK_SIZE_VERIFY < total:
            time.sleep(CHUNK_DELAY_VERIFY)

    print(f"[VERIFY] {'selesai penuh' if not budget_hit else 'BELUM tuntas (kepotong budget)'}. {calls} panggilan assignment-history, {corrected} SLS terkoreksi, {list_failed} SLS gagal diambil listnya.", flush=True)
    return not budget_hit


def run_once():
    """Return ("skipped"|"done"|"partial", next_run_hint_or_None) — dipakai
    main loop utk menentukan jeda sebelum siklus berikutnya."""
    global _last_verify_at

    load_verify_state()
    if not _should_verify_now():
        nxt = _last_verify_at + timedelta(hours=VERIFY_INTERVAL_HOURS)
        print(f"[VERIFY] Lewati (terakhir {_last_verify_at:%d/%m %H:%M} WITA, jadwal berikutnya {nxt:%d/%m %H:%M} WITA)", flush=True)
        return "skipped", nxt

    print("="*50)
    print(f"VERIFIKASI GROUND-TRUTH FASIH (OPEN/DRAFT basi)  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]")
    print("="*50)

    candidates = query_candidates()
    if not candidates:
        print("[VERIFY] tidak ada kandidat — semua SLS bersih.", flush=True)
        _last_verify_at = _now_wita()
        save_verify_state()
        return "done", None

    with sync_playwright() as pw:
        browser, _ = _make_browser(pw)
        try:
            completed = verify_stale_sls(candidates, browser)
        finally:
            browser.close()

    if completed:
        _last_verify_at = _now_wita()
        print(f"[VERIFY] tuntas semua kandidat — jadwal verifikasi berikutnya {VERIFY_INTERVAL_HOURS} jam lagi.", flush=True)
    else:
        print(f"[VERIFY] belum tuntas, dilanjut {RETRY_INTERVAL_HOURS} jam lagi (bukan {VERIFY_INTERVAL_HOURS} jam).", flush=True)
    save_verify_state()
    return ("done" if completed else "partial"), None


if __name__ == "__main__":
    while True:
        try:
            status, nxt_hint = run_once()
        except Exception as e:
            print(f"[ERROR] Verifikasi gagal: {e}", flush=True)
            status, nxt_hint = "partial", None

        if status == "skipped" and nxt_hint:
            secs = max(0, (nxt_hint - _now_wita()).total_seconds())
        elif status == "done":
            secs = VERIFY_INTERVAL_HOURS * 3600
        else:  # partial / error
            secs = RETRY_INTERVAL_HOURS * 3600

        nxt = _now_wita() + timedelta(seconds=secs)
        print(f"[SCHEDULER] Verifikasi berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} ({secs/3600:.1f} jam lagi)", flush=True)
        time.sleep(secs)
