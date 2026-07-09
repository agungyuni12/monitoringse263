"""
Import SEKALI JALAN: Prelist Usaha dalam Keluarga per SLS, dari file rekap
prelist statis (bukan dari API dashboard — prelist adalah kuota tetap yang
tidak berubah, jadi cukup di-import sekali ke kolom coverage_usaha_keluarga
dengan kode_indikator sintetis "90001").

Sumber: file Excel "Rekap Prelist" BPS (kolom "ASSIGNMENT KELUARGA DAN ROSTER
USAHA DI DALAM KELUARGA" > sub-kolom "UMKM Keluarga" + "ST2023") — hasil
diskusi menunjukkan gabungan dua kolom itu yang merepresentasikan prelist
usaha dalam keluarga (bukan cuma "UMKM Keluarga" saja, karena nilainya kecil
sekali / tidak masuk akal sebagai kuota sendirian).

Jalankan manual saat ada file rekap prelist baru:
  python3 import_prelist_usaha_keluarga.py "/path/ke/Rekap Prelist_52.xlsx"

Env vars: DB_HOST / DB_PORT / DB_USER / DB_PASS / DB_NAME, KODE_KABUPATEN (default 5205)
"""

import os
import sys
import pandas as pd
import pymysql
from datetime import datetime

KODE_KAB = int(os.getenv("KODE_KABUPATEN", "5205"))

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

KODE_INDIKATOR = "90001"
NAMA_INDIKATOR = "Jumlah Prelist Usaha dalam Keluarga"

# Kolom-kolom dalam file rekap prelist (header ada di baris 1-2, data mulai baris 3):
#   0=IDKAB, 3=IDSUBSLS_25_2 (=kode_sls), 23="UMKM Keluarga", 24="ST2023"
COL_IDKAB = 0
COL_KODE_SLS = 3
COL_UMKM_KELUARGA = 23
COL_ST2023 = 24


def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        ssl={"ssl": False},
    )


def main(path):
    df = pd.read_excel(path, sheet_name=0, header=None, skiprows=2)
    df.columns = list(range(df.shape[1]))

    subset = df[df[COL_IDKAB] == KODE_KAB].dropna(subset=[COL_KODE_SLS]).copy()
    subset["kode_sls"] = subset[COL_KODE_SLS].astype("int64").astype(str)
    subset["prelist_ukel"] = (
        pd.to_numeric(subset[COL_UMKM_KELUARGA], errors="coerce").fillna(0)
        + pd.to_numeric(subset[COL_ST2023], errors="coerce").fillna(0)
    ).astype(int)

    print(f"[FILE] {len(subset)} baris SLS ditemukan utk kabupaten {KODE_KAB}", flush=True)
    print(f"[FILE] Total prelist usaha dalam keluarga: {subset['prelist_ukel'].sum():,}", flush=True)

    conn = _connect_db()
    cur = conn.cursor()
    cur.execute("SELECT kode_sls, id FROM sls")
    sls_map = {row[0]: row[1] for row in cur.fetchall()}
    print(f"[DB] SLS map: {len(sls_map)} entri", flush=True)

    SQL = """
        INSERT INTO coverage_usaha_keluarga
          (sls_id, kode_indikator, nama_indikator, satuan, total_value, is_agregat, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          nama_indikator = VALUES(nama_indikator),
          satuan         = VALUES(satuan),
          total_value    = VALUES(total_value),
          is_agregat     = VALUES(is_agregat),
          synced_at      = VALUES(synced_at)
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    upserted = skipped = 0
    for _, row in subset.iterrows():
        sls_id = sls_map.get(row["kode_sls"])
        if sls_id is None:
            skipped += 1
            continue
        val = int(row["prelist_ukel"])
        cur.execute(SQL, (sls_id, KODE_INDIKATOR, NAMA_INDIKATOR, "Usaha", val, 1 if val > 0 else None, now))
        upserted += 1
    conn.commit()
    cur.close()
    conn.close()
    print(f"\nSelesai! {upserted} baris diupsert, {skipped} dilewati (SLS tidak ada di DB).", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 import_prelist_usaha_keluarga.py <path_ke_file_xlsx>")
        sys.exit(1)
    main(sys.argv[1])
