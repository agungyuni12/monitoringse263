"""
Sync anomali per-assignment dari Dashboard SE2026 → tabel anomali

Endpoint: GET /api/mikro/anomali-case-kab
  Response: JSON array, setiap item berisi:
    assignment_id   : UUID assignment FASIH
    nama_tercantum  : nama usaha / kepala rumah tangga
    anomali_title   : deskripsi anomali lengkap
    kode_wilayah    : 16-digit kode SLS (kode_desa + kode_sls + sub_sls)
    is_resolved     : bool, apakah sudah ditindaklanjuti
    source_type     : "usaha" atau "keluarga"
    id_indikator    : kode belum (128-135 usaha, 136+ keluarga)

Env vars:
  DASH_USER       login Dashboard SE2026 (default: nurfitriati)
  DASH_PASS       password Dashboard SE2026 (default: triemam95)
  KODE_KABUPATEN  kode kabupaten 4-digit (default: 5205)
  DB_HOST / DB_PORT / DB_USER / DB_PASS / DB_NAME
  HEADLESS        jalankan Chrome headless (default: false)
"""

import os, time, json
import pymysql
from datetime import datetime
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth(navigator_webdriver=True)

DASH_URL = "https://dashboard-se2026.apps.bps.go.id"
SSO_USER = os.getenv("DASH_USER",      "nurfitriati")
SSO_PASS = os.getenv("DASH_PASS",      "triemam95")
KODE_KAB = os.getenv("KODE_KABUPATEN", "5205")
HEADLESS  = os.getenv("HEADLESS",      "false").lower() == "true"

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

FASIH_URL       = "https://fasih-sm.bps.go.id"
FASIH_USER      = os.getenv("FASIH_USER",      "agung.yuniarta")
FASIH_PASS      = os.getenv("FASIH_PASS",      "kelayu1998")
FASIH_PERIOD_ID = os.getenv("FASIH_PERIOD_ID", "fd68e454-ba45-4b85-8205-f3bf777ded24")

DELAY = 1.0  # detik jeda antar request

# Fallback statis jika /api/admin/config gagal
# `no` = anomali_no yang dipakai sbg param API (dari config.no, bukan formula belumKode)
_USAHA_STATIC = [
    {"rule_key": "128", "no": 1, "belumKode": "128", "sudahKode": "40",  "short_label": "Biaya Produksi Dominan"},
    {"rule_key": "129", "no": 2, "belumKode": "129", "sudahKode": "41",  "short_label": "Keuntungan Usaha"},
    {"rule_key": "130", "no": 3, "belumKode": "130", "sudahKode": "42",  "short_label": "Penyertaan Modal Korporasi"},
    {"rule_key": "131", "no": 4, "belumKode": "131", "sudahKode": "43",  "short_label": "Data Keuangan MBG"},
    {"rule_key": "132", "no": 5, "belumKode": "132", "sudahKode": "44",  "short_label": "Hubungan Aset Pekerja Produksi"},
    {"rule_key": "133", "no": 6, "belumKode": "133", "sudahKode": "45",  "short_label": "Internet Usaha Menengah Besar"},
    {"rule_key": "134", "no": 7, "belumKode": "134", "sudahKode": "46",  "short_label": "Laporan Keuangan Usaha Menengah"},
    {"rule_key": "135", "no": 8, "belumKode": "135", "sudahKode": None,  "short_label": "Perbedaan KBLI 2 Digit"},
]

