"""
Sync status keberadaan usaha per-assignment dari FASIH API.
Source: GET /app/api/assignment-general/api/assignment/get-by-assignment-id?assignmentId={id}
Parse answers → dataKey "keberadaan_usaha#{n}" untuk tiap assignment usaha.

Hanya assignment USAHA: data6 (skala_usaha_all) terisi (UMK/UMKM/UMB/dll) → non-empty = usaha

DB table: keberadaan_usaha
  assignment_id, sls_id, nama, skala_usaha, keberadaan_kode, keberadaan_label, synced_at
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
KODE_KAB        = os.getenv("KODE_KABUPATEN",  "5205")
HEADLESS        = os.getenv("HEADLESS",        "false").lower() == "true"

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

BATCH_SIZE    = 20   # assignment per Promise.all batch
CHUNK_SIZE    = 5    # SLS per chunk sebelum re-login
CHUNK_DELAY   = 5    # detik istirahat antar chunk
PROGRESS_FILE = "/app/keberadaan_progress.txt"  # simpan posisi terakhir

WITA = timezone(timedelta(hours=8))


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


# ── DB ───────────────────────────────────────────────────────────────────────

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
              synced_at         DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uk_asgn (assignment_id),
              KEY idx_sls (sls_id),
              CONSTRAINT fk_kebrd_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def load_sls_map(conn):
    """kode_sls_16 → sls_id"""
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls")
        return {r["kode_sls"]: r["id"] for r in cur.fetchall()}


def upsert_keberadaan(conn, sls_id, assignment_id, nama, skala_usaha,
                      kode, label, synced_at):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO keberadaan_usaha
              (sls_id, assignment_id, nama, skala_usaha,
               keberadaan_kode, keberadaan_label, synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              sls_id           = VALUES(sls_id),
              nama             = IF(VALUES(nama) != '' AND VALUES(nama) IS NOT NULL,
                                    VALUES(nama), nama),
              skala_usaha      = IF(VALUES(skala_usaha) != '' AND VALUES(skala_usaha) IS NOT NULL,
                                    VALUES(skala_usaha), skala_usaha),
              keberadaan_kode  = IF(VALUES(keberadaan_kode) IS NOT NULL,
                                    VALUES(keberadaan_kode), keberadaan_kode),
              keberadaan_label = IF(VALUES(keberadaan_label) IS NOT NULL,
                                    VALUES(keberadaan_label), keberadaan_label),
              synced_at        = VALUES(synced_at)
        """, (sls_id, assignment_id, nama or "", skala_usaha or "",
              kode, label, synced_at))
    conn.commit()


# ── Browser ──────────────────────────────────────────────────────────────────

def _make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )
    return browser, ctx


def login_fasih(ctx):
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
    print(f"[FASIH] login OK → {page.url}")
    return page


def _page_fetch(page, url):
    """Fetch satu URL via browser context (bypass F5 bot protection)."""
    try:
        return page.evaluate(f"""async () => {{
            const r = await fetch('{url}', {{credentials:'include'}});
            if (!r.ok) return null;
            return await r.json();
        }}""")
    except Exception:
        return None


def _page_fetch_batch(page, urls):
    """Fetch banyak URL sekaligus via Promise.all (jauh lebih cepat dari sequential)."""
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


# ── FASIH API ─────────────────────────────────────────────────────────────────

def fetch_assignments_per_sls(page, kode_sls):
    """
    Kembalikan list usaha assignments untuk satu SLS.
    Response: {"success":true,"data":[{"assignmentId":...,"data1":nama,"data6":skala_usaha},...]}
    Filter: data6 terisi (UMK/UMKM/UMB/dll) → usaha; kosong → keluarga
    """
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


def _parse_keberadaan(raw_r):
    """Parse satu JSON response get-by-assignment-id → (kode, label)."""
    if not raw_r:
        return None, None
    data_obj = raw_r.get("data") if isinstance(raw_r, dict) else None
    if not data_obj:
        return None, None
    data_str = data_obj.get("data") or ""
    if not data_str:
        return None, None
    try:
        inner = json.loads(data_str) if isinstance(data_str, str) else data_str
    except Exception:
        return None, None
    for item in (inner.get("answers") or []):
        if not isinstance(item, dict):
            continue
        if not item.get("dataKey", "").startswith("keberadaan_usaha#"):
            continue
        ans = item.get("answer")
        if not ans:
            continue
        if isinstance(ans, list) and ans:
            first = ans[0]
            if isinstance(first, dict):
                label_raw = (first.get("label") or "").strip()
                value_raw = str(first.get("value") or "").strip()
                label_clean = label_raw.split(". ", 1)[1].strip() if ". " in label_raw else label_raw
                return value_raw, label_clean
        elif isinstance(ans, str):
            return None, ans.strip()
    return None, None


