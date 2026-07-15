"""
sync_keberadaan_kilo.py — sama seperti sync_keberadaan.py, tapi discope
hanya ke SLS kecamatan Kilo (Kab. Dompu). Dipakai untuk sync satu kecamatan
saja tanpa mengganggu progress job 'keberadaan'/'keberadaan_rev' yang cover
semua kecamatan.

Progress disimpan terpisah di sync_progress dengan job='keberadaan_kilo'
(single-pass, tidak ada pasangan forward/reverse).

DB table: keberadaan_usaha (sama seperti sync_keberadaan.py)
"""

import os, time, json
import pymysql
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

FASIH_URL       = "https://fasih-sm.bps.go.id"
FASIH_USER      = os.getenv("FASIH_USER",      "agung.yuniarta")
FASIH_PASS      = os.getenv("FASIH_PASS",      "kelayu1998")
FASIH_PERIOD_ID = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")
HEADLESS        = os.getenv("HEADLESS",        "false").lower() == "true"
NAMA_KEC        = os.getenv("SYNC_KECAMATAN",  "Kilo")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

BATCH_SIZE  = 20
CHUNK_SIZE  = 5
CHUNK_DELAY = 5
JOB_NAME    = "keberadaan_kilo"
DONE        = -1

WITA = timezone(timedelta(hours=8))


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS keberadaan_usaha (
              id                BIGINT NOT NULL AUTO_INCREMENT,
              sls_id            INT NOT NULL,
              assignment_id     VARCHAR(36) NOT NULL,
              nama              VARCHAR(255) DEFAULT NULL,
              skala_usaha       VARCHAR(100) DEFAULT NULL,
              keberadaan_kode   VARCHAR(10) DEFAULT NULL,
              keberadaan_label  VARCHAR(100) DEFAULT NULL,
              gate_label        VARCHAR(150) DEFAULT NULL,
              assignment_status VARCHAR(50) DEFAULT NULL,
              synced_at         DATETIME DEFAULT NULL,
              sync_keterangan   VARCHAR(255) DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uk_asgn (assignment_id),
              KEY idx_sls (sls_id),
              CONSTRAINT fk_kebrd_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def load_sls_map(conn):
    """kode_sls → sls_id, hanya untuk kecamatan NAMA_KEC."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls WHERE nama_kec = %s ORDER BY kode_sls", (NAMA_KEC,))
        return [(r["kode_sls"], r["id"]) for r in cur.fetchall()]


def upsert_keberadaan(conn, sls_id, assignment_id, nama, skala_usaha,
                      kode, label, gate_label, assignment_status, synced_at,
                      sync_keterangan=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO keberadaan_usaha
              (sls_id, assignment_id, nama, skala_usaha,
               keberadaan_kode, keberadaan_label, gate_label, assignment_status, synced_at,
               sync_keterangan)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              sls_id            = VALUES(sls_id),
              nama              = IF(VALUES(nama) != '' AND VALUES(nama) IS NOT NULL,
                                     VALUES(nama), nama),
              skala_usaha       = IF(VALUES(skala_usaha) != '' AND VALUES(skala_usaha) IS NOT NULL,
                                     VALUES(skala_usaha), skala_usaha),
              keberadaan_kode   = IF(VALUES(keberadaan_kode) IS NOT NULL,
                                     VALUES(keberadaan_kode), keberadaan_kode),
              keberadaan_label  = IF(VALUES(keberadaan_label) IS NOT NULL,
                                     VALUES(keberadaan_label), keberadaan_label),
              gate_label        = IF(VALUES(gate_label) IS NOT NULL,
                                     VALUES(gate_label), gate_label),
              assignment_status = IF(VALUES(assignment_status) IS NOT NULL,
                                     VALUES(assignment_status), assignment_status),
              synced_at         = VALUES(synced_at),
              sync_keterangan   = VALUES(sync_keterangan)
        """, (sls_id, assignment_id, nama or "", skala_usaha or "",
              kode, label, gate_label, assignment_status, synced_at, sync_keterangan))
    conn.commit()


def _ensure_progress_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_progress (
                job       VARCHAR(50) PRIMARY KEY,
                sls_index INT NOT NULL DEFAULT 0
            ) ENGINE=InnoDB
        """)
    conn.commit()


def _save_progress(conn, sls_index):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sync_progress (job, sls_index) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE sls_index = %s
        """, (JOB_NAME, sls_index, sls_index))
    conn.commit()