# no = config.no (bukan belumKode-135) — ada gap di sequence belumKode (138 dan 143 skip)
_KELUARGA_STATIC = [
    {"rule_key": "136", "no": 1, "belumKode": "136", "sudahKode": "47",  "short_label": "Status Cerai / Belum Kawin"},
    {"rule_key": "137", "no": 2, "belumKode": "137", "sudahKode": "48",  "short_label": "KK < 10 Th di Rumah Sendiri"},
    {"rule_key": "139", "no": 3, "belumKode": "139", "sudahKode": "50",  "short_label": "Semua AK Disabilitas"},
    {"rule_key": "140", "no": 4, "belumKode": "140", "sudahKode": "51",  "short_label": "Luas Lantai Ekstrem"},
    {"rule_key": "141", "no": 5, "belumKode": "141", "sudahKode": "52",  "short_label": "Selisih Pendapatan Negatif"},
    {"rule_key": "142", "no": 6, "belumKode": "142", "sudahKode": "53",  "short_label": "Listrik Rendah Ada Barang Mewah"},
    {"rule_key": "144", "no": 7, "belumKode": "144", "sudahKode": None,  "short_label": "Jumlah AK Ekstrem"},
]


# ── Browser ──────────────────────────────────────────────────────────────────

def _make_browser(pw):
    browser = pw.chromium.launch(
        executable_path=os.getenv("CHROME_PATH", "/usr/bin/google-chrome-stable") or None,
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--no-sandbox"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )
    return browser, ctx


def login_dashboard(ctx):
    """Login ke Dashboard SE2026 via SSO BPS Keycloak."""
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)

    LONG = 90_000
    print("[LOGIN] Membuka SSO Dashboard...", flush=True)
    try:
        page.goto(f"{DASH_URL}/api/auth/sso", wait_until="commit", timeout=LONG)
    except Exception:
        pass
    time.sleep(3)

    page.wait_for_selector("input[name='username']", timeout=LONG)
    page.fill("input[name='username']", SSO_USER)
    page.fill("input[name='password']", SSO_PASS)
    page.click("#kc-login, input[type='submit'], button[type='submit']")

    for _ in range(30):
        time.sleep(2)
        if "dashboard-se2026" in page.url and "callback" not in page.url:
            break

    time.sleep(5)
    page.close()

    if "dashboard-se2026" not in ctx.pages[0].url if ctx.pages else True:
        # Verifikasi session dengan hit endpoint
        r = ctx.request.get(f"{DASH_URL}/api/admin/config", timeout=15_000)
        if r.status not in (200, 403):
            raise RuntimeError(f"Login gagal: status {r.status}")

    print("[LOGIN] Berhasil.", flush=True)


# ── Config ───────────────────────────────────────────────────────────────────

def fetch_anomali_config(ctx):
    """
    Ambil anomali_config dari /api/admin/config.
    Return (usaha_list, keluarga_list) — list of {rule_key, belumKode, sudahKode, short_label}.
    Gunakan static fallback jika gagal.
    """
    try:
        r = ctx.request.get(f"{DASH_URL}/api/admin/config", timeout=30_000)
        if r.status == 200:
            data = r.json().get("data", [])
            usaha_raw = next((x["value"] for x in data if x.get("config_name") == "anomali_config"), None)
            kel_raw   = next((x["value"] for x in data if x.get("config_name") == "anomali_keluarga_config"), None)

            def _parse(items):
                return [
                    {
                        "rule_key":    str(item.get("belumKode", "")),
                        "no":          item.get("no", 0),
                        "belumKode":   str(item.get("belumKode", "")),
                        "sudahKode":   str(item.get("sudahKode", "")) if item.get("sudahKode") else None,
                        "short_label": item.get("short_label", ""),
                    }
                    for item in items
                ]

            if usaha_raw and kel_raw:
                ul = _parse(usaha_raw)
                kl = _parse(kel_raw)
                print(f"[CONFIG] usaha={len(ul)} tipe, keluarga={len(kl)} tipe", flush=True)
                return ul, kl
    except Exception as e:
        print(f"[CONFIG] Gagal ({e}), pakai static.", flush=True)

    return _USAHA_STATIC, _KELUARGA_STATIC


# ── Waktu sync dashboard ──────────────────────────────────────────────────────

