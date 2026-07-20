"""
Sync data FASIH → database se2026 (tabel progress)

Endpoint: /analytic/api/v2/assignment/report-progress-by-responsibility
Strategi: paginate 235 pencacah Dompu (5 halaman), aggregate per sub-SLS (16-digit),
          upsert ke tabel progress berdasarkan kode_sls.

Data yang dipakai di sini murni mentah dari FASIH (endpoint report-progress-
by-responsibility bisa telat sinkron utk status OPEN/DRAFT — lihat docstring
sync_fasih_verify_stale.py). Verifikasi ground-truth dijalankan terpisah,
manual, lewat sync_fasih_verify_stale.py — hasilnya tidak lagi otomatis
diterapkan balik ke sini.

Env vars:
  FASIH_USER    (default: agung.yuniarta)
  FASIH_PASS    (default: kelayu1998)
  DB_HOST       (default: 127.0.0.1)
  DB_PORT       (default: 3306)
  DB_USER       (default: root)
  DB_PASS       (default: kelayu1998)
  DB_NAME       (default: se2026)
"""

import os, json, math, random, re, time
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
        locale="id-ID",
        timezone_id="Asia/Makassar",
        extra_http_headers={"Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7"},
    )
    return browser, ctx


def _human_pause(a=0.4, b=1.1):
    time.sleep(random.uniform(a, b))


def _human_mouse_wander(page, moves=3):
    """Gerakkan mouse sedikit sebelum interaksi, biar gak terlihat instan/scripted."""
    try:
        w = page.viewport_size["width"]
        h = page.viewport_size["height"]
        for _ in range(moves):
            page.mouse.move(random.randint(50, w - 50), random.randint(50, h - 50), steps=random.randint(5, 15))
            time.sleep(random.uniform(0.08, 0.25))
    except Exception:
        pass


def _human_type(locator, text):
    locator.click()
    _human_pause(0.15, 0.4)
    locator.press_sequentially(text, delay=random.randint(60, 160))


def _check_bot_wall(page, tag):
    body = ""
    try:
        body = page.content()
    except Exception:
        pass
    if "Bot Detected" in body or "sistem kami mendeteksi koneksi anda sebagai bot" in body:
        m = re.search(r"BOT-\d+", body)
        code = m.group(0) if m else "?"
        raise RuntimeError(f"Diblokir bot-detection BPS di tahap '{tag}' (kode {code})")


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
    _check_bot_wall(page, "challenge")

    active = page
    if page.url in ("about:blank", ""):
        print("[LOGIN] Membuka page baru setelah challenge...", flush=True)
        active = ctx.new_page()
        _stealth.apply_stealth_sync(active)

    _human_pause(0.6, 1.4)
    print("[LOGIN] Navigasi ulang ke SSO...", flush=True)
    active.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    print(f"[LOGIN] URL: {active.url}", flush=True)
    _check_bot_wall(active, "navigasi SSO")

    active.wait_for_selector("#kc-form-login", timeout=LONG)
    print("[LOGIN] Form login ditemukan.", flush=True)
    _human_mouse_wander(active)
    _human_pause(0.3, 0.8)
    _human_type(active.locator("#username"), FASIH_USER)
    _human_pause(0.2, 0.6)
    _human_type(active.locator("#password"), FASIH_PASS)
    _human_pause(0.3, 0.9)
    active.click("#kc-login")
    try:
        active.wait_for_url("**fasih-sm.bps.go.id**", timeout=LONG)
    except Exception:
        print(f"[LOGIN] wait_for_url timeout. URL saat ini: {active.url}", flush=True)
        _check_bot_wall(active, "setelah submit kredensial")
        raise
    print(f"[LOGIN] Redirect ke: {active.url}", flush=True)
    _check_bot_wall(active, "setelah login")

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
    Dipakai baik oleh aggregate() (dari statusBreakdown per pencacah) di sini
    maupun verify_stale_sls() di sync_fasih_verify_stale.py (dari status
    per-assignment hasil verifikasi ground truth) supaya logika
    klasifikasinya konsisten di kedua jalur — makanya fungsi ini disalin,
    bukan cuma dipakai satu file."""
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
        finally:
            browser.close()

    sls_agg = apply_non_sls_override(sls_agg)
    summary(sls_agg)

    print("[STEP 3] Upload ke database...")
    n = upload(sls_agg)
    print(f"\nSelesai! {n} SLS diupdate.")

if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] Sync gagal: {e}", flush=True)

        nxt = _next_run()
        secs = max(0, (nxt - _now_wita()).total_seconds())
        print(f"[SCHEDULER] Sync berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} ({int(secs//60)} menit)", flush=True)
        time.sleep(secs)
