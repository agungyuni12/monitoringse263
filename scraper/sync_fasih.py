"""
Sync data FASIH → database se2026 (tabel progress)

Endpoint: /analytic/api/v2/assignment/report-progress-by-responsibility
Strategi: paginate 235 pencacah Dompu (5 halaman), aggregate per sub-SLS (16-digit),
          upsert ke tabel progress berdasarkan kode_sls.

Env vars:
  FASIH_USER    (default: agung.yuniarta)
  FASIH_PASS    (default: kelayu1998)
  DB_HOST       (default: 127.0.0.1)
  DB_PORT       (default: 3306)
  DB_USER       (default: root)
  DB_PASS       (default: kelayu1998)
  DB_NAME       (default: se2026)
"""

import os, json, requests, math, time, sys
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from collections import defaultdict
import pymysql
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
_stealth = Stealth(navigator_webdriver=True)

# === KONFIGURASI ===
FASIH_USER = os.getenv("FASIH_USER", "agung.yuniarta")
FASIH_PASS = os.getenv("FASIH_PASS", "kelayu1998")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

BASE_URL  = "https://fasih-sm.bps.go.id"
SURVEY_ID = "a0429e96-51a5-477b-a415-485f9c153004"
PERIOD_ID = "fd68e454-ba45-4b85-8205-f3bf777ded24"

PENCACAH_ROLE_ID = "6d7d919a-45e5-4779-bb87-2905b49fd31a"
DOMPU_REGION2_ID = "546a26bf-e388-41ab-9083-e02cbbc093d4"

# Status yang dihitung sebagai "submit"
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

PAGE_SIZE = 10    # server membatasi max 10 per halaman
DELAY     = 0.3   # detik jeda antar halaman


HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

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
    """Login lewat browser, return (page, xsrf). ctx tetap hidup untuk API calls;
    page dikembalikan juga supaya caller bisa pakai page.evaluate (fetch batch)."""
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)

    LONG = 90_000   # 90 detik untuk koneksi lambat

    print("[LOGIN] Membuka halaman challenge...", flush=True)
    try:
        page.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    except Exception:
        pass
    print(f"[LOGIN] URL setelah challenge: {page.url}", flush=True)

    active = page
    if page.url in ("about:blank", ""):
        print("[LOGIN] Membuka page baru setelah challenge...", flush=True)
        active = ctx.new_page()
        _stealth.apply_stealth_sync(active)

    print("[LOGIN] Navigasi ulang ke SSO...", flush=True)
    active.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    print(f"[LOGIN] URL: {active.url}", flush=True)

    active.wait_for_selector("#kc-form-login", timeout=LONG)
    print("[LOGIN] Form login ditemukan.", flush=True)
    active.fill("#username", FASIH_USER)
    active.fill("#password", FASIH_PASS)
    active.click("#kc-login")
    active.wait_for_url("**fasih-sm.bps.go.id**", timeout=LONG)
    print(f"[LOGIN] Redirect ke: {active.url}", flush=True)

    cookies = ctx.cookies()
    xsrf = next((c["value"] for c in cookies if c["name"] == "XSRF-TOKEN"), "")
    if not xsrf:
        raise RuntimeError("Login gagal – tidak ada XSRF token")
    print("[LOGIN] Berhasil.", flush=True)
    return active, xsrf


