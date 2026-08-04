"""
Export nama-nama usaha & keluarga tidak ditemukan (bukan cuma rekap jumlahnya)
dari tabel tidak_ditemukan_usaha / tidak_ditemukan_keluarga ke satu file Excel,
dikelompokkan per kecamatan + desa (satu sheet Usaha, satu sheet Keluarga).

Jalankan: python3 export_tidak_ditemukan.py [path_output.xlsx]
"""
import os, sys
import pandas as pd
import pymysql

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

USAHA_QUERY = """
    SELECT s.nama_kec, s.nama_desa, s.nama_sls, s.kode_sls,
           tu.nama, tu.skala_usaha, tu.jenis_prelist, tu.alamat,
           tu.assignment_status, tu.tanggal_modified
    FROM tidak_ditemukan_usaha tu
    JOIN sls s ON tu.sls_id = s.id
    ORDER BY s.nama_kec, s.nama_desa, tu.nama
"""

KELUARGA_QUERY = """
    SELECT s.nama_kec, s.nama_desa, s.nama_sls, s.kode_sls,
           tk.nama, tk.alamat, tk.assignment_status, tk.tanggal_modified
    FROM tidak_ditemukan_keluarga tk
    JOIN sls s ON tk.sls_id = s.id
    ORDER BY s.nama_kec, s.nama_desa, tk.nama
"""


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "tidak_ditemukan_per_desa.xlsx"

    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
    )

    df_usaha = pd.read_sql(USAHA_QUERY, conn)
    df_keluarga = pd.read_sql(KELUARGA_QUERY, conn)
    conn.close()

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_usaha.to_excel(writer, sheet_name="Usaha", index=False)
        df_keluarga.to_excel(writer, sheet_name="Keluarga", index=False)

    print(f"Usaha   : {len(df_usaha)} baris, {df_usaha['nama_desa'].nunique()} desa")
    print(f"Keluarga: {len(df_keluarga)} baris, {df_keluarga['nama_desa'].nunique()} desa")
    print(f"Tersimpan -> {out_path}")


if __name__ == "__main__":
    main()