def fetch_keberadaan_batch(page, assignment_ids):
    """
    Fetch keberadaan untuk banyak assignments sekaligus via Promise.all.
    Return list of (kode, label) sesuai urutan assignment_ids.
    """
    base = f"{FASIH_URL}/app/api/assignment-general/api/assignment/get-by-assignment-id?assignmentId="
    urls = [base + aid for aid in assignment_ids]
    results = _page_fetch_batch(page, urls)
    return [_parse_keberadaan(r) for r in results]


# ── Main ─────────────────────────────────────────────────────────────────────

def _save_progress(chunk_start):
    try:
        with open(PROGRESS_FILE, "w") as f:
            f.write(str(chunk_start))
    except Exception:
        pass


def _load_progress():
    try:
        with open(PROGRESS_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _clear_progress():
    try:
        import os as _os
        _os.remove(PROGRESS_FILE)
    except Exception:
        pass


def run_once():
    synced_at = _now_wita()
    conn      = _connect_db()
    ensure_table(conn)
    sls_map   = load_sls_map(conn)
    sls_list  = list(sls_map.items())
    total_sls = len(sls_list)
    total_chunks = (total_sls + CHUNK_SIZE - 1) // CHUNK_SIZE

    resume_from = _load_progress()
    if resume_from > 0:
        print(f"[{_now_wita()}] Resume dari SLS {resume_from} (chunk {resume_from//CHUNK_SIZE + 1}/{total_chunks})", flush=True)
    else:
        print(f"[{_now_wita()}] Mulai sync keberadaan — {total_sls} SLS, {total_chunks} chunk (@{CHUNK_SIZE} SLS)", flush=True)

    ok          = 0
    null_count  = 0
    total_asgn  = 0

    with sync_playwright() as pw:
        browser, _ = _make_browser(pw)

        for chunk_i, chunk_start in enumerate(range(0, total_sls, CHUNK_SIZE)):
            if chunk_start < resume_from:
                continue
            chunk = sls_list[chunk_start : chunk_start + CHUNK_SIZE]
            print(f"\n[chunk {chunk_i+1}/{total_chunks}] Login...", flush=True)

            # Context baru + login ulang tiap chunk
            ctx  = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            page = login_fasih(ctx)

            pending = []  # kumpulkan semua assignment dari 5 SLS

            for j, (kode_sls, sls_id) in enumerate(chunk):
                global_i = chunk_start + j
                items    = fetch_assignments_per_sls(page, kode_sls)
                usaha    = [it for it in items if it.get("assignment_id")]
                for it in usaha:
                    it["sls_id"] = sls_id
                    pending.append(it)
                print(f"  [{global_i}/{total_sls}] {kode_sls} → {len(usaha)} asgn (buffer={len(pending)})", flush=True)

            # Fetch keberadaan via Promise.all batch untuk semua pending sekaligus
            chunk_ok   = 0
            chunk_null = 0
            if pending:
                for start in range(0, len(pending), BATCH_SIZE):
                    batch           = pending[start : start + BATCH_SIZE]
                    ids             = [a["assignment_id"] for a in batch]
                    keberadaan_list = fetch_keberadaan_batch(page, ids)
                    for asgn, (kode, label) in zip(batch, keberadaan_list):
                        if kode is None and label is None:
                            null_count += 1
                            chunk_null += 1
                        else:
                            ok       += 1
                            chunk_ok += 1
                        upsert_keberadaan(
                            conn,
                            sls_id        = asgn["sls_id"],
                            assignment_id = asgn["assignment_id"],
                            nama          = asgn["nama"],
                            skala_usaha   = asgn["skala_usaha"],
                            kode          = kode,
                            label         = label,
                            synced_at     = synced_at,
                        )
                total_asgn += len(pending)

            print(f"  → simpan {len(pending)} asgn | ok={chunk_ok} null={chunk_null} | total ok={ok}", flush=True)

            try:
                ctx.close()
            except Exception:
                pass

            # Simpan posisi chunk berikutnya supaya bisa resume kalau restart
            next_start = chunk_start + CHUNK_SIZE
            _save_progress(next_start)

            if next_start < total_sls:
                print(f"  [jeda {CHUNK_DELAY}s]", flush=True)
                time.sleep(CHUNK_DELAY)

        browser.close()
    conn.close()

    _clear_progress()  # hapus progress file, run berikutnya mulai dari awal
    print(f"\n[{_now_wita()}] Selesai: total={total_asgn} ok={ok} null={null_count}", flush=True)


def _next_run():
    """Jalankan tiap 7 jam"""
    return 7 * 3600


if __name__ == "__main__":
    print("=== sync_keberadaan.py ===")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback; traceback.print_exc()
        wait = _next_run()
        h, m = divmod(int(wait) // 60, 60)
        print(f"[SCHEDULER] Berikutnya {h}j {m}m lagi ({_now_wita()})")
        time.sleep(wait)
