"""
sync_users.py — sinkronkan email PPL dan SLS assignment dari FASIH ke DB

Yang disync:
  - users.email  : diisi dari FASIH (nama tidak diubah)
  - sls.ppl_id   : diupdate kalau ada perubahan assignment pencacah di FASIH

Login sama persis dengan sync_fasih.py (ctx.request.post + XSRF).
"""
import os, json, time, math
import pymysql
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_USER       = os.getenv("FASIH_USER",      "agung.yuniarta")
FASIH_PASS       = os.getenv("FASIH_PASS",      "kelayu1998")
BASE_URL         = "https://fasih-sm.bps.go.id"
PERIOD_ID        = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
SURVEY_ID        = "a0429e96-51a5-477b-a415-485f9c153004"
DOMPU_REGION2_ID = "546a26bf-e388-41ab-9083-e02cbbc093d4"
PENCACAH_ROLE_ID = "6d7d919a-45e5-4779-bb87-2905b49fd31a"
PAGE_SIZE        = 10   # server FASIH batasi max 10
HEADLESS         = os.getenv("HEADLESS", "false").lower() == "true"

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

WITA = timezone(timedelta(hours=8))


def _now():
    return datetime.now(WITA).replace(tzinfo=None)


def connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def make_browser(pw):
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
    try:
        page.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=90_000)
    except Exception:
        pass
    active = page
    if page.url in ("about:blank", ""):
        active = ctx.new_page()
        _stealth.apply_stealth_sync(active)
    active.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=90_000)
    active.wait_for_selector("#kc-form-login", timeout=90_000)
    active.fill("#username", FASIH_USER)
    active.fill("#password", FASIH_PASS)
    active.click("#kc-login")
    active.wait_for_url("**fasih-sm.bps.go.id**", timeout=90_000)
    cookies = ctx.cookies()
    xsrf = next((c["value"] for c in cookies if c["name"] == "XSRF-TOKEN"), "")
    if not xsrf:
        raise RuntimeError("Login gagal – tidak ada XSRF token")
    print(f"[LOGIN] OK", flush=True)
    return xsrf


def fetch_all(ctx, xsrf):
    hdrs = {
        "Accept":       "application/json, */*",
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Referer":      f"{BASE_URL}/app/surveys",
        "Origin":       BASE_URL,
    }

    def _page(page_num):
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
        r = ctx.request.post(
            f"{BASE_URL}/analytic/api/v2/assignment/report-progress-by-responsibility",
            data=json.dumps(payload), headers=hdrs, timeout=60_000,
        )
        if r.status != 200:
            print(f"  [WARN] halaman {page_num}: HTTP {r.status} — {r.text()[:500]}", flush=True)
            return [], 0
        d = r.json()
        inner = d.get("data", {})
        return inner.get("content", []), inner.get("totalElements", 0)

    content0, total = _page(0)
    if not content0:
        raise RuntimeError("Tidak ada data dari FASIH")
    pages = max(1, math.ceil(total / PAGE_SIZE))
    print(f"[FASIH] Total pencacah: {total} | {pages} halaman", flush=True)

    all_items = list(content0)
    for pg in range(1, pages):
        print(f"  Halaman {pg+1}/{pages}...", flush=True)
        c, _ = _page(pg)
        all_items.extend(c)
        time.sleep(2)

    return all_items


def sync(all_items):
    conn = connect_db()
    cur  = conn.cursor()

    # Load semua SLS dari DB: kode_sls → {id, ppl_id}
    cur.execute("SELECT id, kode_sls, ppl_id FROM sls")
    sls_map = {r["kode_sls"]: r for r in cur.fetchall()}

    # Load semua users PPL: id → email
    cur.execute("SELECT id, email FROM users WHERE role='ppl'")
    user_map = {r["id"]: r["email"] for r in cur.fetchall()}

    email_updated  = 0
    sls_reassigned = 0
    skipped        = 0

    for pencacah in all_items:
        fasih_email = (pencacah.get("email") or "").strip()
        if not fasih_email:
            skipped += 1
            continue

        region_summary = pencacah.get("regionSummary", [])
        # Kumpulkan SLS yang ditangani pencacah ini (16 digit, awalan 5205)
        sls_codes = [
            rs["regionCode"] for rs in region_summary
            if rs.get("regionCode", "").startswith("5205") and len(rs.get("regionCode", "")) == 16
        ]

        if not sls_codes:
            skipped += 1
            continue

        # Cari user lokal via sls.ppl_id
        ppl_ids = set()
        for kode in sls_codes:
            row = sls_map.get(kode)
            if row:
                ppl_ids.add(row["ppl_id"])

        if not ppl_ids:
            skipped += 1
            continue

        # Ambil ppl_id yang paling sering muncul (mayoritas SLS)
        from collections import Counter
        code_to_ppl = {kode: sls_map[kode]["ppl_id"] for kode in sls_codes if kode in sls_map}
        if not code_to_ppl:
            continue
        local_ppl_id = Counter(code_to_ppl.values()).most_common(1)[0][0]

        # Update email user lokal (kalau belum sama)
        current_email = user_map.get(local_ppl_id, "")
        if current_email != fasih_email:
            cur.execute("UPDATE users SET email=%s WHERE id=%s AND role='ppl'",
                        (fasih_email, local_ppl_id))
            if cur.rowcount:
                print(f"  [email] user_id={local_ppl_id}: {current_email!r} → {fasih_email!r}", flush=True)
                email_updated += 1
            user_map[local_ppl_id] = fasih_email

        # Update sls.ppl_id kalau ada SLS yang ppl_id-nya berbeda
        for kode in sls_codes:
            row = sls_map.get(kode)
            if not row:
                continue
            if row["ppl_id"] != local_ppl_id:
                cur.execute("UPDATE sls SET ppl_id=%s WHERE kode_sls=%s",
                            (local_ppl_id, kode))
                if cur.rowcount:
                    print(f"  [sls] {kode}: ppl_id {row['ppl_id']} → {local_ppl_id}", flush=True)
                    sls_reassigned += 1
                    row["ppl_id"] = local_ppl_id

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n[DONE] email_updated={email_updated} | sls_reassigned={sls_reassigned} | skipped={skipped}", flush=True)


def run():
    print(f"=== sync_users.py [{_now()}] ===", flush=True)
    with sync_playwright() as pw:
        browser, ctx = make_browser(pw)
        xsrf      = login(ctx)
        all_items = fetch_all(ctx, xsrf)
        browser.close()

    print(f"\n[SYNC] Mulai update DB...", flush=True)
    sync(all_items)


if __name__ == "__main__":
    run()