def _load_progress(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sls_index FROM sync_progress WHERE job=%s", (JOB_NAME,))
            row = cur.fetchone()
            if not row or row["sls_index"] == DONE:
                return 0
            return row["sls_index"]
    except Exception:
        return 0


def _mark_done(conn):
    _save_progress(conn, DONE)


def _make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
    )
    return browser


LOGIN_MAX_RETRY   = 3
LOGIN_RETRY_DELAY = 15


def _do_login(ctx):
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)
    try:
        page.goto(f"{FASIH_URL}/oauth2/authorization/ics",
                  wait_until="networkidle", timeout=90_000)
    except Exception:
        pass
    time.sleep(3)
    page.wait_for_selector("input[name='username']", timeout=90_000)
    page.fill("input[name='username']", FASIH_USER)
    page.fill("input[name='password']", FASIH_PASS)
    page.click("#kc-login, input[type='submit']")
    for _ in range(30):
        time.sleep(2)
        u = page.url
        if "fasih-sm.bps.go.id" in u and "login" not in u and "oauth2" not in u:
            break
    time.sleep(3)
    print(f"[FASIH] login OK → {page.url}", flush=True)
    return page


def login_fasih(browser):
    last_err = None
    for attempt in range(1, LOGIN_MAX_RETRY + 1):
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        try:
            page = _do_login(ctx)
            return page, ctx
        except Exception as e:
            last_err = e
            try:
                ctx.close()
            except Exception:
                pass
            print(f"[FASIH] login gagal (percobaan {attempt}/{LOGIN_MAX_RETRY}): {e}", flush=True)
            if attempt < LOGIN_MAX_RETRY:
                print(f"  [jeda {LOGIN_RETRY_DELAY}s sebelum coba lagi]", flush=True)
                time.sleep(LOGIN_RETRY_DELAY)
    raise last_err


def _page_fetch(page, url):
    try:
        return page.evaluate(f"""async () => {{
            const r = await fetch('{url}', {{credentials:'include'}});
            if (!r.ok) return null;
            return await r.json();
        }}""")
    except Exception:
        return None


def _page_fetch_batch(page, urls):
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


def fetch_assignments_per_sls(page, kode_sls):
    url = (f"{FASIH_URL}/assignment-general/api/assignments"
           f"/get-principal-values-by-smallest-code/{FASIH_PERIOD_ID}/{kode_sls}")
    raw = _page_fetch(page, url)
    if not raw:
        return []
    items = raw.get("data") if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    results = []
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        asgn_id = item.get("assignmentId") or item.get("id") or ""
        if not asgn_id:
            continue
        skala = (item.get("data6") or "").strip()
        results.append({
            "assignment_id": asgn_id,
            "nama":          (item.get("data1") or "").strip(),
            "skala_usaha":   skala,
        })
    return results


GATE_FIELDS = ("ada_keluarga", "pilih_umkm")


def _parse_assignment(raw_r):
    if not raw_r:
        return None, None, None, None
    data_obj = raw_r.get("data") if isinstance(raw_r, dict) else None
    if not data_obj:
        return None, None, None, None

    assignment_status = data_obj.get("assignment_status_alias")

    data_str = data_obj.get("data") or ""
    if not data_str:
        return None, None, None, assignment_status
    try:
        inner = json.loads(data_str) if isinstance(data_str, str) else data_str
    except Exception:
        return None, None, None, assignment_status

    kode, label, gate_label = None, None, None

    for item in (inner.get("answers") or []):
        if not isinstance(item, dict):
            continue
        key = item.get("dataKey", "") or ""
        ans = item.get("answer")
        if not ans:
            continue

        if key.startswith("keberadaan_usaha#"):
            if isinstance(ans, list) and ans:
                first = ans[0]
                if isinstance(first, dict):
                    label_raw   = (first.get("label") or "").strip()
                    value_raw   = str(first.get("value") or "").strip()
                    label_clean = label_raw.split(". ", 1)[1].strip() if ". " in label_raw else label_raw
                    kode, label = value_raw, label_clean
            elif isinstance(ans, str):
                kode, label = None, ans.strip()
            continue

        if key in GATE_FIELDS and gate_label is None and isinstance(ans, list) and ans:
            first = ans[0]
            if isinstance(first, dict):
                label_raw = (first.get("label") or "").strip()
                gl_lower = label_raw.lower()
                if "(stop)" in gl_lower or "tidak ditemukan" in gl_lower or "baru" in gl_lower:
                    gate_label = label_raw

    return kode, label, gate_label, assignment_status