def fetch_page(ctx, xsrf, page_num, retries=3):
    payload = {
        "surveyPeriodId": PERIOD_ID,
        "surveyRoleId":   PENCACAH_ROLE_ID,
        "size":   PAGE_SIZE,
        "page":   page_num,
        "search": "",
        "target": "TARGET_ONLY",
        "region": {
            "region1Id": None, "region2Id": DOMPU_REGION2_ID,
            "region3Id": None, "region4Id": None, "region5Id": None,
            "region6Id": None, "region7Id": None, "region8Id": None,
            "region9Id": None, "region10Id": None,
        },
        "regionSummaryLevel": 6,
    }
    hdrs = {
        "Accept": "application/json, */*",
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Referer": f"{BASE_URL}/app/surveys/{SURVEY_ID}/{PERIOD_ID}",
        "Origin": BASE_URL,
    }
    for attempt in range(1, retries + 1):
        try:
            r = ctx.request.post(
                f"{BASE_URL}/analytic/api/v2/assignment/report-progress-by-responsibility",
                data=json.dumps(payload),
                headers=hdrs,
                timeout=90000,
            )
            if r.status != 200:
                print(f"  [WARN] page {page_num}: HTTP {r.status}", flush=True)
                return [], 0
            d = r.json()
            if not d.get("success"):
                print(f"  [WARN] page {page_num} error: {d}", flush=True)
                return [], 0
            inner = d.get("data", {})
            total = inner.get("totalElements") or 0
            return inner.get("content", []), total
        except Exception as e:
            print(f"  [RETRY {attempt}/{retries}] page {page_num}: {e}", flush=True)
            if attempt < retries:
                time.sleep(5 * attempt)
    return [], 0


def scrape_all(ctx, xsrf):
    print("[SCRAPE] Mengambil halaman 1...", flush=True)
    content0, total = fetch_page(ctx, xsrf, 0)
    if not total and not content0:
        raise RuntimeError("Tidak ada data dari FASIH")
    if not total:
        total = len(content0)

    pages = max(1, math.ceil(total / PAGE_SIZE))
    print(f"[SCRAPE] Total pencacah: {total} | Halaman: {pages}", flush=True)

    all_content = list(content0)
    for pg in range(1, pages):
        print(f"  Halaman {pg+1}/{pages}...", flush=True)
        c, _ = fetch_page(ctx, xsrf, pg)
        all_content.extend(c)
        time.sleep(DELAY)

    print(f"[SCRAPE] Total pencacah diambil: {len(all_content)}", flush=True)
    return all_content


def _new_sls_agg():
    return {
        "jumlah_submit":           0,
        "jumlah_draft":            0,
        "fasih_open":              0,
        "fasih_submitted":         0,
        "fasih_approved_pengawas": 0,
        "fasih_rejected_pengawas": 0,
        "fasih_revoked_pengawas":  0,
        "fasih_approved_kabupaten":0,
        "fasih_rejected_kabupaten":0,
        "fasih_approved_provinsi": 0,
        "fasih_rejected_provinsi": 0,
        "fasih_approved_pusat":    0,
        "fasih_rejected_pusat":    0,
        "fasih_edited_admin":      0,  # "EDITED BY Admin ..." — digabung semua level (Kab/Prov/Pusat)
        "fasih_completed_admin":  0,  # "COMPLETED BY Admin ..." — digabung semua level
        "fasih_total":             0,
    }


