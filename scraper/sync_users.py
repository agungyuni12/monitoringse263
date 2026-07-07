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
CHUNK_SIZE  = 50    # SLS per chunk (login ulang tiap chunk) — diperkecil dari 150
                     # supaya tiap chunk lebih cepat kelar & progress ke-checkpoint
                     # lebih rapat (apply_sync per chunk, lihat fetch_all_assignments).
CHUNK_DELAY = 5
LOGIN_MAX_RETRY   = 3
LOGIN_RETRY_DELAY = 15  # detik
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


def login_with_retry(browser, max_retries=LOGIN_MAX_RETRY, retry_delay=LOGIN_RETRY_DELAY):
    """login() ke FASIH kadang timeout (sesi Keycloak lambat/flaky). Coba ulang
    beberapa kali dengan jeda dulu sebelum benar-benar menyerah, supaya 1 kali
    gagal login tidak langsung mematikan seluruh proses (crash & restart mahal
    karena sesi FASIH & data chunk sebelumnya jadi ikut hilang)."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return login(browser)
        except Exception as e:
            last_err = e
            print(f"  [LOGIN] gagal (percobaan {attempt}/{max_retries}): {e}", flush=True)
            if attempt < max_retries:
                time.sleep(retry_delay)
    raise last_err


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


def fetch_all_assignments(sls_codes, sls_map, id_to_email, id_to_name, ppl_email_to_id, pml_email_to_id):
    """Loop chunk SLS (login ulang tiap chunk) + fetch batch konkuren, lalu langsung
    apply_sync ke database PER CHUNK (bukan ditumpuk lalu disimpan sekali di akhir).
    Kalau proses ini crash/di-restart di tengah jalan, chunk yang sudah berhasil
    di-fetch & disimpan TIDAK ikut hilang — resume lewat tabel sync_progress cuma
    perlu lanjut dari chunk berikutnya, bukan mengulang dari awal.
    Return: dict akumulasi jumlah update (ppl_email_updated, ppl_reassigned, dst).
    """
    conn = connect_db()
    _ensure_progress_table(conn)
    total     = len(sls_codes)
    start_idx = _load_progress(conn)
    if start_idx:
        print(f"[RESUME] lanjut dari index {start_idx}/{total}", flush=True)

    totals = {"ppl_email_updated": 0, "ppl_reassigned": 0, "ppl_created": 0,
              "pml_email_updated": 0, "pml_reassigned": 0, "pml_created": 0}

    with sync_playwright() as pw:
        browser = make_browser(pw)
        idx = start_idx
        while idx < total:
            chunk = sls_codes[idx: idx + CHUNK_SIZE]
            print(f"\n[chunk SLS {idx}-{idx+len(chunk)-1}/{total}] login ulang...", flush=True)
            page, ctx = login_with_retry(browser)

            aid_map = {}
            for s in range(0, len(chunk), BATCH_SIZE):
                aid_map.update(fetch_assignment_ids(page, chunk[s:s+BATCH_SIZE]))

            aids = list(aid_map.values())
            struct_map = {}
            for s in range(0, len(aids), BATCH_SIZE):
                struct_map.update(fetch_structures(page, aids[s:s+BATCH_SIZE]))

            ppl_by_sls, pml_by_sls = {}, {}
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

            counts = apply_sync(sls_map, id_to_email, id_to_name, ppl_email_to_id, pml_email_to_id,
                                 ppl_by_sls, pml_by_sls)
            for k, v in counts.items():
                totals[k] += v

            idx += CHUNK_SIZE
            _save_progress(conn, idx)
            if idx < total:
                time.sleep(CHUNK_DELAY)

        browser.close()

    _clear_progress(conn)
    conn.close()
    return totals


PLACEHOLDER_NAME     = "(belum diisi)"
PLACEHOLDER_PASSWORD = "!DISABLED-NEEDS-RESET!"  # bukan bcrypt valid -> login otomatis gagal


def _create_user(cur, role, email):
    """Bikin user baru placeholder (nama & password menyusul diisi manual)."""
    username = email[:50]
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, role, name, email) VALUES (%s,%s,%s,%s,%s)",
            (username, PLACEHOLDER_PASSWORD, role, PLACEHOLDER_NAME, email),
        )
        return cur.lastrowid
    except Exception as e:
        print(f"  [WARN] {role}: gagal bikin user baru utk {email!r}: {e}", flush=True)
        return None


def _sync_role(cur, sls_map, id_to_email, id_to_name, email_to_id, role, id_key, email_by_sls):
    """
    1 SLS = 1 petugas langsung dari FASIH (get-structure-approval), jadi tiap kode SLS
    diproses independen — tidak digabung/divote lintas SLS. Identitas local user
    ditentukan dari users.email yang sudah terdaftar. Kalau email dari FASIH belum ada
    match di users, dibikinkan row user baru (nama placeholder, diisi manual belakangan)
    supaya SLS-nya tetap ke-assign ke identitas yang benar.
    """
    email_updated  = 0
    reassigned     = 0
    users_created  = 0

    for kode, email in email_by_sls.items():
        row = sls_map.get(kode)
        if not row:
            continue

        local_id = email_to_id.get(email)
        if local_id is None:
            local_id = _create_user(cur, role, email)
            if local_id is None:
                continue
            print(f"  [{role}-new] user_id={local_id}: dibuat baru utk email {email!r} (nama menyusul)", flush=True)
            users_created += 1
            id_to_email[local_id] = email
            id_to_name[local_id]  = PLACEHOLDER_NAME
            email_to_id[email]    = local_id

        nama = id_to_name.get(local_id, "?")
        current_email = id_to_email.get(local_id, "")
        if current_email != email:
            cur.execute("UPDATE users SET email=%s WHERE id=%s AND role=%s",
                        (email, local_id, role))
            if cur.rowcount:
                print(f"  [{role}-email] user_id={local_id} ({nama}): {current_email!r} → {email!r}", flush=True)
                email_updated += 1
            id_to_email[local_id] = email

        if row[id_key] != local_id:
            print(f"  [{role}-sls] {kode}: {id_key} {row[id_key]} ({id_to_name.get(row[id_key], '?')}) "
                  f"→ {local_id} ({nama}) [{email}]", flush=True)
            cur.execute(f"UPDATE sls SET {id_key}=%s WHERE kode_sls=%s", (local_id, kode))
            if cur.rowcount:
                reassigned += 1
                row[id_key] = local_id

    return email_updated, reassigned, users_created


def apply_sync(sls_map, id_to_email, id_to_name, ppl_email_to_id, pml_email_to_id, ppl_by_sls, pml_by_sls):
    """Simpan hasil fetch 1 chunk ke database. Dipanggil per chunk (lihat
    fetch_all_assignments) supaya tidak ada data yang ditumpuk lalu hilang kalau
    proses crash sebelum sempat commit."""
    conn = connect_db()
    cur  = conn.cursor()

    ppl_email_updated, ppl_reassigned, ppl_created = _sync_role(
        cur, sls_map, id_to_email, id_to_name, ppl_email_to_id, "ppl", "ppl_id", ppl_by_sls)
    pml_email_updated, pml_reassigned, pml_created = _sync_role(
        cur, sls_map, id_to_email, id_to_name, pml_email_to_id, "pml", "pml_id", pml_by_sls)

    conn.commit()
    cur.close()
    conn.close()

    return {
        "ppl_email_updated": ppl_email_updated, "ppl_reassigned": ppl_reassigned, "ppl_created": ppl_created,
        "pml_email_updated": pml_email_updated, "pml_reassigned": pml_reassigned, "pml_created": pml_created,
    }


def run():
    print(f"=== sync_users.py [{_now()}] ===", flush=True)

    conn = connect_db()
    cur  = conn.cursor()
    cur.execute("SELECT id, kode_sls, ppl_id, pml_id FROM sls ORDER BY kode_sls")
    sls_rows  = cur.fetchall()
    sls_map   = {r["kode_sls"]: r for r in sls_rows}
    sls_codes = list(sls_map.keys())

    cur.execute("SELECT id, email, name, role FROM users WHERE role IN ('ppl', 'pml')")
    user_rows       = cur.fetchall()
    id_to_email     = {r["id"]: (r["email"] or "") for r in user_rows}
    id_to_name      = {r["id"]: r["name"] for r in user_rows}
    ppl_email_to_id = {r["email"]: r["id"] for r in user_rows if r["role"] == "ppl" and r["email"]}
    pml_email_to_id = {r["email"]: r["id"] for r in user_rows if r["role"] == "pml" and r["email"]}
    cur.close()
    conn.close()

    print(f"[DB] {len(sls_codes)} SLS akan dicek ke FASIH (disimpan langsung per chunk)", flush=True)
    totals = fetch_all_assignments(sls_codes, sls_map, id_to_email, id_to_name, ppl_email_to_id, pml_email_to_id)

    print(f"\n[DONE] ppl_email_updated={totals['ppl_email_updated']} | ppl_reassigned={totals['ppl_reassigned']} "
          f"| ppl_created={totals['ppl_created']} | pml_email_updated={totals['pml_email_updated']} "
          f"| pml_reassigned={totals['pml_reassigned']} | pml_created={totals['pml_created']}", flush=True)


if __name__ == "__main__":
    run()