def fetch_keberadaan_batch(page, assignment_ids):
    base    = f"{FASIH_URL}/app/api/assignment-general/api/assignment/get-by-assignment-id?assignmentId="
    urls    = [base + aid for aid in assignment_ids]
    results = _page_fetch_batch(page, urls)
    parsed = []
    for aid, r in zip(assignment_ids, results):
        if isinstance(r, dict) and "__fetch_error" in r:
            print(f"      [FETCH FAIL] {aid[:8]}… : {r['__fetch_error']}", flush=True)
            parsed.append((None, None, None, None, f"Gagal: {r['__fetch_error']}"))
        else:
            kode, label, gate_label, assignment_status = _parse_assignment(r)
            parsed.append((kode, label, gate_label, assignment_status, None))
    return parsed


def run_once():
    synced_at = _now_wita()
    conn      = _connect_db()
    ensure_table(conn)
    _ensure_progress_table(conn)
    sls_list  = load_sls_map(conn)
    total_sls = len(sls_list)
    total_chunks = (total_sls + CHUNK_SIZE - 1) // CHUNK_SIZE

    if total_sls == 0:
        print(f"[{_now_wita()}] Tidak ada SLS untuk kecamatan '{NAMA_KEC}'. Cek nama_kec di tabel sls.", flush=True)
        conn.close()
        return

    resume_from = _load_progress(conn)
    if resume_from > 0:
        print(f"[{_now_wita()}] Resume sync '{NAMA_KEC}' dari SLS {resume_from} "
              f"(chunk {resume_from//CHUNK_SIZE + 1}/{total_chunks})", flush=True)
    else:
        print(f"[{_now_wita()}] Mulai sync keberadaan kecamatan '{NAMA_KEC}' — "
              f"{total_sls} SLS, {total_chunks} chunk (@{CHUNK_SIZE} SLS)", flush=True)

    ok         = 0
    null_count = 0
    total_asgn = 0

    with sync_playwright() as pw:
        browser = _make_browser(pw)

        for chunk_i, chunk_start in enumerate(range(0, total_sls, CHUNK_SIZE)):
            if chunk_start < resume_from:
                continue

            chunk = sls_list[chunk_start : chunk_start + CHUNK_SIZE]
            print(f"\n[chunk {chunk_i+1}/{total_chunks}] Login...", flush=True)

            page, ctx = login_fasih(browser)
            pending = []

            for j, (kode_sls, sls_id) in enumerate(chunk):
                global_i = chunk_start + j
                items    = fetch_assignments_per_sls(page, kode_sls)
                usaha    = [it for it in items if it.get("assignment_id")]
                for it in usaha:
                    it["sls_id"] = sls_id
                    pending.append(it)
                print(f"  [{global_i}/{total_sls}] {kode_sls} → {len(usaha)} asgn (buffer={len(pending)})", flush=True)

            chunk_ok   = 0
            chunk_null = 0
            if pending:
                for start in range(0, len(pending), BATCH_SIZE):
                    batch           = pending[start : start + BATCH_SIZE]
                    ids             = [a["assignment_id"] for a in batch]
                    keberadaan_list = fetch_keberadaan_batch(page, ids)
                    for asgn, (kode, label, gate_label, assignment_status, sync_keterangan) in zip(batch, keberadaan_list):
                        if kode is None and label is None and gate_label is None:
                            null_count += 1
                            chunk_null += 1
                        else:
                            ok       += 1
                            chunk_ok += 1
                        upsert_keberadaan(
                            conn,
                            sls_id            = asgn["sls_id"],
                            assignment_id     = asgn["assignment_id"],
                            nama              = asgn["nama"],
                            skala_usaha       = asgn["skala_usaha"],
                            kode              = kode,
                            label             = label,
                            gate_label        = gate_label,
                            assignment_status = assignment_status,
                            synced_at         = synced_at,
                            sync_keterangan   = sync_keterangan,
                        )
                total_asgn += len(pending)

            print(f"  → simpan {len(pending)} asgn | ok={chunk_ok} null={chunk_null} | total ok={ok}", flush=True)

            try:
                ctx.close()
            except Exception:
                pass

            next_start = chunk_start + CHUNK_SIZE
            _save_progress(conn, next_start)

            if next_start < total_sls:
                print(f"  [jeda {CHUNK_DELAY}s]", flush=True)
                time.sleep(CHUNK_DELAY)

        browser.close()

    _mark_done(conn)
    conn.close()
    print(f"\n[{_now_wita()}] Selesai sync '{NAMA_KEC}': total={total_asgn} ok={ok} null={null_count}", flush=True)


if __name__ == "__main__":
    print(f"=== sync_keberadaan_kilo.py (kecamatan={NAMA_KEC}) ===")
    try:
        run_once()
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback; traceback.print_exc()
    print(f"[{_now_wita()}] Done.")
