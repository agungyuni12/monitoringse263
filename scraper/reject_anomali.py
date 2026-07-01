"""
reject_anomali.py — otomasi reject assignment anomali di FASIH SM

Alur per assignment:
  1. Buka halaman assignment
  2. Klik "Edit Assignment"
  3. Aktifkan toggle "Tampilkan Anomali Usaha dan Keluarga"
  4. Klik "Kirim"
  5. Klik "Reject"

Progress disimpan di reject_progress.txt — aman untuk dijalankan ulang.

Env vars:
  FASIH_USER   (default: agung.yuniarta)
  FASIH_PASS   (default: kelayu1998)
  HEADLESS     jalankan tanpa tampilan browser (default: false)
  START_FROM   nomor urut assignment untuk mulai (default: 0)
"""
import os, time, json
import pymysql
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_USER = os.getenv("FASIH_USER", "agung.yuniarta")
FASIH_PASS = os.getenv("FASIH_PASS", "kelayu1998")
BASE_URL   = "https://fasih-sm.bps.go.id"
PERIOD_ID  = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
HEADLESS   = os.getenv("HEADLESS", "false").lower() == "true"
START_FROM = int(os.getenv("START_FROM", "0"))

PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "reject_progress.txt")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

WITA = timezone(timedelta(hours=8))


def _now():
    return datetime.now(WITA).strftime("%H:%M:%S")


# ── Progress tracking ─────────────────────────────────────────────────────────

def load_progress():
    """Baca assignment_id yang sudah selesai."""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE) as f:
        return {line.strip() for line in f if line.strip()}


def save_progress(assignment_id):
    with open(PROGRESS_FILE, "a") as f:
        f.write(assignment_id + "\n")


# ── DB ────────────────────────────────────────────────────────────────────────

