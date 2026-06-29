"""
Debug: cari endpoint FASIH yang return semua assignment untuk kab 5205
"""
import os, json, time
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_URL       = "https://fasih-sm.bps.go.id"
FASIH_USER      = os.getenv("FASIH_USER",      "agung.yuniarta")
FASIH_PASS      = os.getenv("FASIH_PASS",      "kelayu1998")
FASIH_PERIOD_ID = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
KODE_KAB        = "5205"
HEADLESS        = os.getenv("HEADLESS", "false").lower() == "true"


def try_url(page, url, label):
    try:
        r = page.evaluate(f"""async () => {{
            const r = await fetch('{url}', {{credentials:'include'}});
            const txt = await r.text();
            return {{status: r.status, body: txt.slice(0, 500)}};
        }}""")
        status = r.get("status")
        body   = r.get("body", "")
        try:
            obj = json.loads(body + ("..." if len(body) >= 500 else ""))
        except Exception:
            obj = None
        count = None
        if isinstance(obj, dict):
            d = obj.get("data") or obj.get("content") or obj.get("result") or []
            if isinstance(d, list):
                count = len(d)
            elif isinstance(d, dict):
                count = d.get("totalElements") or d.get("total")
        print(f"  [{status}] {label}")
        print(f"         {body[:200]}")
        if count is not None:
            print(f"         → {count} items")
    except Exception as e:
        print(f"  [ERR] {label}: {e}")
    print()


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()
        _stealth.apply_stealth_sync(page)

        # Login
        try:
            page.goto(f"{FASIH_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=90_000)
        except Exception:
            pass
        time.sleep(3)
        page.wait_for_selector("input[name='username']", timeout=90_000)
        page.fill("input[name='username']", FASIH_USER)
        page.fill("input[name='password']", FASIH_PASS)
        page.click("#kc-login, input[type='submit']")
        for _ in range(30):
            time.sleep(2)
            if "fasih-sm.bps.go.id" in page.url and "login" not in page.url:
                break
        time.sleep(3)
        print(f"Login OK: {page.url}\n")

        base = f"{FASIH_URL}/assignment-general/api"
        pid  = FASIH_PERIOD_ID
        kab  = KODE_KAB

        candidates = [
            # Paginated list endpoints
            (f"{base}/assignments?periodId={pid}&page=0&size=20", "list semua (page 0)"),
            (f"{base}/assignments?periodId={pid}&kodeKab={kab}&page=0&size=20", "list by kab (page 0)"),
            (f"{base}/assignments/list?periodId={pid}&kodeKab={kab}&page=0&size=20", "list/ by kab"),
            (f"{base}/assignments/get-all?periodId={pid}&kodeKab={kab}", "get-all by kab"),

            # By kabupaten variants
            (f"{base}/assignments/get-principal-values-by-kabupaten/{pid}/{kab}", "by-kab kode_sls"),
            (f"{base}/assignments/get-principal-values/{pid}?kodeKab={kab}", "get-principal-values qs kab"),

            # By kecamatan (ambil kec 010 = Dompu kota)
            (f"{base}/assignments/get-principal-values-by-kecamatan/{pid}/{kab}010", "by-kec 010"),
            (f"{base}/assignments/get-principal-values-by-smallest-code/{pid}/{kab}010001000", "by-sls pendek"),

            # Summary / progres endpoints
            (f"{base}/assignments/count?periodId={pid}&kodeKab={kab}", "count by kab"),
            (f"{base}/assignments/summary?periodId={pid}&kodeKab={kab}", "summary by kab"),
            (f"{base}/assignments/rekapitulasi?periodId={pid}&kodeKab={kab}", "rekapitulasi"),

            # Check what SLS codes FASIH actually uses
            (f"{base}/smallest-codes?periodId={pid}&kodeKab={kab}&page=0&size=20", "smallest-codes list"),
            (f"{base}/assignments/smallest-code-list?periodId={pid}&kodeKab={kab}", "smallest-code-list"),
        ]

        for url, label in candidates:
            try_url(page, url, label)

        browser.close()


if __name__ == "__main__":
    main()