def fetch_dashboard_synced_at(ctx, kode_kab):
    """
    Ambil updated_at dari aggregate dashboard → convert ke WITA → return string.
    Fallback ke datetime.now() jika gagal.
    """
    from datetime import timezone, timedelta
    WITA = timezone(timedelta(hours=8))
    try:
        r = ctx.request.get(
            f"{DASH_URL}/api/agregat/fasih?kabupaten={kode_kab}&level=kabupaten&jenis=kualitas&indikator=128",
            timeout=15_000,
        )
        if r.status == 200:
            items = r.json()
            if items and items[0].get("updated_at"):
                # updated_at dari dashboard: "2026-06-26T17:08:17.166Z" (UTC)
                raw = items[0]["updated_at"].replace("Z", "+00:00")
                utc_dt = datetime.fromisoformat(raw)
                wita_dt = utc_dt.astimezone(WITA)
                synced_str = wita_dt.strftime("%Y-%m-%d %H:%M:%S")
                print(f"[SYNC TIME] Dashboard updated_at (WITA): {synced_str}", flush=True)
                return synced_str
    except Exception as e:
        print(f"[SYNC TIME] Gagal ambil updated_at: {e}", flush=True)
    fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[SYNC TIME] Fallback ke now: {fallback}", flush=True)
    return fallback


# ── Fetch anomali ─────────────────────────────────────────────────────────────

def fetch_anomali(ctx, kode_kab, belum_kode, sudah_kode, anomali_type, anomali_no, retries=3):
    """
    Fetch JSON per-assignment anomali dari /api/mikro/anomali-case-kab.
    anomali_no = config.no (bukan belumKode-base, karena keluarga punya gaps).
    Return list of raw JSON items. List kosong jika gagal.
    """
    params = {
        "kode_kabupaten": kode_kab,
        "indikator":      belum_kode,
        "type":           anomali_type,
        "anomali_no":     anomali_no,
    }
    if sudah_kode:
        params["sudah_indikator"] = sudah_kode

    url = f"{DASH_URL}/api/mikro/anomali-case-kab?{urlencode(params)}"

    for attempt in range(1, retries + 1):
        try:
            r = ctx.request.get(url, timeout=90_000)
            if r.status != 200:
                print(f"    [WARN] HTTP {r.status} untuk indikator={belum_kode}", flush=True)
                return []
            items = r.json()
            if not isinstance(items, list):
                print(f"    [WARN] Response bukan list untuk indikator={belum_kode}", flush=True)
                return []
            print(f"    indikator={belum_kode} ({anomali_type}): {len(items)} kasus", flush=True)
            return items
        except Exception as e:
            print(f"    [RETRY {attempt}/{retries}] indikator={belum_kode}: {e}", flush=True)
            if attempt < retries:
                time.sleep(5 * attempt)

    return []


# ── FASIH: nama_principal untuk keluarga ─────────────────────────────────────

