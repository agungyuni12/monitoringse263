"""
Deteksi kecocokan usaha keluarga ↔ BKU (usaha bangunan mandiri) → database
se2026 (tabel usaha_keluarga_bku_duplikat & usaha_keluarga_tanpa_bku).

Dipisah dari sync_usaha.py (dulu FASE 1c/1d di situ) — matching ini murni
operasi SQL atas tabel tidak_ditemukan_usaha yg SUDAH ada (hasil sync
sync_usaha.py), TIDAK butuh login/scraping/browser sama sekali. Alasan
dipisah: kalau matching numpuk jadi satu proses dgn sync_usaha.py, dia ikut
nunggu (atau gagal total) tiap kali FASIH Dashboard lambat/kena bot-wall —
padahal matching-nya sendiri gak ada urusan ke FASIH.

Jalan sbg loop polling tiap POLL_INTERVAL_MIN menit: cek apakah
tidak_ditemukan_usaha sudah ter-refresh (MAX(imported_at) berubah) sejak
matching TERAKHIR sukses jalan — state-nya disimpan di tabel sync_state
(job='usaha_matching'), pola yg sama dgn sync_fasih_verify_stale.py. Kalau
belum berubah, skip (gak ada gunanya re-matching data yg sama persis). Dengan
begini matching otomatis ngikutin kapan pun sync_usaha.py BENERAN selesai
(4x sehari, tapi jam pastinya bisa molor kalau FASIH lambat) tanpa perlu tau
jadwalnya atau di-hardcode ikut jam yg sama.

Dua tabel yg ditulis:
  - usaha_keluarga_bku_duplikat: usaha jenis_prelist='keluarga' yg hp/email-nya
    SAMA PERSIS dgn usaha BKU (mandiri) yg sudah ada — indikasi usaha yg sama
    tercatat dobel, satu di keluarga satu lagi sudah berdiri sendiri sbg BKU.
  - usaha_keluarga_tanpa_bku: kebalikannya — usaha keluarga yg hp/email-nya
    terisi tapi TIDAK ketemu pasangan usaha BKU manapun (kandidat "diangkat"
    jadi BKU baru). KBLI kategori A (Pertanian) dikecualikan, lihat komentar
    _TANPA_BKU_MATCH_SQL di bawah.

Env vars:
  DB_HOST (default: 127.0.0.1)
  DB_PORT (default: 3306)
  DB_USER (default: root)
  DB_PASS (default: kelayu1998)
  DB_NAME (default: se2026)
"""

import json, os, time
from datetime import datetime, timedelta, timezone

import pymysql

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

WITA = timezone(timedelta(hours=8))
POLL_INTERVAL_MIN = 15


def _now_wita():
    return datetime.now(WITA).replace(tzinfo=None)


