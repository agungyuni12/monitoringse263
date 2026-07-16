"""
Sync data GROUNDCHECK PELANGGAN LISTRIK PT PLN (PERSERO) - PRABAYAR dari FASIH → se2026.

Endpoint: POST /analytic/api/v2/assignment/datatable-all-user-survey-periode
Field mapping (dari /assignment-sync/api/mobile/template/custom-data/{templateId}):
  data2 = r103  = Nama pelanggan (SUDAH TERMASKING dari sumber PLN, mis. "E*I S******I" —
                   bukan dimasking oleh script ini, tidak ada cara mendapat versi lengkapnya
                   lewat FASIH)
  data3 = r101a = ID Pelanggan (utuh, tidak dimasking)
  assignmentStatusAlias = status alur FASIH (OPEN/DRAFT/SUBMITTED BY Pencacah/dst)

Strategi paginasi — PENTING:
Endpoint ini (dan bahkan UI FASIH sendiri — "Showing 1 to 10 of 10,000 entries", max 1.000
halaman) TIDAK BISA mengambil baris di posisi ranking ke-~999 ke atas dalam SATU kombinasi
filter+sort, berapa pun total data yang cocok. Ini dikonfirmasi berlaku persis sama di posisi
~999 terlepas dari filter yang dipakai — kemungkinan besar limit index/WAF yang disengaja,
bukan bug.

Karena itu paginasi dilakukan 2 lapis:
  1. Partisi UTAMA per PENCACAH (currentUserId) — didapat dari endpoint yang sama dipakai
     sync_fasih.py (report-progress-by-responsibility). Partisi ini EXHAUSTIVE (setiap
     assignment pasti punya satu currentUserId) dan TIDAK overlap.
  2. Untuk pencacah yang assignment-nya ≤ batas (~990) dalam SATU request: cukup 1 request,
     dijamin lengkap.
  3. Untuk pencacah yang totalnya MELEBIHI batas (ada beberapa akun "biller" PLN dengan
     ribuan assignment per orang): ambil UNION dari beberapa kombinasi sort (id/data1/data2/
     data3/data4 × asc/desc). Setiap kombinasi sort mengembalikan ~990 baris "teratas"
     berbeda, jadi gabungan beberapa sort menutupi jauh lebih banyak dibanding satu query saja
     (diuji manual: 1 sort session → ~31% dari satu bucket 3193 baris, 10 kombinasi sort →
     98.4%). Filter data4 (alamat) SENGAJA TIDAK dipakai sebagai kunci partisi utama karena
     field itu teks bebas dari pencacah (nama dusun/RT-RW, bukan nama desa resmi) — hanya
     ~40% baris yang cocok exact-match ke nama desa resmi.

     Coverage per pencacah besar TIDAK dijamin 100% (batasan arsitektur FASIH, bukan bug di
     sini) — sisa yang tidak ketangkap dicatat sebagai PERINGATAN di log, bukan hilang diam-
     diam.

Env vars:
  FASIH_USER, FASIH_PASS, DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME — sama seperti sync
  lain di project ini.
"""

import os, json, time
import pymysql
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

BASE_URL     = "https://fasih-sm.bps.go.id"
SURVEY_ID    = "2395b67d-d1af-4739-9ef8-c0cc0aa9ce9a"
PERIOD_ID    = "16acea4e-4710-43d1-8b00-eeee589c8b66"
DOMPU_ULP_ID = "5fa041d9-15c0-4742-90ac-8056c9620be4"                # region3Id — ULP DOMPU
PENCACAH_ROLE_ID = "34daa2b9-0ee3-4a52-97ef-2b6d00dbac93"

FASIH_USER = os.getenv("FASIH_USER", "agung.yuniarta")
FASIH_PASS = os.getenv("FASIH_PASS", "kelayu1998")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

PAGE_LENGTH  = 990   # aman di bawah batas deep-pagination ~999 yang diamati
CHUNK_SIZE   = 4      # pencacah per login sebelum context+login baru
CHUNK_DELAY  = 4      # detik istirahat antar chunk
ROW_DELAY    = 0.3    # detik jeda antar request