def get_assignment_ids():
    """Ambil semua distinct assignment_id dari tabel anomali, urut abjad."""
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT assignment_id FROM anomali ORDER BY assignment_id")
    ids = [r["assignment_id"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


# ── Browser ───────────────────────────────────────────────────────────────────

def make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--disable-infobars",
              "--no-sandbox", "--start-maximized"],
        slow_mo=200,
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
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
    print(f"[LOGIN] OK — {active.url}", flush=True)
    return active


# ── Per-assignment automation ─────────────────────────────────────────────────

def process_assignment(page, assignment_id, idx, total):
    url = f"{BASE_URL}/app/assignment/{PERIOD_ID}/{assignment_id}"
    prefix = f"[{idx}/{total}] {assignment_id[:8]}…"

    try:
        # 1. Buka halaman assignment, tunggu sampai FAB siap
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            page.locator("button[aria-label='Open menu']").first.wait_for(state="visible", timeout=20_000)
        except PWTimeout:
            print(f"  {prefix} ⚠ Halaman tidak load, skip", flush=True)
            return "skip"

        # 2. Klik tombol "+" untuk buka menu FAB
        page.locator("button[aria-label='Open menu']").first.click()
        time.sleep(1)

        # 3. Klik "Edit Assignment" — <a class="fab-action-btn"> di dalam .fab-item
        try:
            edit_btn = page.locator(".fab-item a.fab-action-btn, .fab-item a[href$='/edit']").first
            edit_btn.wait_for(state="visible", timeout=8_000)
            edit_btn.click()
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
            time.sleep(1.5)
        except PWTimeout:
            print(f"  {prefix} ⚠ Link Edit Assignment tidak ditemukan, skip", flush=True)
            return "skip"

        # 4a. Klik "CATATAN" di sidebar kiri untuk tampilkan form catatan
        try:
            catatan = page.locator("div[title='CATATAN']").first
            catatan.wait_for(state="visible", timeout=20_000)
            catatan.click()
            time.sleep(1)
        except PWTimeout:
            print(f"  {prefix} ⚠ Sidebar CATATAN tidak ditemukan", flush=True)

        # 4. Aktifkan toggle "Tampilkan Anomali Usaha dan Keluarga"
        #    Toggle = div[id*='switch'][id*='control'] — cek data-checked untuk status
        try:
            label = page.locator("text=Tampilkan Anomali Usaha dan Keluarga").first
            label.wait_for(state="visible", timeout=10_000)
            label.scroll_into_view_if_needed()
            time.sleep(0.5)

            # Toggle pertama: "Tampilkan Anomali Usaha dan Keluarga" — klik 1x
            toggle1 = page.locator("div[id*='switch'][id*='control']").first
            toggle1.wait_for(state="visible", timeout=5_000)
            toggle1.click()
            time.sleep(0.8)

            # Toggle kedua muncul di bawah — klik 2x
            toggle2 = page.locator("div[id*='switch'][id*='control']").nth(1)
            toggle2.wait_for(state="visible", timeout=5_000)
            toggle2.click()
            time.sleep(0.4)
            toggle2.click()
            time.sleep(0.4)
        except PWTimeout:
            print(f"  {prefix} ⚠ Toggle Tampilkan Anomali tidak ditemukan", flush=True)

        # 5. Klik "Kirim" pertama
        try:
            kirim_btn = page.get_by_role("button", name="Kirim", exact=True).last
            kirim_btn.wait_for(state="visible", timeout=8_000)
            kirim_btn.click()
            time.sleep(1.5)
        except PWTimeout:
            print(f"  {prefix} ⚠ Tombol Kirim tidak ditemukan", flush=True)

        # 5b. Klik "Kirim" kedua
        try:
            kirim_btn2 = page.get_by_role("button", name="Kirim", exact=True).last
            kirim_btn2.wait_for(state="visible", timeout=5_000)
            kirim_btn2.click()
            time.sleep(1.5)
        except PWTimeout:
            pass

        # 5c. Klik "Konfirmasi" di dialog
        try:
            konfirmasi = page.get_by_role("button", name="Konfirmasi", exact=True).last
            konfirmasi.wait_for(state="visible", timeout=6_000)
            konfirmasi.click()
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PWTimeout:
                pass
            time.sleep(1)
        except PWTimeout:
            pass

        # 5d. Klik "Kembali ke preview assignment" (fab-button yang muncul setelah konfirmasi)
        try:
            kembali = page.locator("button[aria-label='Kembali ke preview assignment']").first
            kembali.wait_for(state="visible", timeout=20_000)
            kembali.click()
            time.sleep(1)
        except PWTimeout:
            print(f"  {prefix} ⚠ Tombol Kembali ke preview tidak ditemukan", flush=True)
            return "error"

        # 5e. Klik "Tinggalkan" untuk keluar dari mode edit
        try:
            tinggalkan = page.get_by_role("button", name="Tinggalkan", exact=True).first
            tinggalkan.wait_for(state="visible", timeout=8_000)
            tinggalkan.click()
            time.sleep(1.5)
        except PWTimeout:
            print(f"  {prefix} ⚠ Tombol Tinggalkan tidak ditemukan", flush=True)
            return "error"

        # 6. Tunggu FAB "Open menu" muncul lagi lalu buka menu
        try:
            fab2 = page.locator("button[aria-label='Open menu']").first
            fab2.wait_for(state="visible", timeout=20_000)
            fab2.click()
            time.sleep(1)
        except PWTimeout:
            print(f"  {prefix} ⚠ FAB tidak muncul setelah Tinggalkan", flush=True)
            return "error"

        # 7. Klik "Reject" — button[aria-haspopup='dialog'] di dalam .fab-item
        try:
            reject_btn = page.locator(
                ".fab-item button[aria-haspopup='dialog']"
            ).first
            reject_btn.wait_for(state="visible", timeout=8_000)
            reject_btn.click()
            time.sleep(1.5)

            # Konfirmasi dialog jika ada
            try:
                confirm = page.locator(
                    "button:has-text('Ya'), button:has-text('Konfirmasi'), "
                    "button:has-text('OK'), button:has-text('Iya')"
                ).first
                confirm.wait_for(state="visible", timeout=4_000)
                confirm.click()
                time.sleep(1)
            except PWTimeout:
                pass

        except PWTimeout:
            print(f"  {prefix} ⚠ Tombol Reject tidak ditemukan", flush=True)
            return "error"

        print(f"  {prefix} ✓ [{_now()}]", flush=True)
        return "ok"

    except PWTimeout as e:
        print(f"  {prefix} ✗ Timeout: {e}", flush=True)
        return "error"
    except Exception as e:
        print(f"  {prefix} ✗ Error: {e}", flush=True)
        return "error"


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print(f"=== reject_anomali.py [{datetime.now(WITA).strftime('%Y-%m-%d %H:%M:%S')}] ===", flush=True)

    assignment_ids = get_assignment_ids()
    done           = load_progress()
    total          = len(assignment_ids)

    remaining = [a for a in assignment_ids if a not in done]
    print(f"Total assignment: {total} | Sudah selesai: {len(done)} | Sisa: {len(remaining)}", flush=True)

    if not remaining:
        print("Semua assignment sudah diproses!", flush=True)
        return

    # START_FROM untuk skip manual
    if START_FROM > 0:
        remaining = remaining[START_FROM:]
        print(f"Mulai dari urutan {START_FROM}", flush=True)

    stats = {"ok": 0, "skip": 0, "error": 0}

    with sync_playwright() as pw:
        browser, ctx = make_browser(pw)
        page = login(ctx)
        time.sleep(2)

        for i, assignment_id in enumerate(remaining, 1):
            idx = len(done) + i
            result = process_assignment(page, assignment_id, idx, total)
            stats[result] = stats.get(result, 0) + 1

            if result in ("ok", "skip"):
                save_progress(assignment_id)
                done.add(assignment_id)

            # Jeda antar assignment
            time.sleep(1.5)

            # Laporan tiap 50 assignment
            if i % 50 == 0:
                print(f"\n[PROGRESS] {i}/{len(remaining)} — ok:{stats['ok']} skip:{stats['skip']} error:{stats['error']}\n", flush=True)

        browser.close()

    print(f"\n=== SELESAI === ok:{stats['ok']} | skip:{stats['skip']} | error:{stats['error']}", flush=True)


if __name__ == "__main__":
    run()