def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def _ensure_index(conn, table, index_name, columns_ddl):
    """Sama persis dgn versi di sync_usaha.py — dicek dulu via
    information_schema (bukan ALTER TABLE langsung, biar idempotent)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s",
            (DB_NAME, table, index_name),
        )
        exists = cur.fetchone()["n"] > 0
        if not exists:
            cur.execute(f"ALTER TABLE {table} ADD INDEX {index_name} ({columns_ddl})")
            conn.commit()
            print(f"[DB] Index '{index_name}' ditambahkan ke {table} (auto-migrate).", flush=True)


def ensure_tables(conn):
    with conn.cursor() as cur:
        # Kandidat usaha yg nempel roster keluarga (jenis_prelist='keluarga')
        # tapi hp/email-nya SAMA PERSIS dengan usaha BKU (mandiri) yg sudah
        # ada — indikasi usaha yg sama sudah tercatat dobel, satu di keluarga
        # satu lagi sudah berdiri sendiri sbg BKU. Petugas perlu memindahkan/
        # menutup yg di keluarga lewat FASIH-mobile masing2 (lihat
        # sync_duplikat_bku). Baris TIDAK dihapus saat sudah tidak terdeteksi
        # lagi (artinya sudah dipindah) — cuma ditandai resolved_at, supaya
        # tetap ada riwayatnya (lihat menu Riwayat di admin).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usaha_keluarga_bku_duplikat (
              id                      INT NOT NULL AUTO_INCREMENT,
              sls_id                  INT NOT NULL,
              assignment_id_keluarga  VARCHAR(64) NOT NULL,
              nama_usaha_keluarga     VARCHAR(255) DEFAULT NULL,
              assignment_id_bku       VARCHAR(64) NOT NULL,
              nama_usaha_bku          VARCHAR(255) DEFAULT NULL,
              match_field             VARCHAR(10) NOT NULL,
              match_value             VARCHAR(150) NOT NULL,
              nama_cocok              TINYINT(1) NOT NULL DEFAULT 0,
              first_detected_at       DATETIME NOT NULL,
              synced_at               DATETIME NOT NULL,
              resolved_at             DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_ukbd_pair (assignment_id_keluarga, assignment_id_bku, match_field),
              KEY idx_ukbd_sls (sls_id),
              KEY idx_ukbd_resolved (resolved_at),
              CONSTRAINT fk_ukbd_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # Kebalikan dari usaha_keluarga_bku_duplikat: usaha jenis_prelist=
        # 'keluarga' yg hp/email-nya TERISI tapi TIDAK ketemu pasangan usaha
        # BKU manapun (belum pernah dibuatkan usaha BKU tersendiri). Kandidat
        # utk "diangkat" jadi usaha BKU baru oleh petugas, beda kasus dari
        # duplikat (yg BKU-nya sudah ADA). Kalau nanti muncul usaha BKU baru
        # yg hp/email-nya cocok, baris ini otomatis resolved di sync
        # berikutnya (lihat sync_tanpa_bku) — DAN baru akan muncul sbg
        # duplikat aktif di usaha_keluarga_bku_duplikat.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usaha_keluarga_tanpa_bku (
              id                      INT NOT NULL AUTO_INCREMENT,
              sls_id                  INT NOT NULL,
              assignment_id_keluarga  VARCHAR(64) NOT NULL,
              nama_usaha_keluarga     VARCHAR(255) DEFAULT NULL,
              hp                      VARCHAR(30) DEFAULT NULL,
              email                   VARCHAR(150) DEFAULT NULL,
              first_detected_at       DATETIME NOT NULL,
              synced_at               DATETIME NOT NULL,
              resolved_at             DATETIME DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_ukttb_assignment (assignment_id_keluarga),
              KEY idx_ukttb_sls (sls_id),
              KEY idx_ukttb_resolved (resolved_at),
              CONSTRAINT fk_ukttb_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                job        VARCHAR(50) PRIMARY KEY,
                state_json LONGTEXT NOT NULL,
                updated_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    conn.commit()

    # Krusial buat performa find_duplikat_bku/find_tanpa_bku (self-JOIN &
    # NOT EXISTS di hp/email) — tanpa index ini, tabel tidak_ditemukan_usaha
    # yg besar bakal nested-loop scan penuh tiap cek & bisa bikin koneksi
    # MySQL putus (lihat _ensure_index). Kalau tidak_ditemukan_usaha belum
    # ada sama sekali (sync_usaha.py belum pernah jalan), ALTER di bawah bakal
    # error — dibiarkan naik ke run_once()'s caller, siklus berikutnya coba
    # lagi (lihat loop utama).
    _ensure_index(conn, "tidak_ditemukan_usaha", "idx_tdu_hp", "hp")
    _ensure_index(conn, "tidak_ditemukan_usaha", "idx_tdu_email", "email")


# ── Deteksi usaha keluarga yg perlu dipindah ke BKU ─────────────────────────
# Cocokkan usaha jenis_prelist='keluarga' dengan usaha BKU (jenis_prelist
# mandiri) yg hp ATAU email-nya SAMA PERSIS. Kalau ketemu, berarti usaha yg
# sama sudah tercatat dobel: sekali nempel di roster keluarga, sekali lagi
# berdiri sendiri sbg BKU — yg di keluarga itu duplikat yg perlu dipindahkan/
# ditutup petugas via FASIH-mobile.

# hp='9999' adalah kode sentinel BPS ("tidak diisi"), BUKAN nomor HP asli —
# tapi lolos filter IS NOT NULL/!= '' krn bukan string kosong. Dicek manual
# (GROUP BY hp): 16.083 baris pakai nilai ini, ~200x lipat drpd nomor HP asli
# manapun (tertinggi cuma 69). Tanpa dikecualikan, self-JOIN di bawah
# mencocokkan ribuan baris ber-hp "9999" ke ribuan baris lain yg juga
# "9999" — ledakan kombinatorial puluhan juta baris kombinasi cuma dari 1
# nilai ini, bikin MySQL (dan host-nya) hang. email TIDAK punya masalah
# serupa (dicek manual, nilai terbanyak cuma 5x — wajar).
_HP_JUNK_SQL = "AND k.hp != '9999'"

_DUP_BKU_MATCH_SQL = """
    SELECT k.sls_id AS sls_id,
           k.assignment_id AS aid_keluarga, k.nama AS nama_keluarga,
           b.assignment_id AS aid_bku, b.nama AS nama_bku,
           %s AS match_field, k.{col} AS match_value
    FROM tidak_ditemukan_usaha k
    INNER JOIN tidak_ditemukan_usaha b
            ON b.{col} = k.{col}
           AND b.assignment_id != k.assignment_id
           AND (b.jenis_prelist IS NULL OR b.jenis_prelist != 'keluarga')
    WHERE k.jenis_prelist = 'keluarga'
      AND k.{col} IS NOT NULL AND k.{col} != ''
      {junk}
