"""
sync_users.py — sinkronkan email PPL & PML + assignment SLS dari FASIH ke DB, sekali jalan.

Untuk tiap SLS:
  1. get-principal-values-by-smallest-code -> assignmentId salah satu target di SLS itu
     (PPL & PML sama untuk semua target dalam 1 SLS, jadi cukup 1 assignmentId)
  2. get-structure-approval?assignmentId=... -> email Pencacah (PPL) & Pengawas (PML)
     sekaligus dalam satu response

Yang disync ke DB:
  - users.email : role ppl & pml, diisi dari FASIH (nama tidak diubah)
  - sls.ppl_id  : diupdate kalau ada perubahan assignment pencacah di FASIH
  - sls.pml_id  : diupdate kalau ada perubahan assignment pengawas di FASIH

Diproses per-chunk SLS dengan login ulang tiap chunk (sesi FASIH gampang timeout)
dan fetch batch konkuren (Promise.all di browser) ala sync_keberadaan.py biar
lebih cepat dibanding request satu-satu, plus resumable lewat tabel sync_progress
kalau proses terputus di tengah jalan.
"""
import os, time, json
import pymysql
from collections import Counter
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

BASE_URL   = "https://fasih-sm.bps.go.id"
FASIH_USER = os.getenv("FASIH_USER",      "agung.yuniarta")
FASIH_PASS = os.getenv("FASIH_PASS",      "kelayu1998")
PERIOD_ID  = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
HEADLESS   = os.getenv("HEADLESS", "false").lower() == "true"

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

BATCH_SIZE  = 20    # request konkuren per Promise.all
CHUNK_SIZE  = 150   # SLS per chunk (login ulang tiap chunk)
CHUNK_DELAY = 5
JOB_NAME    = "sync_users"

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
    return pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--no-sandbox"],
    )


def login(browser):
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720},
    )
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
    print(f"[LOGIN] OK", flush=True)
    return active, ctx


def fetch_batch(page, urls):
    """Fetch konkuren (Promise.all) dari dalam browser, pakai cookie sesi aktif."""
    if not urls:
        return []
    urls_js = json.dumps(urls)
    try:
        return page.evaluate(f"""async () => {{
            const urls = {urls_js};
            return await Promise.all(urls.map(u =>
                fetch(u, {{credentials:'include'}})
                .then(r => r.ok ? r.json() : null)
                .catch(() => null)
            ));
        }}""")
    except Exception:
        return [None] * len(urls)


def _ensure_progress_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_progress (
                job       VARCHAR(50) PRIMARY KEY,
                sls_index INT NOT NULL DEFAULT 0
            ) ENGINE=InnoDB
        """)
    conn.commit()


def _save_progress(conn, idx):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sync_progress (job, sls_index) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE sls_index = %s
        """, (JOB_NAME, idx, idx))
    conn.commit()


