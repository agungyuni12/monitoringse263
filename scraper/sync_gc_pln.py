"""
Sync data GROUNDCHECK PELANGGAN LISTRIK PT PLN (PERSERO) - PRABAYAR dari FASIH → se2026.

Endpoint: POST /analytic/api/v2/assignment/datatable-all-user-survey-periode
Field mapping (dari /assignment-sync/api/mobile/template/custom-data/{templateId}):
  data2 = r103  = Nama pelanggan (SUDAH TERMASKING dari sumber PLN, mis. "E*I S******I" —
                   bukan dimasking oleh script ini, tidak ada cara mendapat versi lengkapnya
                   lewat FASIH)
  data3 = r101a = ID Pelanggan
  assignmentStatusAlias = status alur FASIH (OPEN/DRAFT/SUBMITTED BY Pencacah/dst)

Strategi paginasi: endpoint ini membatasi deep pagination (start/offset) sampai ~999 baris
per request TERLEPAS dari filter yang dipakai (dikonfirmasi lewat eksplorasi manual — start=990
masih OK, start=999 & seterusnya selalu balik searchData=[] meski total docCount masih besar).
Karena itu, request TIDAK di-paginate lewat offset biasa, tapi di-chunk per DESA (data4 = nama
desa, exact match) memakai daftar nama_desa dari tabel `sls` (khusus wilayah kerja Dompu) —
sama seperti sync_keberadaan.py yang chunk per kode_sls. Rata-rata desa ~283 assignment (jauh
di bawah batas ~990), jadi 1 request per desa cukup buat semua barisnya. Kalau ada desa yang
totalnya (dari searchAggregation) lebih besar dari yang berhasil diambil, dicatat sebagai
PERINGATAN — bukan gagal diam-diam.

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

BASE_URL   = "https://fasih-sm.bps.go.id"
SURVEY_ID  = "2395b67d-d1af-4739-9ef8-c0cc0aa9ce9a"
PERIOD_ID  = "16acea4e-4710-43d1-8b00-eeee589c8b66"
DOMPU_ULP_ID = "5fa041d9-15c0-4742-90ac-8056c9620be4"   # region3Id — ULP DOMPU

FASIH_USER = os.getenv("FASIH_USER", "agung.yuniarta")
FASIH_PASS = os.getenv("FASIH_PASS", "kelayu1998")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

PAGE_LENGTH  = 990   # aman di bawah batas deep-pagination ~999 yang diamati
CHUNK_SIZE   = 15    # desa per login sebelum context+login baru
CHUNK_DELAY  = 4      # detik istirahat antar chunk
ROW_DELAY    = 0.3    # detik jeda antar request desa dalam satu chunk

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


def load_desa_list(conn):
    """Daftar nama_desa unik wilayah kerja Dompu dari tabel sls — dipakai sebagai
    kunci partisi request ke FASIH (lihat catatan paginasi di docstring atas)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT nama_desa FROM sls
            WHERE nama_desa IS NOT NULL AND nama_desa != ''
        """)
        return [r["nama_desa"] for r in cur.fetchall()]


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

def fetch_desa(ctx, xsrf, nama_desa, retries=3):
    """Ambil semua assignment untuk satu desa (data4 exact match). Return
    (rows, total_reported) — total_reported dari searchAggregation dipakai buat
    deteksi kalau ada baris yang kepotong batas paginasi."""
    payload = {
        "draw": 1,
        "columns": [{"data": c, "name": "", "searchable": True, "orderable": c not in ("id", "codeIdentity"),
                     "search": {"value": "", "regex": False}}
                    for c in ["id", "codeIdentity", "data2", "data3"]],
        "order": [{"column": 0, "dir": "asc"}],
        "start": 0, "length": PAGE_LENGTH,
        "search": {"value": "", "regex": False},
        "assignmentExtraParam": {
            "region1Id": None, "region2Id": None, "region3Id": DOMPU_ULP_ID,
            "region4Id": None, "region5Id": None, "region6Id": None, "region7Id": None,
            "region8Id": None, "region9Id": None, "region10Id": None,
            "surveyPeriodId": PERIOD_ID, "assignmentErrorStatusType": -1,
            "assignmentStatusAlias": None,
            "data1": None, "data2": None, "data3": None, "data4": nama_desa, "data5": None,
            "data6": None, "data7": None, "data8": None, "data9": None, "data10": None,
            "userIdResponsibility": None, "currentUserId": None, "regionId": None,
            "filterTargetType": "TARGET_ONLY",
        },
    }
    hdrs = {
        "Accept": "application/json, */*",
        "Content-Type": "application/json",
        "X-XSRF-TOKEN": xsrf,
        "Referer": f"{BASE_URL}/survey-collection/collect/{SURVEY_ID}",
        "Origin": BASE_URL,
    }
    for attempt in range(1, retries + 1):
        try:
            r = ctx.request.post(
                f"{BASE_URL}/analytic/api/v2/assignment/datatable-all-user-survey-periode",
                data=json.dumps(payload), headers=hdrs, timeout=60000,
            )
            if r.status != 200:
                print(f"    [WARN] {nama_desa}: HTTP {r.status}", flush=True)
                return [], 0
            d = r.json()
            total = sum(a["docCount"] for a in (d.get("searchAggregation") or []))
            rows = []
            for rec in d.get("searchData", []):
                rows.append({
                    "assignment_id": rec.get("id"),
                    "id_pelanggan":  rec.get("data3"),
                    "nama":          rec.get("data2"),
                    "status":        rec.get("assignmentStatusAlias"),
                })
            return rows, total
        except Exception as e:
            print(f"    [RETRY {attempt}/{retries}] {nama_desa}: {e}", flush=True)
            if attempt < retries:
                time.sleep(3 * attempt)
    return [], 0


# ── Main ─────────────────────────────────────────────────────────────────────

def run_once():
    synced_at = _now_wita()
    conn = _connect_db()
    ensure_table(conn)
    desa_list = load_desa_list(conn)
    total_desa = len(desa_list)
    print(f"[{_now_wita()}] Mulai sync GC PLN Prabayar — {total_desa} desa (ULP DOMPU)", flush=True)

    grand_inserted = grand_updated = 0
    incomplete_desa = []

    with sync_playwright() as pw:
        browser, _ = _make_browser(pw)

        for chunk_start in range(0, total_desa, CHUNK_SIZE):
            chunk = desa_list[chunk_start:chunk_start + CHUNK_SIZE]
            chunk_no = chunk_start // CHUNK_SIZE + 1
            total_chunks = (total_desa + CHUNK_SIZE - 1) // CHUNK_SIZE
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

            for i, nama_desa in enumerate(chunk):
                global_i = chunk_start + i + 1
                rows, total = fetch_desa(ctx, xsrf, nama_desa)
                if len(rows) < total:
                    incomplete_desa.append((nama_desa, len(rows), total))
                    print(f"  [{global_i}/{total_desa}] {nama_desa}: {len(rows)}/{total} "
                          f"(PERINGATAN: kemungkinan kepotong batas paginasi)", flush=True)
                else:
                    print(f"  [{global_i}/{total_desa}] {nama_desa}: {len(rows)} baris", flush=True)
                ins, upd = upsert_batch(conn, rows, synced_at)
                grand_inserted += ins
                grand_updated += upd
                time.sleep(ROW_DELAY)

            try:
                ctx.close()
            except Exception:
                pass

            if chunk_start + CHUNK_SIZE < total_desa:
                time.sleep(CHUNK_DELAY)

        browser.close()

    conn.close()
    print(f"\n[{_now_wita()}] Selesai. inserted={grand_inserted} updated={grand_updated}", flush=True)
    if incomplete_desa:
        print(f"[PERINGATAN] {len(incomplete_desa)} desa kemungkinan datanya belum lengkap "
              f"(kena batas paginasi FASIH ~990/request):", flush=True)
        for nama, got, total in incomplete_desa:
            print(f"    - {nama}: {got}/{total}", flush=True)


if __name__ == "__main__":
    print("=== sync_gc_pln.py ===")
    try:
        run_once()
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
    print(f"[{_now_wita()}] Done.")