"""


def find_duplikat_bku(conn):
    """Cari pasangan (usaha keluarga, usaha BKU) yg hp atau email-nya sama
    persis. Dua query terpisah (hp, email) lalu digabung di Python, bukan
    UNION SQL — supaya kalau SATU pasang assignment cocok di hp MAUPUN email
    sekaligus, keduanya tetap tercatat sbg 2 bukti match yg beda (lihat
    UNIQUE KEY (assignment_id_keluarga, assignment_id_bku, match_field) di
    usaha_keluarga_bku_duplikat)."""
    pairs = []
    with conn.cursor() as cur:
        for field in ("hp", "email"):
            junk = _HP_JUNK_SQL if field == "hp" else ""
            cur.execute(_DUP_BKU_MATCH_SQL.format(col=field, junk=junk), (field,))
            pairs.extend(cur.fetchall())
    return pairs


def _nama_cocok(nama_a, nama_b):
    """Nama usaha BUKAN syarat wajib buat deteksi duplikat (itu tetap HP/email
    doang) — ini cuma penanda tambahan: kalau nama_a & nama_b (dinormalisasi:
    lower + strip) sama persis DAN dua2nya keisi, berarti makin yakin usahanya
    memang sama (bukan cuma kebetulan HP/email-nya sama, mis. satu keluarga
    beda usaha pakai kontak yg sama). Beda nama TIDAK menggugurkan match HP/
    email-nya — cuma berarti perlu dicek manual dulu sebelum dipindahkan."""
    a = (nama_a or "").strip().lower()
    b = (nama_b or "").strip().lower()
    return 1 if a and a == b else 0


def sync_duplikat_bku(conn, synced_at):
    pairs = find_duplikat_bku(conn)
    with conn.cursor() as cur:
        for p in pairs:
            nama_cocok = _nama_cocok(p["nama_keluarga"], p["nama_bku"])
            cur.execute("""
                INSERT INTO usaha_keluarga_bku_duplikat
                  (sls_id, assignment_id_keluarga, nama_usaha_keluarga,
                   assignment_id_bku, nama_usaha_bku, match_field, match_value, nama_cocok,
                   first_detected_at, synced_at, resolved_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
                ON DUPLICATE KEY UPDATE
                  sls_id              = VALUES(sls_id),
                  nama_usaha_keluarga = VALUES(nama_usaha_keluarga),
                  nama_usaha_bku      = VALUES(nama_usaha_bku),
                  match_value         = VALUES(match_value),
                  nama_cocok          = VALUES(nama_cocok),
                  synced_at           = VALUES(synced_at),
                  resolved_at         = NULL
            """, (
                p["sls_id"], p["aid_keluarga"], p["nama_keluarga"],
                p["aid_bku"], p["nama_bku"], p["match_field"], p["match_value"], nama_cocok,
                synced_at, synced_at,
            ))
        # Pasangan yg dulu terdeteksi tapi baris ini TIDAK ikut ke-refresh
        # (synced_at masih yg lama) berarti sudah tidak cocok lagi sekarang —
        # bisa krn petugas sudah memindahkan/menutup usaha di keluarga, atau
        # salah satu sisi berubah datanya. Tandai resolved (bukan dihapus,
        # supaya ada riwayatnya).
        cur.execute("""
            UPDATE usaha_keluarga_bku_duplikat
            SET resolved_at = %s
            WHERE resolved_at IS NULL AND synced_at < %s
        """, (synced_at, synced_at))
        resolved_now = cur.rowcount
    conn.commit()
    print(f"[DB] duplikat usaha keluarga→BKU: {len(pairs)} pasangan aktif, {resolved_now} baru selesai (sudah dipindah).", flush=True)


# ── Deteksi usaha keluarga yg BELUM ada BKU-nya sama sekali ─────────────────
# Kebalikan dari find_duplikat_bku: usaha jenis_prelist='keluarga' yg hp ATAU
# email-nya TERISI, tapi TIDAK ada usaha BKU manapun yg cocok (NOT EXISTS,
# bukan INNER JOIN). Kandidat utk "diangkat" jadi usaha BKU baru — beda kasus
# dari duplikat (yg BKU-nya sudah ADA, tinggal dipindahkan/ditutup yg lama).
#
# KBLI kategori A (Pertanian, Kehutanan, Perikanan) DIKECUALIKAN — usaha
# pertanian yg nempel roster keluarga itu memang wajar (carry-over ST2023,
# lihat docstring sync_usaha.py), BUKAN indikasi usaha yg "seharusnya" berdiri
# sendiri sbg BKU, jadi jangan didorong utk dibuatkan BKU baru.

_TANPA_BKU_MATCH_SQL = """
    SELECT k.sls_id AS sls_id, k.assignment_id AS aid_keluarga,
           k.nama AS nama_keluarga, k.hp AS hp, k.email AS email
    FROM tidak_ditemukan_usaha k
    WHERE k.jenis_prelist = 'keluarga'
      AND ((k.hp IS NOT NULL AND k.hp != '' AND k.hp != '9999') OR (k.email IS NOT NULL AND k.email != ''))
      AND (k.kbli_kategori_prelist IS NULL OR k.kbli_kategori_prelist != 'A')
      AND NOT EXISTS (
          SELECT 1 FROM tidak_ditemukan_usaha b
          WHERE b.assignment_id != k.assignment_id
            AND (b.jenis_prelist IS NULL OR b.jenis_prelist != 'keluarga')
            AND (
                 (k.hp IS NOT NULL AND k.hp != '' AND k.hp != '9999' AND b.hp = k.hp)
              OR (k.email IS NOT NULL AND k.email != '' AND b.email = k.email)
            )
      )
"""


def find_tanpa_bku(conn):
    with conn.cursor() as cur:
        cur.execute(_TANPA_BKU_MATCH_SQL)
        return cur.fetchall()


def sync_tanpa_bku(conn, synced_at):
    rows = find_tanpa_bku(conn)
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO usaha_keluarga_tanpa_bku
                  (sls_id, assignment_id_keluarga, nama_usaha_keluarga, hp, email,
                   first_detected_at, synced_at, resolved_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)
                ON DUPLICATE KEY UPDATE
                  sls_id              = VALUES(sls_id),
                  nama_usaha_keluarga = VALUES(nama_usaha_keluarga),
                  hp                  = VALUES(hp),
                  email               = VALUES(email),
                  synced_at           = VALUES(synced_at),
                  resolved_at         = NULL
            """, (
                r["sls_id"], r["aid_keluarga"], r["nama_keluarga"], r["hp"], r["email"],
                synced_at, synced_at,
            ))
        # Sama seperti sync_duplikat_bku: baris lama yg gak ke-refresh berarti
        # sudah tidak masuk kriteria ini lagi — entah krn sudah ketemu usaha
        # BKU yg cocok (langsung akan muncul sbg duplikat aktif di
        # usaha_keluarga_bku_duplikat), hp/email-nya dihapus/diubah, atau
        # usahanya sendiri sudah tidak ada lagi. Ditandai resolved, bukan
        # dihapus.
        cur.execute("""
            UPDATE usaha_keluarga_tanpa_bku
            SET resolved_at = %s
            WHERE resolved_at IS NULL AND synced_at < %s
        """, (synced_at, synced_at))
        resolved_now = cur.rowcount
    conn.commit()
    print(f"[DB] usaha keluarga tanpa BKU: {len(rows)} aktif, {resolved_now} baru selesai (sudah ketemu BKU/berubah).", flush=True)