# Kolom yang diminta ke datatable, sekaligus jadi index buat kombinasi sort di bawah.
COLUMNS = ["id", "codeIdentity", "data1", "data2", "data3", "data4"]
#            0        1            2        3        4        5
# Kombinasi sort dipakai kalau 1 request default (sort id asc) sudah kepotong batas.
# id/codeIdentity terbukti tidak benar-benar bisa di-sort beda arah oleh backend (asc==desc),
# jadi tidak diulang — cukup data1..data4 x asc/desc (paling efektif dari hasil uji coba).
EXTRA_SORT_COMBOS = [(2, "asc"), (2, "desc"), (4, "asc"), (4, "desc"),
                     (5, "asc"), (5, "desc"), (3, "asc"), (3, "desc")]

WITA = timezone(timedelta(hours=8))


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


# ── DB ───────────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gc_pln_prabayar (
              id             BIGINT NOT NULL AUTO_INCREMENT,
              assignment_id  VARCHAR(36) NOT NULL,
              id_pelanggan   VARCHAR(50) DEFAULT NULL,
              nama           VARCHAR(255) DEFAULT NULL,
              status         VARCHAR(50) DEFAULT NULL,
              synced_at      DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uk_asgn (assignment_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()


def upsert_batch(conn, rows, synced_at):
    if not rows:
        return 0, 0
    inserted = updated = 0
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO gc_pln_prabayar (assignment_id, id_pelanggan, nama, status, synced_at)
                VALUES (%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  id_pelanggan = VALUES(id_pelanggan),
                  nama         = VALUES(nama),
                  status       = VALUES(status),
                  synced_at    = VALUES(synced_at)
            """, (r["assignment_id"], r["id_pelanggan"], r["nama"], r["status"], synced_at))
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated


# ── Browser / login (pola identik sync_fasih.py) ───────────────────────────────

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
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)

    LONG = 90_000
    try:
        page.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    except Exception:
        pass

    active = page
    if page.url in ("about:blank", ""):
        active = ctx.new_page()
        _stealth.apply_stealth_sync(active)

    active.goto(f"{BASE_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    active.wait_for_selector("#kc-form-login", timeout=LONG)
    active.fill("#username", FASIH_USER)
    active.fill("#password", FASIH_PASS)
    active.click("#kc-login")
    active.wait_for_url("**fasih-sm.bps.go.id**", timeout=LONG)

    cookies = ctx.cookies()
    xsrf = next((c["value"] for c in cookies if c["name"] == "XSRF-TOKEN"), "")
    if not xsrf:
        raise RuntimeError("Login gagal – tidak ada XSRF token")
    return active, xsrf


# ── FASIH API ────────────────────────────────────────────────────────────────

def list_pencacah(ctx, xsrf, retries=3):
    """Daftar pencacah (currentUserId) yang punya assignment di ULP DOMPU, lengkap
    dengan total assignment masing-masing — dari endpoint yang sama dipakai
    sync_fasih.py buat progress SE2026."""
    people = []
    page_num = 0
    while True:
        payload = {
            "surveyPeriodId": PERIOD_ID, "surveyRoleId": PENCACAH_ROLE_ID,
            "size": 10, "page": page_num, "search": "", "target": "TARGET_ONLY",
            "region": {"region1Id": None, "region2Id": None, "region3Id": DOMPU_ULP_ID,
                       "region4Id": None, "region5Id": None, "region6Id": None, "region7Id": None,
                       "region8Id": None, "region9Id": None, "region10Id": None},
            "regionSummaryLevel": 6,
        }
        hdrs = {"Accept": "application/json, */*", "Content-Type": "application/json",
                "X-XSRF-TOKEN": xsrf, "Referer": f"{BASE_URL}/survey-collection/collect/{SURVEY_ID}",
                "Origin": BASE_URL}
        d = None
        for attempt in range(1, retries + 1):
            try:
                r = ctx.request.post(f"{BASE_URL}/analytic/api/v2/assignment/report-progress-by-responsibility",
                                      data=json.dumps(payload), headers=hdrs, timeout=60000)
                if r.status == 200:
                    d = r.json()
                    break
            except Exception as e:
                print(f"  [RETRY {attempt}/{retries}] list_pencacah page {page_num}: {e}", flush=True)
            time.sleep(3 * attempt)
        if d is None or not d.get("success"):
            break
        inner = d.get("data", {})
        content = inner.get("content", [])
        people.extend(content)
        total_elements = inner.get("totalElements") or 0
        if (page_num + 1) * 10 >= total_elements or not content:
            break
        page_num += 1
    return people


def _build_payload(user_id, order_col, order_dir, start=0, length=PAGE_LENGTH):
    return {
        "draw": 1,
        "columns": [{"data": c, "name": "", "searchable": True, "orderable": True,
                     "search": {"value": "", "regex": False}} for c in COLUMNS],
        "order": [{"column": order_col, "dir": order_dir}],
        "start": start, "length": length,
        "search": {"value": "", "regex": False},
        "assignmentExtraParam": {
            "region1Id": None, "region2Id": None, "region3Id": DOMPU_ULP_ID,
            "region4Id": None, "region5Id": None, "region6Id": None, "region7Id": None,
            "region8Id": None, "region9Id": None, "region10Id": None,
            "surveyPeriodId": PERIOD_ID, "assignmentErrorStatusType": -1,
            "assignmentStatusAlias": None,
            "data1": None, "data2": None, "data3": None, "data4": None, "data5": None,
            "data6": None, "data7": None, "data8": None, "data9": None, "data10": None,
            "userIdResponsibility": None, "currentUserId": user_id, "regionId": None,
            "filterTargetType": "TARGET_ONLY",
        },
    }


def _fetch_one(ctx, xsrf, user_id, order_col, order_dir, retries=3):
    """1 request datatable-all-user-survey-periode. Return (rows_dict_by_id, ok).
    ok=False kalau semua percobaan gagal (network/HTTP error) — HARUS dibedakan dari
    "ok=True tapi rows kosong" (berarti memang tidak ada data di sort/filter itu). Tanpa
    pembedaan ini, kegagalan jaringan sesaat di request PERTAMA bisa kebaca sebagai "total=0,
    sudah lengkap" dan diam-diam kehilangan seluruh data pencacah itu (pernah kejadian nyata:
    ECONNRESET pas fetch base sofiannbiller@gmail.com bikin 723 baris hilang tanpa PERINGATAN
    sama sekali sebelum fix ini)."""
    hdrs = {"Accept": "application/json, */*", "Content-Type": "application/json",
            "X-XSRF-TOKEN": xsrf, "Referer": f"{BASE_URL}/survey-collection/collect/{SURVEY_ID}",
            "Origin": BASE_URL}
    payload = _build_payload(user_id, order_col, order_dir)
    for attempt in range(1, retries + 1):
        try:
            r = ctx.request.post(f"{BASE_URL}/analytic/api/v2/assignment/datatable-all-user-survey-periode",
                                  data=json.dumps(payload), headers=hdrs, timeout=60000)
            if r.status != 200:
                time.sleep(2 * attempt)
                continue
            d = r.json()
            rows = {}
            for rec in d.get("searchData", []):
                rows[rec.get("id")] = {
                    "assignment_id": rec.get("id"),
                    "id_pelanggan":  rec.get("data3"),
                    "nama":          rec.get("data2"),
                    "status":        rec.get("assignmentStatusAlias"),
                }
            return rows, True
        except Exception as e:
            print(f"    [RETRY {attempt}/{retries}] datatable user={user_id[:8]}: {e}", flush=True)
            time.sleep(2 * attempt)
    return {}, False


def fetch_pencacah_assignments(ctx, xsrf, user_id, expected_total):
    """Ambil semua assignment milik satu pencacah. `expected_total` datang dari
    report-progress-by-responsibility (endpoint TERPISAH, dipanggil sekali di awal lewat
    list_pencacah) — dipakai sebagai target "sudah lengkap belum", BUKAN total dari response
    datatable itu sendiri, supaya kegagalan request tidak disalahartikan sebagai "total
    memang 0". Kalau 1 request default (sort id asc) sudah cukup (tidak kepotong batas
    ~990), berhenti di situ. Kalau tidak, coba beberapa kombinasi sort tambahan dan
    gabungkan (union by assignment id) — lihat catatan strategi paginasi di docstring atas.
    Return (rows_dict_by_id, semua_request_sukses)."""
    rows, ok = _fetch_one(ctx, xsrf, user_id, 0, "asc")
    all_ok = ok

    if len(rows) < expected_total:
        for col, direction in EXTRA_SORT_COMBOS:
            if len(rows) >= expected_total:
                break
            more, ok2 = _fetch_one(ctx, xsrf, user_id, col, direction)
            all_ok = all_ok and ok2
            rows.update(more)
            time.sleep(ROW_DELAY)

    return rows, all_ok


# ── Main ─────────────────────────────────────────────────────────────────────

def run_once():
    synced_at = _now_wita()
    conn = _connect_db()
    ensure_table(conn)

    grand_inserted = grand_updated = 0
    incomplete = []

    with sync_playwright() as pw:
        browser, ctx0 = _make_browser(pw)
        _, xsrf0 = login(ctx0)
        people = list_pencacah(ctx0, xsrf0)
        try:
            ctx0.close()
        except Exception:
            pass

        total_people = len(people)
        print(f"[{_now_wita()}] Mulai sync GC PLN Prabayar — {total_people} pencacah (ULP DOMPU), "
              f"total {sum(p['total'] for p in people)} assignment", flush=True)

        for chunk_start in range(0, total_people, CHUNK_SIZE):
            chunk = people[chunk_start:chunk_start + CHUNK_SIZE]
            chunk_no = chunk_start // CHUNK_SIZE + 1
            total_chunks = (total_people + CHUNK_SIZE - 1) // CHUNK_SIZE
            print(f"[chunk {chunk_no}/{total_chunks}] login ulang...", flush=True)

            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
            )
            try:
                _, xsrf = login(ctx)
            except Exception as e:
                print(f"  [ERROR] login gagal, chunk dilewati: {e}", flush=True)
                try:
                    ctx.close()
                except Exception:
                    pass
                continue

            for i, person in enumerate(chunk):
                global_i = chunk_start + i + 1
                user_id = person["userId"]
                expected_total = person["total"]
                rows, all_ok = fetch_pencacah_assignments(ctx, xsrf, user_id, expected_total)
                got = len(rows)
                if got < expected_total:
                    incomplete.append((person.get("username", user_id), got, expected_total, all_ok))
                    reason = "request gagal jaringan, akan dicoba lagi run berikutnya" if not all_ok \
                              else "kepotong batas FASIH, lihat log akhir"
                    print(f"  [{global_i}/{total_people}] {person.get('username')}: {got}/{expected_total} "
                          f"(PERINGATAN: {reason})", flush=True)
                else:
                    print(f"  [{global_i}/{total_people}] {person.get('username')}: {got} baris", flush=True)
                ins, upd = upsert_batch(conn, list(rows.values()), synced_at)
                grand_inserted += ins
                grand_updated += upd
                time.sleep(ROW_DELAY)

            try:
                ctx.close()
            except Exception:
                pass

            if chunk_start + CHUNK_SIZE < total_people:
                time.sleep(CHUNK_DELAY)

        browser.close()

    conn.close()
    print(f"\n[{_now_wita()}] Selesai. inserted={grand_inserted} updated={grand_updated}", flush=True)
    if incomplete:
        total_got = sum(g for _, g, _, _ in incomplete)
        total_exp = sum(t for _, _, t, _ in incomplete)
        print(f"[PERINGATAN] {len(incomplete)} pencacah datanya tidak 100% lengkap: "
              f"total {total_got}/{total_exp} baris ({total_got/total_exp*100:.1f}%)", flush=True)
        for username, got, total, all_ok in incomplete:
            reason = "gagal jaringan" if not all_ok else "batas ~990/request FASIH"
            print(f"    - {username}: {got}/{total} ({reason})", flush=True)


if __name__ == "__main__":
    print("=== sync_gc_pln.py ===")
    try:
        run_once()
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
    print(f"[{_now_wita()}] Done.")