def apply_status(a, status, cnt, unknown_statuses=None):
    """Tambahkan `cnt` assignment berstatus `status` ke bucket agregat SLS `a`.
    Dipakai baik oleh aggregate() (dari statusBreakdown per pencacah) maupun
    verify_stale_sls() (dari status per-assignment hasil verifikasi ground truth)
    supaya logika klasifikasinya konsisten di kedua jalur."""
    a["fasih_total"] += cnt
    if status in SUBMIT_STATUSES:
        a["jumlah_submit"] += cnt
    if status == "DRAFT":
        a["jumlah_draft"] += cnt
    su = status.upper()
    known_bucket = True
    if status == "OPEN":
        a["fasih_open"] += cnt
    elif status == "DRAFT":
        pass  # sudah dihitung di jumlah_draft di atas
    elif "SUBMITTED" in su:
        a["fasih_submitted"] += cnt
    elif "PENGAWAS" in su:
        if "APPROVED" in su:
            a["fasih_approved_pengawas"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_pengawas"] += cnt
        elif "REVOKED" in su:
            a["fasih_revoked_pengawas"] += cnt
        else:
            known_bucket = False
    elif "KABUPATEN" in su:
        if "APPROVED" in su:
            a["fasih_approved_kabupaten"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_kabupaten"] += cnt
        elif "EDITED" in su:
            a["fasih_edited_admin"] += cnt
        elif "COMPLETED" in su:
            a["fasih_completed_admin"] += cnt
        else:
            known_bucket = False
    elif "PROVINSI" in su:
        if "APPROVED" in su:
            a["fasih_approved_provinsi"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_provinsi"] += cnt
        elif "EDITED" in su:
            a["fasih_edited_admin"] += cnt
        elif "COMPLETED" in su:
            a["fasih_completed_admin"] += cnt
        else:
            known_bucket = False
    elif "PUSAT" in su:
        if "APPROVED" in su:
            a["fasih_approved_pusat"] += cnt
        elif "REJECTED" in su:
            a["fasih_rejected_pusat"] += cnt
        elif "EDITED" in su:
            a["fasih_edited_admin"] += cnt
        elif "COMPLETED" in su:
            a["fasih_completed_admin"] += cnt
        else:
            known_bucket = False
    else:
        known_bucket = False
    # Status yang tidak masuk bucket manapun DAN tidak ada di
    # SUBMIT_STATUSES berarti benar-benar belum dikenali sistem ini
    # (bukan sekadar "EDITED"/"COMPLETED" yang sudah masuk
    # SUBMIT_STATUSES tapi memang tidak perlu bucket approved/
    # rejected tersendiri) — kejadian nyata: "EDITED BY Admin
    # Kabupaten" & "COMPLETED BY Admin Kabupaten" sempat tidak
    # dihitung selama beberapa waktu sebelum status ini diketahui.
    if not known_bucket and status not in SUBMIT_STATUSES and unknown_statuses is not None:
        unknown_statuses[status] += cnt


def aggregate(all_content):
    """Aggregate status per kode_sls (16-digit regionCode).

    seen_user_ids mencegah satu pencacah dihitung dua kali — endpoint
    report-progress-by-responsibility ini tidak selalu stabil urutannya,
    jadi kalau ada aktivitas live (mis. Pengawas approve) selagi puluhan
    halaman ini di-scrape, satu pencacah bisa "geser" posisi dan muncul lagi
    di halaman lain, bikin regionSummary-nya (dan SLS yang dia pegang)
    ke-agregat dobel. Dikonfirmasi nyata: dibandingkan 2 dump DB berjarak
    ~20 jam, 313 dari 1659 SLS punya fasih_total persis 2x lipat — terlalu
    presisi buat pertumbuhan data asli.
    """
    sls_agg = defaultdict(_new_sls_agg)
    unknown_statuses = defaultdict(int)
    seen_user_ids = set()
    dup_count = 0

    for pencacah in all_content:
        user_id = pencacah.get("userId")
        if user_id:
            if user_id in seen_user_ids:
                dup_count += 1
                continue
            seen_user_ids.add(user_id)
        for rs in pencacah.get("regionSummary", []):
            kode = rs.get("regionCode", "")
            if not kode or not kode.startswith("5205"):
                continue
            a = sls_agg[kode]
            for sb in rs.get("statusBreakdown", []):
                status = sb.get("status", "")
                cnt    = int(sb.get("count", 0))
                apply_status(a, status, cnt, unknown_statuses)

    if dup_count:
        print(f"[AGGREGATE] {dup_count} pencacah duplikat (muncul di >1 halaman) dilewati", flush=True)
    print(f"[AGGREGATE] SLS unik: {len(sls_agg)}", flush=True)
    if unknown_statuses:
        print(f"[AGGREGATE] PERINGATAN: status belum dikenali (tidak masuk hitungan submit/approved): {dict(unknown_statuses)}", flush=True)
    return sls_agg


# === VERIFIKASI GROUND TRUTH (mengatasi status OPEN/DRAFT basi) ===
#
# report-progress-by-responsibility (dipakai scrape_all/aggregate di atas)
# ternyata dibaca dari index pencarian internal FASIH (service "analytic")
# yang kadang telat sinkron dari database asli — sebuah assignment yang di
# FASIH sendiri sudah "SUBMITTED BY Pencacah" berjam-jam lalu bisa saja
# masih kebaca OPEN/DRAFT dari index ini (dikonfirmasi manual by memandingkan
# panel "Assignment Detail" FASIH vs list Data yang difilter DRAFT — bug ada
# di index internal FASIH, bukan cuma di sync ini).
#
# Sumber yang benar-benar akurat adalah endpoint riwayat per-assignment
# (assignment-general/api/assignment-history), tapi itu butuh assignmentId
# satu-satu — tidak praktis dipanggil untuk semua ~136rb assignment tiap
# siklus sync. Jadi verifikasi ini dibatasi hanya untuk SLS yang "mencurigakan":
# yang punya assignment OPEN/DRAFT TAPI juga sudah ada progres lain di SLS
# yang sama (submit/approved/dst). SLS yang 100% OPEN dianggap memang belum
# disentuh sama sekali dan dilewati (bukan indikasi bug).
MAX_VERIFY_CALLS      = 6000  # batas panggilan per siklus — dijaga konservatif (bukan dipaksa habis
                               # sekali jalan) karena sesi ini pernah kena bot-detection FASIH waktu
                               # request beruntun terlalu banyak. Backlog besar (mis. 710 SLS di awal)
                               # otomatis dicicil beberapa siklus 2-jam berturut-turut, bukan nunggu 6
                               # jam per percobaan — itu sudah dibenerin lewat flag `completed`.
VERIFY_INTERVAL_HOURS = 8     # verifikasi ground-truth cukup tiap 8 jam, bukan tiap siklus sync (2 jam) — proses ini lambat

# Pola chunk + login ulang + fetch batch ini disalin dari sync_keberadaan.py
# (sudah terbukti aman dari rate-limit FASIH — dulu dua proses sync_keberadaan
# jalan bersamaan sempat kena HTTP 429 sebelum pola retry-with-backoff ini ada).
CHUNK_SIZE_VERIFY  = 5    # SLS per chunk sebelum context baru + login ulang
CHUNK_DELAY_VERIFY = 5    # detik istirahat antar chunk
BATCH_SIZE_VERIFY  = 20   # assignment per Promise.all batch (browser-side, jauh lebih cepat dari sequential)

_last_verify_at = None  # diisi run_once(); reset ke None kalau proses restart (verifikasi akan jalan lagi di siklus pertama)

# Hasil verifikasi ground-truth per kode_sls, dipakai ulang di siklus 2-jam yang
# TIDAK menjalankan verifikasi — supaya koreksinya tidak tertimpa balik oleh
# aggregate() yang tiap siklus selalu baca ulang data mentah (mungkin masih basi)
# dari FASIH. Cache dianggap masih berlaku selama fasih_total SLS itu belum
# berubah (set assignment-nya sama, cuma status yang tadinya lag); kalau
# fasih_total berubah berarti ada perubahan nyata → cache dibuang, SLS itu
# otomatis jadi kandidat verifikasi lagi di siklus verifikasi berikutnya.
_verified_cache = {}  # kode_sls -> dict hasil agregat SLS yang sudah diverifikasi


def _should_verify_now():
    if _last_verify_at is None:
        return True
    return (_now_wita() - _last_verify_at) >= timedelta(hours=VERIFY_INTERVAL_HOURS)


def apply_verified_cache(sls_agg):
    """Timpa balik SLS di sls_agg dengan hasil verifikasi sebelumnya yang masih
    berlaku (fasih_total belum berubah). Dipanggil TIAP siklus, verifikasi atau
    bukan, supaya koreksi ground-truth tidak hilang di siklus yang skip STEP 2b."""
    applied = 0
    stale_keys = []
    for kode, cached in _verified_cache.items():
        fresh = sls_agg.get(kode)
        if fresh is None:
            continue
        if fresh["fasih_total"] != cached["fasih_total"]:
            stale_keys.append(kode)  # assignment di SLS ini berubah, cache tidak berlaku lagi
            continue
        if fresh != cached:
            sls_agg[kode] = dict(cached)
            applied += 1
    for kode in stale_keys:
        del _verified_cache[kode]
    if applied or stale_keys:
        print(f"[VERIFY-CACHE] {applied} SLS pakai hasil verifikasi sebelumnya, {len(stale_keys)} cache dibuang (assignment berubah)", flush=True)
    return sls_agg


# _last_verify_at & _verified_cache disimpan ke DB (bukan cuma di memori) supaya
# selamat dari redeploy — proses ini masih sering di-restart selama tahap
# testing/iterasi, dan tanpa persist ini tiap redeploy akan memicu full
# verifikasi ulang (bukan salah, cuma boros & lambat kalau redeploy-nya sering).
def _connect_state_db():
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
        conn = _connect_state_db()
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
    """Dipanggil sekali di awal proses (sebelum loop while True) — muat balik
    _last_verify_at & _verified_cache dari DB kalau ada, supaya redeploy tidak
    selalu memicu full verifikasi ulang dari nol."""
    global _last_verify_at, _verified_cache
    try:
        conn = _connect_state_db()
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


def _is_candidate_for_verify(a):
    non_open_draft = a["fasih_total"] - a["fasih_open"] - a["jumlah_draft"]
    return a["jumlah_draft"] > 0 or (a["fasih_open"] > 0 and non_open_draft > 0)


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
            # search.value adalah text search di codeIdentity, jaga-jaga saring
            # persis biar tidak ketuker sama kode_sls lain yang mirip
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


def verify_stale_sls(sls_agg, browser):
    """Untuk SLS yang statusnya mencurigakan (OPEN/DRAFT bercampur dgn progres
    lain di SLS yang sama), cek ulang status per-assignment ke sumber ground
    truth dan timpa baris agregatnya kalau ternyata beda.

    Diproses per-chunk (CHUNK_SIZE_VERIFY SLS, context+login baru tiap chunk,
    jeda antar chunk) dan pakai fetch batch (Promise.all + retry-on-429) di
    sisi browser — pola yang sama dipakai sync_keberadaan.py, sudah terbukti
    aman dari rate limit FASIH."""
    candidates = [k for k, a in sls_agg.items() if _is_candidate_for_verify(a)]
    total = len(candidates)
    print(f"[VERIFY] {total} SLS kandidat perlu verifikasi ground-truth...", flush=True)
    if not candidates:
        return sls_agg, True  # tidak ada kandidat = otomatis "selesai penuh"

    calls = 0
    corrected = 0
    list_failed = 0
    budget_hit = False
    total_chunks = math.ceil(total / CHUNK_SIZE_VERIFY)

    for chunk_start in range(0, total, CHUNK_SIZE_VERIFY):
        if budget_hit:
            break
        chunk = candidates[chunk_start:chunk_start + CHUNK_SIZE_VERIFY]
        chunk_no = chunk_start // CHUNK_SIZE_VERIFY + 1
        print(f"  [VERIFY] chunk {chunk_no}/{total_chunks} ({len(chunk)} SLS, {calls} panggilan sejauh ini) — login ulang...", flush=True)

        page = xsrf = None
        for login_attempt in range(1, 3):  # 2x percobaan — kegagalan biasanya cuma hiccup jaringan sesaat
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
            print(f"  [VERIFY] chunk {chunk_no} dilewati (login gagal 2x), {len(chunk)} SLS dicoba lagi siklus verifikasi berikutnya", flush=True)
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

            old_draft, old_open = sls_agg[kode]["jumlah_draft"], sls_agg[kode]["fasih_open"]
            if new_a["jumlah_draft"] != old_draft or new_a["fasih_open"] != old_open:
                corrected += 1
                print(f"    [VERIFY] {kode}: draft {old_draft}->{new_a['jumlah_draft']}  open {old_open}->{new_a['fasih_open']}", flush=True)
            sls_agg[kode] = new_a
            _verified_cache[kode] = dict(new_a)  # simpan biar dipakai ulang di siklus 2-jam yg skip verifikasi

        try:
            ctx.close()
        except Exception:
            pass

        # Simpan progress per-chunk (bukan cuma di akhir run_once) — supaya kalau
        # proses ini di-redeploy/kill di TENGAH verifikasi, chunk yang sudah
        # kelar tidak hilang & tidak perlu diulang dari SLS pertama lagi.
        # _last_verify_at SENGAJA belum diupdate di sini (masih nilai lama) —
        # baru diset "selesai" oleh run_once() setelah verify_stale_sls tuntas
        # semua kandidat, supaya sisa kandidat yang belum sempat dicek tetap
        # ditagih di percobaan berikutnya (bukan dianggap "sudah 6 jam lagi").
        save_verify_state()

        if budget_hit:
            sisa = total - chunk_start - len(chunk)
            print(f"  [VERIFY] budget {MAX_VERIFY_CALLS} panggilan habis, sisa {sisa} SLS dilanjut siklus BERIKUTNYA (bukan nunggu {VERIFY_INTERVAL_HOURS} jam — belum benar-benar selesai)", flush=True)
            break
        if chunk_start + CHUNK_SIZE_VERIFY < total:
            time.sleep(CHUNK_DELAY_VERIFY)

    print(f"[VERIFY] {'selesai penuh' if not budget_hit else 'BELUM tuntas (kepotong budget)'}. {calls} panggilan assignment-history, {corrected} SLS terkoreksi, {list_failed} SLS gagal diambil listnya.", flush=True)
    return sls_agg, not budget_hit


def apply_non_sls_override(sls_agg):
    """
    SLS "Non SLS" (area kosong seperti gunung/sawah/kebun/ladang tanpa usaha/
    keluarga nyata) selalu dianggap punya minimal 1 assignment approved oleh
    pengawas (dan otomatis ikut submit), terlepas dari status approval asli
    di FASIH — supaya tidak nyangkut "belum diperiksa" di rekap progres.

    Identifikasi Non SLS BUKAN dari nama_sls (variasinya banyak: "NON SLS...",
    "KEBUN...", "SAWAH...", "LADANG...", "GUNUNG...", "HUTAN...", dst — tidak
    konsisten), tapi dari KODE SLS: kode_sls 16 digit = prov(2)+kab(2)+kec(3)+
    desa(3)+sls(4)+subsls(2). SLS residensial normal (RT/dusun) diberi nomor
    segmen sls < 1000, sedangkan Non SLS (wilayah kerja statistik non-
    permukiman) selalu diberi nomor segmen sls >= 1000 — konvensi baku BPS.
    """
    n = 0
    for kode, a in sls_agg.items():
        if len(kode) < 14 or not kode[10:14].isdigit():
            continue
        if int(kode[10:14]) < 1000:
            continue
        total = a["fasih_total"]
        if total <= 0:
            continue
        approved = min(max(a["fasih_approved_pengawas"], 1), total)
        if approved != a["fasih_approved_pengawas"]:
            a["fasih_approved_pengawas"] = approved
            n += 1
        submit = min(max(a["jumlah_submit"], approved), total)
        a["jumlah_submit"] = submit
    if n:
        print(f"[NON-SLS OVERRIDE] {n} SLS di-set minimal 1 approved", flush=True)
    return sls_agg


def upload(sls_agg):
    print(f"\n[DB] Menghubungkan ke {DB_HOST}:{DB_PORT}/{DB_NAME}...", flush=True)
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        ssl={"ssl": False},
    )
    cur = conn.cursor()

    cur.execute("SELECT id, kode_sls FROM sls")
    db_sls = {row[1]: row[0] for row in cur.fetchall()}
    print(f"[DB] SLS di database: {len(db_sls)}", flush=True)

    # Container ini jalan di UTC (Docker default) — datetime.now() polos akan
    # menyimpan jam yang salah 8 jam kalau langsung dipakai sbg fasih_synced_at
    # (kolom itu diasumsikan selalu WITA).
    synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
    inserted = updated = skipped = 0

    SQL = """
        INSERT INTO progress
          (sls_id, jumlah_submit, jumlah_draft,
           fasih_open, fasih_submitted,
           fasih_approved_pengawas, fasih_rejected_pengawas, fasih_revoked_pengawas,
           fasih_approved_kabupaten, fasih_rejected_kabupaten,
           fasih_approved_provinsi, fasih_rejected_provinsi,
           fasih_approved_pusat, fasih_rejected_pusat,
           fasih_edited_admin, fasih_completed_admin,
           fasih_total, fasih_synced_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
          jumlah_submit             = VALUES(jumlah_submit),
          jumlah_draft              = VALUES(jumlah_draft),
          fasih_open                = VALUES(fasih_open),
          fasih_submitted           = VALUES(fasih_submitted),
          fasih_approved_pengawas   = VALUES(fasih_approved_pengawas),
          fasih_rejected_pengawas   = VALUES(fasih_rejected_pengawas),
          fasih_revoked_pengawas    = VALUES(fasih_revoked_pengawas),
          fasih_approved_kabupaten  = VALUES(fasih_approved_kabupaten),
          fasih_rejected_kabupaten  = VALUES(fasih_rejected_kabupaten),
          fasih_approved_provinsi   = VALUES(fasih_approved_provinsi),
          fasih_rejected_provinsi   = VALUES(fasih_rejected_provinsi),
          fasih_approved_pusat      = VALUES(fasih_approved_pusat),
          fasih_rejected_pusat      = VALUES(fasih_rejected_pusat),
          fasih_edited_admin        = VALUES(fasih_edited_admin),
          fasih_completed_admin     = VALUES(fasih_completed_admin),
          fasih_total               = VALUES(fasih_total),
          fasih_synced_at           = VALUES(fasih_synced_at),
          updated_at                = NOW()
    """

    for kode, agg in sls_agg.items():
        sls_id = db_sls.get(kode)
        if sls_id is None:
            skipped += 1
            continue
        cur.execute(SQL, (
            sls_id,
            agg["jumlah_submit"],            agg["jumlah_draft"],
            agg["fasih_open"],               agg["fasih_submitted"],
            agg["fasih_approved_pengawas"],  agg["fasih_rejected_pengawas"],  agg["fasih_revoked_pengawas"],
            agg["fasih_approved_kabupaten"], agg["fasih_rejected_kabupaten"],
            agg["fasih_approved_provinsi"],  agg["fasih_rejected_provinsi"],
            agg["fasih_approved_pusat"],     agg["fasih_rejected_pusat"],
            agg["fasih_edited_admin"],       agg["fasih_completed_admin"],
            agg["fasih_total"],              synced_at,
        ))
        if cur.rowcount == 1:
            inserted += 1
        else:
            updated += 1
        if agg["fasih_total"] > 0:
            cur.execute("UPDATE sls SET target = %s WHERE id = %s", (agg["fasih_total"], sls_id))

    conn.commit()
    cur.close()
    conn.close()
    print(f"[DB] inserted={inserted}, updated={updated}, skipped={skipped}", flush=True)
    return inserted + updated


def summary(sls_agg):
    tot   = sum(v["fasih_total"]   for v in sls_agg.values())
    sub   = sum(v["jumlah_submit"] for v in sls_agg.values())
    draft = sum(v["jumlah_draft"]  for v in sls_agg.values())
    opn   = sum(v["fasih_open"]    for v in sls_agg.values())
    pct   = (sub * 100 // tot) if tot else 0
    print(f"\n{'='*50}")
    print(f"REKAP FASIH – DOMPU")
    print(f"{'='*50}")
    print(f"  SLS dengan data : {len(sls_agg)}")
    print(f"  Total assignment: {tot:,}")
    print(f"  - OPEN          : {opn:,}")
    print(f"  - DRAFT         : {draft:,}")
    print(f"  - SUBMITTED+    : {sub:,}  ({pct}%)")
    print(f"{'='*50}\n")


WITA = time.timezone  # akan di-override di bawah
try:
    import zoneinfo
    _wita_tz = zoneinfo.ZoneInfo("Asia/Makassar")
except Exception:
    import datetime as _dt
    _wita_tz = _dt.timezone(_dt.timedelta(hours=8))

def _now_wita():
    return datetime.now(_wita_tz)

def _next_run():
    now = _now_wita()
    h, m = now.hour, now.minute
    # Malam 22:00–06:29 → tunggu sampai 06:30
    if h >= 22 or h < 6 or (h == 6 and m < 30):
        from datetime import timedelta
        nxt = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if h >= 22:
            nxt += timedelta(days=1)
        return nxt
    # Siang → 2 jam dari sekarang
    from datetime import timedelta
    return now + timedelta(hours=2)

def run_once():
    print("="*50)
    print(f"SYNC FASIH → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]")
    print("="*50)

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        try:
            _, xsrf = login(ctx)
            print("\n[STEP 1] Scrape FASIH...")
            all_content = scrape_all(ctx, xsrf)

            print("\n[STEP 2] Aggregate per SLS...")
            sls_agg = aggregate(all_content)
            sls_agg = apply_verified_cache(sls_agg)

            global _last_verify_at
            if _should_verify_now():
                print(f"\n[STEP 2b] Verifikasi ground-truth SLS mencurigakan (OPEN/DRAFT basi)...")
                try:
                    sls_agg, completed = verify_stale_sls(sls_agg, browser)
                    if completed:
                        _last_verify_at = _now_wita()
                        print(f"[VERIFY] tuntas semua kandidat — jadwal verifikasi berikutnya {VERIFY_INTERVAL_HOURS} jam lagi.", flush=True)
                    else:
                        # Kepotong budget di tengah — JANGAN update _last_verify_at, supaya
                        # siklus sync berikutnya (2 jam lagi, bukan 6 jam) langsung lanjut
                        # verifikasi sisanya, bukan dianggap "sudah selesai".
                        print(f"[VERIFY] belum tuntas, akan lanjut otomatis di siklus sync berikutnya (~2 jam lagi).", flush=True)
                except Exception as e:
                    print(f"[VERIFY] gagal, lanjut pakai data sebelum verifikasi: {e}", flush=True)
            else:
                nxt = _last_verify_at + timedelta(hours=VERIFY_INTERVAL_HOURS)
                print(f"\n[STEP 2b] Lewati verifikasi ground-truth (terakhir {_last_verify_at:%H:%M} WITA, jadwal berikutnya {nxt:%H:%M} WITA)", flush=True)
        finally:
            browser.close()

    sls_agg = apply_non_sls_override(sls_agg)
    summary(sls_agg)

    print("[STEP 3] Upload ke database...")
    n = upload(sls_agg)
    save_verify_state()
    print(f"\nSelesai! {n} SLS diupdate.")

if __name__ == "__main__":
    load_verify_state()
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] Sync gagal: {e}", flush=True)

        nxt = _next_run()
        secs = max(0, (nxt - _now_wita()).total_seconds())
        print(f"[SCHEDULER] Sync berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} ({int(secs//60)} menit)", flush=True)
        time.sleep(secs)