def login_fasih(ctx):
    LONG = 90_000
    print("[FASIH] Login...", flush=True)
    page = ctx.new_page()
    _stealth.apply_stealth_sync(page)
    try:
        page.goto(f"{FASIH_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    except Exception:
        pass
    time.sleep(2)
    active = page
    if page.url in ("about:blank", ""):
        active = ctx.new_page()
        _stealth.apply_stealth_sync(active)
    active.goto(f"{FASIH_URL}/oauth2/authorization/ics", wait_until="networkidle", timeout=LONG)
    active.wait_for_selector("#kc-form-login", timeout=LONG)
    active.fill("#username", FASIH_USER)
    active.fill("#password", FASIH_PASS)
    active.click("#kc-login")
    active.wait_for_url(f"**{FASIH_URL}**", timeout=LONG)
    time.sleep(3)
    print("[FASIH] Login berhasil.", flush=True)


def fill_nama_by_sls(conn, pw):
    """
    Isi kolom nama anomali yang kosong via endpoint batch per-SLS FASIH.
    Jauh lebih efisien: 1 request per SLS (bukan per assignment).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT s.kode_sls
        FROM anomali a JOIN sls s ON a.sls_id = s.id
        WHERE (a.nama IS NULL OR a.nama = '')
    """)
    sls_codes = [r[0] for r in cur.fetchall()]
    cur.close()

    if not sls_codes:
        print("[FASIH NAMA] Semua nama sudah terisi.", flush=True)
        return

    print(f"[FASIH NAMA] Fetch nama untuk {len(sls_codes)} SLS...", flush=True)

    fasih_browser, fasih_ctx = _make_browser(pw)
    try:
        login_fasih(fasih_ctx)

        # Kumpulkan assignment_id → nama dari semua SLS
        nama_map = {}
        for i, kode_sls in enumerate(sls_codes, 1):
            url = (
                f"{FASIH_URL}/assignment-general/api/assignments"
                f"/get-principal-values-by-smallest-code/{FASIH_PERIOD_ID}/{kode_sls}"
            )
            try:
                r = fasih_ctx.request.get(url, timeout=15_000)
                if r.status == 200:
                    for item in r.json().get("data", []):
                        asg_id = item.get("assignmentId")
                        nama   = str(item.get("data1") or "").strip()[:255]
                        if asg_id and nama:
                            nama_map[asg_id] = nama
            except Exception as e:
                print(f"  [WARN] SLS {kode_sls}: {e}", flush=True)
            if i % 20 == 0 or i == len(sls_codes):
                print(f"  {i}/{len(sls_codes)} SLS diproses, {len(nama_map)} nama dikumpulkan...", flush=True)
            time.sleep(0.3)

        # Update DB
        cur = conn.cursor()
        updated = 0
        for asg_id, nama in nama_map.items():
            cur.execute(
                "UPDATE anomali SET nama=%s WHERE assignment_id=%s AND (nama IS NULL OR nama='')",
                (nama, asg_id),
            )
            if cur.rowcount > 0:
                updated += 1
        conn.commit()
        cur.close()
        print(f"[FASIH NAMA] Selesai: {updated} nama diupdate.", flush=True)
    finally:
        fasih_browser.close()


# ── Database ─────────────────────────────────────────────────────────────────

def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        ssl={"ssl": False},
    )


def load_sls_map(conn):
    """Return dict {kode_sls_16: sls_id}."""
    cur = conn.cursor()
    cur.execute("SELECT kode_sls, id FROM sls")
    result = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    print(f"[DB] SLS map: {len(result)} entri", flush=True)
    return result


def upsert_anomali(conn, sls_map, items, rule_key, short_label, synced_at):
    """
    Upsert items anomali ke tabel anomali, lalu hapus baris lama untuk rule_key
    yang sama tapi assignment_id-nya sudah tidak ada di daftar terbaru dari FASIH
    (artinya anomali itu sudah resolved / tidak berlaku lagi).
    UNIQUE KEY adalah (assignment_id, rule_key) — satu baris per (assignment, tipe anomali).
    Return (n_upserted, n_skipped, n_deleted).
    """
    cur = conn.cursor()

    SQL = """
        INSERT INTO anomali
          (sls_id, assignment_id, nama, jenis, rule_key, rule_msg, rule_type, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
        ON DUPLICATE KEY UPDATE
          sls_id    = VALUES(sls_id),
          nama      = IF(VALUES(nama) != '' AND VALUES(nama) IS NOT NULL, VALUES(nama), nama),
          jenis     = VALUES(jenis),
          rule_msg  = VALUES(rule_msg),
          synced_at = VALUES(synced_at)
    """

    upserted = skipped = 0
    current_ids = set()
    for item in items:
        assignment_id = str(item.get("assignment_id") or "").strip()
        if not assignment_id or len(assignment_id) != 36:
            continue
        current_ids.add(assignment_id)

        # kode_wilayah sudah 16-digit SLS code
        kode16 = str(item.get("kode_wilayah") or item.get("level_6_code") or "").strip()
        sls_id = sls_map.get(kode16)
        if sls_id is None:
            skipped += 1
            continue

        nama     = str(item.get("nama_tercantum") or "").strip()[:255]
        rule_msg = str(item.get("anomali_title") or "").strip()

        try:
            cur.execute(SQL, (
                sls_id, assignment_id, nama, short_label,
                rule_key, rule_msg, synced_at,
            ))
            upserted += 1
        except Exception as e:
            print(f"      [DB ERROR] {e}", flush=True)

    # Bersihkan anomali lama untuk rule_key ini yang sudah tidak muncul lagi di FASIH
    if current_ids:
        placeholders = ",".join(["%s"] * len(current_ids))
        cur.execute(
            f"DELETE FROM anomali WHERE rule_key = %s AND assignment_id NOT IN ({placeholders})",
            (rule_key, *current_ids),
        )
    else:
        cur.execute("DELETE FROM anomali WHERE rule_key = %s", (rule_key,))
    deleted = cur.rowcount

    conn.commit()
    cur.close()
    return upserted, skipped, deleted