# ── State polling (sync_state.job='usaha_matching') ─────────────────────────
# Pola sama persis dgn sync_state.job='fasih_verify' di sync_fasih_verify_stale.py.

def _load_last_seen(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT state_json FROM sync_state WHERE job = 'usaha_matching'")
        row = cur.fetchone()
    if not row:
        return None
    return json.loads(row["state_json"]).get("last_source_imported_at")


def _save_last_seen(conn, value):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sync_state (job, state_json, updated_at)
            VALUES ('usaha_matching', %s, NOW())
            ON DUPLICATE KEY UPDATE state_json = VALUES(state_json), updated_at = NOW()
        """, (json.dumps({"last_source_imported_at": value}),))
    conn.commit()


def run_once():
    conn = _connect_db()
    ensure_tables(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT MAX(imported_at) AS m FROM tidak_ditemukan_usaha")
        source_latest_raw = cur.fetchone()["m"]

    if source_latest_raw is None:
        print("[MATCHING] tidak_ditemukan_usaha masih kosong — skip, tunggu sync_usaha.py jalan dulu.", flush=True)
        conn.close()
        return

    source_latest = source_latest_raw.strftime("%Y-%m-%d %H:%M:%S")
    last_seen = _load_last_seen(conn)
    if last_seen == source_latest:
        print(f"[MATCHING] tidak_ditemukan_usaha belum berubah sejak matching terakhir ({source_latest}) — skip.", flush=True)
        conn.close()
        return

    print("=" * 50, flush=True)
    print(f"SYNC MATCHING USAHA KELUARGA ↔ BKU → se2026  [{_now_wita():%Y-%m-%d %H:%M:%S} WITA]", flush=True)
    print(f"[MATCHING] Data sumber ter-refresh: {source_latest} (terakhir diproses: {last_seen})", flush=True)
    print("=" * 50, flush=True)

    synced_at = _now_wita().strftime("%Y-%m-%d %H:%M:%S")
    sync_duplikat_bku(conn, synced_at)
    sync_tanpa_bku(conn, synced_at)

    _save_last_seen(conn, source_latest)
    conn.close()
    print("Selesai!", flush=True)


if __name__ == "__main__":
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] Matching gagal: {e}", flush=True)

        secs = POLL_INTERVAL_MIN * 60
        print(f"[SCHEDULER] Cek lagi dalam {POLL_INTERVAL_MIN} menit.", flush=True)
        time.sleep(secs)