def _load_progress(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT sls_index FROM sync_progress WHERE job=%s", (JOB_NAME,))
        row = cur.fetchone()
        return row["sls_index"] if row else 0


def _clear_progress(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sync_progress WHERE job=%s", (JOB_NAME,))
    conn.commit()


def fetch_assignment_ids(page, kode_sls_list):
    """batch: kode_sls -> assignmentId pertama (representatif utk seluruh SLS itu)."""
    urls = [
        f"{BASE_URL}/app/api/assignment-general/api/assignments/get-principal-values-by-smallest-code/{PERIOD_ID}/{k}"
        for k in kode_sls_list
    ]
    raws = fetch_batch(page, urls)
    out = {}
    for kode, raw in zip(kode_sls_list, raws):
        items = raw.get("data") if isinstance(raw, dict) else (raw if isinstance(raw, list) else None)
        if items:
            first = items[0]
            aid = first.get("assignmentId") if isinstance(first, dict) else None
            if aid:
                out[kode] = aid
    return out


def fetch_structures(page, assignment_ids):
    """batch: assignmentId -> (ppl_email, pml_email)."""
    urls = [
        f"{BASE_URL}/assignment-general/api/assignment-responsibility/get-structure-approval?assignmentId={aid}"
        for aid in assignment_ids
    ]
    raws = fetch_batch(page, urls)
    out = {}
    for aid, raw in zip(assignment_ids, raws):
        officers = raw.get("data") if isinstance(raw, dict) else None
        ppl_email = pml_email = ""
        for o in (officers or []):
            role  = o.get("currentSurveyRoleName")
            email = (o.get("email") or "").strip()
            if role == "Pencacah":
                ppl_email = email
            elif role == "Pengawas":
                pml_email = email
        out[aid] = (ppl_email, pml_email)
    return out


def fetch_all_assignments(sls_codes):
    """Loop chunk SLS (login ulang tiap chunk) + fetch batch konkuren.
    Return: (ppl_by_sls, pml_by_sls) — kode_sls -> email
    """
    conn = connect_db()
    _ensure_progress_table(conn)
    total     = len(sls_codes)
    start_idx = _load_progress(conn)
    if start_idx:
        print(f"[RESUME] lanjut dari index {start_idx}/{total}", flush=True)

    ppl_by_sls, pml_by_sls = {}, {}

    with sync_playwright() as pw:
        browser = make_browser(pw)
        idx = start_idx
        while idx < total:
            chunk = sls_codes[idx: idx + CHUNK_SIZE]
            print(f"\n[chunk SLS {idx}-{idx+len(chunk)-1}/{total}] login ulang...", flush=True)
            page, ctx = login(browser)

            aid_map = {}
            for s in range(0, len(chunk), BATCH_SIZE):
                aid_map.update(fetch_assignment_ids(page, chunk[s:s+BATCH_SIZE]))

            aids = list(aid_map.values())
            struct_map = {}
            for s in range(0, len(aids), BATCH_SIZE):
                struct_map.update(fetch_structures(page, aids[s:s+BATCH_SIZE]))

            got = 0
            for kode, aid in aid_map.items():
                ppl_email, pml_email = struct_map.get(aid, ("", ""))
                if ppl_email:
                    ppl_by_sls[kode] = ppl_email
                if pml_email:
                    pml_by_sls[kode] = pml_email
                if ppl_email or pml_email:
                    got += 1
            print(f"  → {got}/{len(chunk)} SLS terisi (assignmentId ditemukan={len(aid_map)})", flush=True)

            try:
                ctx.close()
            except Exception:
                pass

            idx += CHUNK_SIZE
            _save_progress(conn, idx)
            if idx < total:
                time.sleep(CHUNK_DELAY)

        browser.close()

    _clear_progress(conn)
    conn.close()
    return ppl_by_sls, pml_by_sls


def _sync_role(cur, sls_map, user_map, role, id_key, email_by_sls):
    email_updated = 0
    reassigned    = 0

    email_to_codes = {}
    for kode, email in email_by_sls.items():
        email_to_codes.setdefault(email, []).append(kode)

    for email, codes in email_to_codes.items():
        code_to_id = {k: sls_map[k][id_key] for k in codes if k in sls_map}
        if not code_to_id:
            continue
        local_id = Counter(code_to_id.values()).most_common(1)[0][0]

        current_email = user_map.get(local_id, "")
        if current_email != email:
            cur.execute("UPDATE users SET email=%s WHERE id=%s AND role=%s",
                        (email, local_id, role))
            if cur.rowcount:
                print(f"  [{role}-email] user_id={local_id}: {current_email!r} → {email!r}", flush=True)
                email_updated += 1
            user_map[local_id] = email

        for kode in codes:
            row = sls_map.get(kode)
            if not row or row[id_key] == local_id:
                continue
            cur.execute(f"UPDATE sls SET {id_key}=%s WHERE kode_sls=%s", (local_id, kode))
            if cur.rowcount:
                print(f"  [{role}-sls] {kode}: {id_key} {row[id_key]} → {local_id}", flush=True)
                reassigned += 1
                row[id_key] = local_id

    return email_updated, reassigned


def apply_sync(sls_map, user_map, ppl_by_sls, pml_by_sls):
    conn = connect_db()
    cur  = conn.cursor()

    ppl_email_updated, ppl_reassigned = _sync_role(cur, sls_map, user_map, "ppl", "ppl_id", ppl_by_sls)
    pml_email_updated, pml_reassigned = _sync_role(cur, sls_map, user_map, "pml", "pml_id", pml_by_sls)

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n[DONE] ppl_email_updated={ppl_email_updated} | ppl_reassigned={ppl_reassigned} "
          f"| pml_email_updated={pml_email_updated} | pml_reassigned={pml_reassigned}", flush=True)


def run():
    print(f"=== sync_users.py [{_now()}] ===", flush=True)

    conn = connect_db()
    cur  = conn.cursor()
    cur.execute("SELECT id, kode_sls, ppl_id, pml_id FROM sls ORDER BY kode_sls")
    sls_rows  = cur.fetchall()
    sls_map   = {r["kode_sls"]: r for r in sls_rows}
    sls_codes = list(sls_map.keys())

    cur.execute("SELECT id, email FROM users WHERE role IN ('ppl', 'pml')")
    user_map = {r["id"]: r["email"] for r in cur.fetchall()}
    cur.close()
    conn.close()

    print(f"[DB] {len(sls_codes)} SLS akan dicek ke FASIH", flush=True)
    ppl_by_sls, pml_by_sls = fetch_all_assignments(sls_codes)
    print(f"\n[FASIH] hasil fetch: PPL={len(ppl_by_sls)} | PML={len(pml_by_sls)} dari {len(sls_codes)} SLS", flush=True)

    print(f"\n[SYNC] Mulai update DB...", flush=True)
    apply_sync(sls_map, user_map, ppl_by_sls, pml_by_sls)


if __name__ == "__main__":
    run()