# ── Main ─────────────────────────────────────────────────────────────────────

def run_once():
    print("=" * 55)
    print(f"SYNC ANOMALI SE2026  [{datetime.now():%Y-%m-%d %H:%M:%S}]")
    print(f"Kabupaten: {KODE_KAB}")
    print("=" * 55)

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        try:
            login_dashboard(ctx)
            usaha_list, kel_list = fetch_anomali_config(ctx)

            conn      = _connect_db()
            sls_map   = load_sls_map(conn)
            synced_at = fetch_dashboard_synced_at(ctx, KODE_KAB)
            total     = 0
            total_del = 0

            # Sync anomali usaha
            print(f"\n[USAHA] {len(usaha_list)} tipe...", flush=True)
            for item in usaha_list:
                rows = fetch_anomali(ctx, KODE_KAB, item["belumKode"], item["sudahKode"], "usaha", item["no"])
                n, skip, deleted = upsert_anomali(conn, sls_map, rows, item["rule_key"], item["short_label"], synced_at)
                total += n
                total_del += deleted
                if skip:
                    print(f"      {skip} skip (SLS tidak ada di DB)", flush=True)
                if deleted:
                    print(f"      {deleted} anomali lama dihapus (sudah resolved)", flush=True)
                time.sleep(DELAY)

            # Sync anomali keluarga
            print(f"\n[KELUARGA] {len(kel_list)} tipe...", flush=True)
            for item in kel_list:
                rows = fetch_anomali(ctx, KODE_KAB, item["belumKode"], item["sudahKode"], "keluarga", item["no"])
                n, skip, deleted = upsert_anomali(conn, sls_map, rows, item["rule_key"], item["short_label"], synced_at)
                total += n
                total_del += deleted
                if skip:
                    print(f"      {skip} skip (SLS tidak ada di DB)", flush=True)
                if deleted:
                    print(f"      {deleted} anomali lama dihapus (sudah resolved)", flush=True)
                time.sleep(DELAY)

            fill_nama_by_sls(conn, pw)
            conn.close()
            print(f"\nSelesai! {total} baris anomali diupsert, {total_del} dihapus (resolved).", flush=True)

        finally:
            browser.close()


try:
    import zoneinfo
    _wita_tz = zoneinfo.ZoneInfo("Asia/Makassar")
except Exception:
    import datetime as _dt
    _wita_tz = _dt.timezone(_dt.timedelta(hours=8))


def _now_wita():
    return datetime.now(_wita_tz)


def _next_run():
    """Jadwal sync: setiap 12 jam (2x sehari)."""
    from datetime import timedelta
    return _now_wita() + timedelta(hours=12)


if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] Sync gagal: {e}", flush=True)

        nxt  = _next_run()
        secs = max(0, (nxt - _now_wita()).total_seconds())
        print(
            f"[SCHEDULER] Sync berikutnya: {nxt.strftime('%d/%m/%Y %H:%M WITA')} "
            f"({int(secs // 60)} menit)",
            flush=True,
        )
        time.sleep(secs)
