"""
explore_users.py — cek struktur data petugas (PPL/PML) dari FASIH API
Login sama persis dengan sync_fasih.py (ctx.request.post + XSRF).
"""
import os, json, time
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_USER       = os.getenv("FASIH_USER",      "agung.yuniarta")
FASIH_PASS       = os.getenv("FASIH_PASS",      "kelayu1998")
BASE_URL         = "https://fasih-sm.bps.go.id"
PERIOD_ID        = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
DOMPU_REGION2_ID = "546a26bf-e388-41ab-9083-e02cbbc093d4"
PENCACAH_ROLE_ID = "6d7d919a-45e5-4779-bb87-2905b49fd31a"
HEADLESS         = os.getenv("HEADLESS", "false").lower() == "true"


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
    print(f"[LOGIN] OK — {active.url}", flush=True)
    return xsrf


def fetch_page(ctx, xsrf, page_num=0, size=10):
    payload = {
        "surveyPeriodId": PERIOD_ID,
        "surveyRoleId":   PENCACAH_ROLE_ID,
        "size":   size,
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
        "Accept":       "application/json, */*",
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Referer":      f"{BASE_URL}/app/surveys",
        "Origin":       BASE_URL,
    }
    r = ctx.request.post(
        f"{BASE_URL}/analytic/api/v2/assignment/report-progress-by-responsibility",
        data=json.dumps(payload),
        headers=hdrs,
        timeout=60_000,
    )
    if r.status != 200:
        print(f"[ERROR] HTTP {r.status}: {r.text()[:300]}")
        return [], 0
    d = r.json()
    inner = d.get("data", {})
    return inner.get("content", []), inner.get("totalElements", 0)


def main():
    with sync_playwright() as pw:
        browser, ctx = make_browser(pw)
        xsrf = login(ctx)

        content, total = fetch_page(ctx, xsrf, page_num=0, size=5)
        print(f"\nTotal petugas: {total}")
        print(f"Item dikembalikan: {len(content)}")

        if content:
            print("\n=== 5 item pertama ===")
            for i, item in enumerate(content[:5]):
                d = {k: v for k, v in item.items() if k != "regionSummary"}
                print(f"  [{i}] {json.dumps(d, ensure_ascii=False)}")

            first_email = content[0].get("email", "")
            first_id    = content[0].get("userId", "")
            xsrf2 = next((c["value"] for c in ctx.cookies() if c["name"] == "XSRF-TOKEN"), xsrf)
            hdrs  = {"Accept": "application/json", "X-XSRF-TOKEN": xsrf2}

            print(f"\n=== Coba endpoint user by email / list ===")
            candidates = [
                (f"{BASE_URL}/user-management/api/users?email={first_email}", "by email"),
                (f"{BASE_URL}/user-management/api/v2/users?email={first_email}", "v2 by email"),
                (f"{BASE_URL}/user-management/api/users?page=0&size=5", "list all"),
                (f"{BASE_URL}/user-management/api/v2/users?page=0&size=5", "v2 list"),
                (f"{BASE_URL}/user-management/api/officers?page=0&size=5", "officers"),
                (f"{BASE_URL}/assignment-general/api/officers?periodId={PERIOD_ID}&page=0&size=5", "asgn officers"),
                (f"{BASE_URL}/assignment-general/api/officers?surveyPeriodId={PERIOD_ID}&page=0&size=5", "asgn officers v2"),
                (f"{BASE_URL}/analytic/api/v2/users?page=0&size=5", "analytic users"),
                (f"{BASE_URL}/app/api/user-management/api/users?page=0&size=5", "app user-mgmt"),
            ]
            for url, label in candidates:
                r = ctx.request.get(url, headers=hdrs, timeout=10_000)
                body = r.text()[:400]
                print(f"  [{r.status}] {label}")
                if r.status == 200:
                    print(f"         {body}")

        browser.close()


if __name__ == "__main__":
    main()
