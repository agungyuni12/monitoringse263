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
from datetime import datetime
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

PAGE_SIZE = 10    # server membatasi max 10 per halaman
DELAY     = 0.3   # detik jeda antar halaman

# Status yang dihitung sebagai "submit"
SUBMIT_STATUSES = frozenset({
    "SUBMITTED BY Pencacah", "APPROVED BY Pengawas",
    "REJECTED BY Pengawas", "REVOKED BY Pengawas",
    "SUBMITTED RESPONDENT", "REJECTED BY Admin Kabupaten",
})


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
    """Login lewat browser, return xsrf token. ctx tetap hidup untuk API calls."""
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
    return xsrf


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


def aggregate(all_content):
    """Aggregate status per kode_sls (16-digit regionCode)."""
    sls_agg = defaultdict(lambda: {
        "jumlah_submit":  0,
        "jumlah_draft":   0,
        "fasih_open":     0,
        "fasih_submitted": 0,
        "fasih_approved": 0,
        "fasih_rejected": 0,
        "fasih_revoked":  0,
        "fasih_total":    0,
    })

    for pencacah in all_content:
        for rs in pencacah.get("regionSummary", []):
            kode = rs.get("regionCode", "")
            if not kode or not kode.startswith("5205"):
                continue
            a = sls_agg[kode]
            for sb in rs.get("statusBreakdown", []):
                status = sb.get("status", "")
                cnt    = int(sb.get("count", 0))
                a["fasih_total"] += cnt
                if status in SUBMIT_STATUSES:
                    a["jumlah_submit"] += cnt
                if status == "DRAFT":
                    a["jumlah_draft"] += cnt
                if status == "OPEN":
                    a["fasih_open"] += cnt
                elif "SUBMITTED" in status:
                    a["fasih_submitted"] += cnt
                elif "APPROVED" in status:
                    a["fasih_approved"] += cnt
                elif "REJECTED" in status:
                    a["fasih_rejected"] += cnt
                elif "REVOKED" in status:
                    a["fasih_revoked"] += cnt

    print(f"[AGGREGATE] SLS unik: {len(sls_agg)}", flush=True)
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

    synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = updated = skipped = 0

    SQL = """
        INSERT INTO progress
          (sls_id, jumlah_submit, jumlah_draft,
           fasih_open, fasih_submitted, fasih_approved,
           fasih_rejected, fasih_revoked, fasih_total,
           fasih_synced_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
          jumlah_submit   = VALUES(jumlah_submit),
          jumlah_draft    = VALUES(jumlah_draft),
          fasih_open      = VALUES(fasih_open),
          fasih_submitted = VALUES(fasih_submitted),
          fasih_approved  = VALUES(fasih_approved),
          fasih_rejected  = VALUES(fasih_rejected),
          fasih_revoked   = VALUES(fasih_revoked),
          fasih_total     = VALUES(fasih_total),
          fasih_synced_at = VALUES(fasih_synced_at),
          updated_at      = NOW()
    """

    for kode, agg in sls_agg.items():
        sls_id = db_sls.get(kode)
        if sls_id is None:
            skipped += 1
            continue
        cur.execute(SQL, (
            sls_id,
            agg["jumlah_submit"], agg["jumlah_draft"],
            agg["fasih_open"], agg["fasih_submitted"], agg["fasih_approved"],
            agg["fasih_rejected"], agg["fasih_revoked"], agg["fasih_total"],
            synced_at,
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


if __name__ == "__main__":
    print("="*50)
    print(f"SYNC FASIH → se2026  [{datetime.now():%Y-%m-%d %H:%M:%S}]")
    print("="*50)

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        try:
            xsrf = login(ctx)

            print("\n[STEP 1] Scrape FASIH...")
            all_content = scrape_all(ctx, xsrf)
        finally:
            browser.close()

    print("\n[STEP 2] Aggregate per SLS...")
    sls_agg = aggregate(all_content)
    summary(sls_agg)

    print("[STEP 3] Upload ke database...")
    n = upload(sls_agg)
    print(f"\nSelesai! {n} SLS diupdate.")
