"""
sync_keberadaan_rev.py — sama persis dengan sync_keberadaan.py
tapi urutan chunk TERBALIK (dari SLS terakhir ke pertama).
Dijalankan paralel di laptop supaya ketemu di tengah.
Progress disimpan di DB dengan job='keberadaan_rev'.
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

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

BATCH_SIZE  = 20
CHUNK_SIZE  = 5
CHUNK_DELAY = 5
JOB_NAME    = "keberadaan_rev"

WITA = timezone(timedelta(hours=8))


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def load_sls_map(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, kode_sls FROM sls ORDER BY kode_sls")
        return [(r["kode_sls"], r["id"]) for r in cur.fetchall()]


def upsert_keberadaan(conn, sls_id, assignment_id, nama, skala_usaha,
                      kode, label, gate_label, assignment_status, synced_at):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO keberadaan_usaha
              (sls_id, assignment_id, nama, skala_usaha,
               keberadaan_kode, keberadaan_label, gate_label, assignment_status, synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
              synced_at         = VALUES(synced_at)
        """, (sls_id, assignment_id, nama or "", skala_usaha or "",
              kode, label, gate_label, assignment_status, synced_at))
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


def _save_progress(conn, chunk_start):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sync_progress (job, sls_index) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE sls_index = %s
        """, (JOB_NAME, chunk_start, chunk_start))
    conn.commit()


def _load_progress(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sls_index FROM sync_progress WHERE job=%s", (JOB_NAME,))
            row = cur.fetchone()
            return row["sls_index"] if row else None  # None = belum ada, mulai dari akhir
    except Exception:
        return None


def _clear_progress(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sync_progress WHERE job=%s", (JOB_NAME,))
    conn.commit()


def _read_frontier(conn, job):
    """Baca posisi sls_index job lain (dipakai buat deteksi auto-stop saat forward &
    reverse ketemu di tengah). None kalau job itu belum pernah jalan / progressnya
    sudah bersih (selesai atau belum mulai)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sls_index FROM sync_progress WHERE job=%s", (job,))
            row = cur.fetchone()
            return row["sls_index"] if row else None
    except Exception:
        return None


def _make_browser(pw):
    browser = pw.chromium.launch(
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
    )
    return browser


def login_fasih(browser):
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )
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
    return page, ctx


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
            return await Promise.all(urls.map(u =>
                fetch(u, {{credentials:'include'}})
                .then(r => r.ok ? r.json() : null)
                .catch(() => null)
            ));
        }}""")
    except Exception:
        return [None] * len(urls)


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


def _parse_assignment(raw_r):
    """Parse satu JSON response get-by-assignment-id →
    (kode, label, gate_label, assignment_status). Lihat sync_keberadaan.py untuk detail."""
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

        if gate_label is None and isinstance(ans, list) and len(ans) == 1:
            first = ans[0]
            if isinstance(first, dict):
                gl = (first.get("label") or "")
                if "(stop)" in gl.lower():
                    gate_label = gl.strip()

    return kode, label, gate_label, assignment_status


def fetch_keberadaan_batch(page, assignment_ids):
    base    = f"{FASIH_URL}/app/api/assignment-general/api/assignment/get-by-assignment-id?assignmentId="
    urls    = [base + aid for aid in assignment_ids]
    results = _page_fetch_batch(page, urls)
    return [_parse_assignment(r) for r in results]


def run_once():
    synced_at    = _now_wita()
    conn         = _connect_db()
    _ensure_progress_table(conn)
    sls_list     = load_sls_map(conn)          # urutan ASC (kode_sls kecil → besar)
    total_sls    = len(sls_list)
    all_starts   = list(range(0, total_sls, CHUNK_SIZE))
    total_chunks = len(all_starts)

    # Chunk yang sudah diproses dari bawah — resume dari sls_index tersimpan
    saved = _load_progress(conn)
    if saved is None:
        # Mulai dari chunk paling bawah
        resume_from = all_starts[-1]
        print(f"[{_now_wita()}] Mulai sync REV — {total_sls} SLS, {total_chunks} chunk, dari bawah", flush=True)
    else:
        resume_from = saved
        chunk_no    = all_starts.index(resume_from) + 1 if resume_from in all_starts else "?"
        print(f"[{_now_wita()}] Resume REV dari SLS {resume_from} (chunk ~{chunk_no}/{total_chunks})", flush=True)

    # Urutan terbalik: besar → kecil, hanya chunk_start <= resume_from
    chunks_to_do = [s for s in reversed(all_starts) if s <= resume_from]

    ok         = 0
    null_count = 0
    total_asgn = 0

    with sync_playwright() as pw:
        browser = _make_browser(pw)

        for chunk_start in chunks_to_do:
            # Auto-stop kalau sudah ketemu proses forward (dia jalan dari SLS
            # pertama ke akhir) — hindari dua proses balapan memproses ulang area sama.
            fwd_frontier = _read_frontier(conn, "keberadaan")
            if fwd_frontier is not None and chunk_start < fwd_frontier:
                print(f"\n[{_now_wita()}] Ketemu proses forward di SLS {chunk_start} "
                      f"(forward sudah sampai {fwd_frontier}) — auto-stop, gabungan sudah cover semua SLS.", flush=True)
                break

            chunk    = sls_list[chunk_start : chunk_start + CHUNK_SIZE]
            chunk_no = total_chunks - all_starts.index(chunk_start)
            print(f"\n[chunk ↑{chunk_no}/{total_chunks} | SLS {chunk_start}-{chunk_start+len(chunk)-1}] Login...", flush=True)

            page, ctx = login_fasih(browser)
            pending   = []

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
                    for asgn, (kode, label, gate_label, assignment_status) in zip(batch, keberadaan_list):
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
                        )
                total_asgn += len(pending)

            print(f"  → simpan {len(pending)} asgn | ok={chunk_ok} null={chunk_null} | total ok={ok}", flush=True)

            try:
                ctx.close()
            except Exception:
                pass

            # Simpan chunk SEBELUMNYA sebagai resume point (karena kita jalan ke bawah→atas)
            next_start = chunk_start - CHUNK_SIZE
            if next_start >= 0:
                _save_progress(conn, next_start)
                print(f"  [jeda {CHUNK_DELAY}s]", flush=True)
                time.sleep(CHUNK_DELAY)

        browser.close()

    _clear_progress(conn)
    conn.close()
    print(f"\n[{_now_wita()}] Selesai REV: total={total_asgn} ok={ok} null={null_count}", flush=True)


if __name__ == "__main__":
    print("=== sync_keberadaan_rev.py ===")
    try:
        run_once()
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback; traceback.print_exc()
    print(f"[{_now_wita()}] Done.")
