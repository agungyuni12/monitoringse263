"""
Import SEKALI JALAN: detail Usaha & Keluarga "Tidak Ditemukan" dari file export
FASIH mentah (bukan sync API dashboard) ke tabel tidak_ditemukan_usaha /
tidak_ditemukan_keluarga. Dipakai sub-menu Usaha/Keluarga di tab Keberadaan
saat filter status "Tidak Ditemukan" dipilih.

Sumber:
  - <folder>/usaha_tidak_ditemukan_*.csv    (semua baris flag_keberadaan='0')
  - <folder>/keluarga_tidak_ditemukan_*.csv (semua baris ada_keluarga_label
    berisi "Tidak Ditemukan")

Join ke sls lewat kolom level_6_full_code (kode SLS lengkap 16 digit) -> sls.kode_sls.

Jalankan manual saat ada file export baru:
  python3 import_tidak_ditemukan.py --usaha /path/usaha.csv --keluarga /path/keluarga.csv

Tanpa argumen, default mencari file di 2 folder yang sudah ada di root project
(lihat DEFAULT_USAHA_CSV / DEFAULT_KELUARGA_CSV di bawah).

Mode --sql-out: tulis hasilnya sebagai file .sql (INSERT ... ON DUPLICATE KEY
UPDATE, di-batch) alih-alih langsung eksekusi ke DB — untuk direview dulu atau
dijalankan manual/di server lain. Tetap butuh koneksi DB (read-only) buat
resolve kode_sls -> sls_id.
  python3 import_tidak_ditemukan.py --sql-out ../db/tidak_ditemukan_data.sql

Env vars: DB_HOST / DB_PORT / DB_USER / DB_PASS / DB_NAME
"""

import argparse
import csv
import os
from datetime import datetime

import pymysql

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "kelayu1998")
DB_NAME = os.getenv("DB_NAME", "se2026")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_USAHA_CSV = os.path.join(
    ROOT, "usaha_tidak_ditemukan_5205_20260716030225",
    "usaha_tidak_ditemukan_5205_20260716030225.csv",
)
DEFAULT_KELUARGA_CSV = os.path.join(
    ROOT, "keluarga_tidak_ditemukan_5205_20260715030051",
    "keluarga_tidak_ditemukan_5205_20260715030051.csv",
)


def _connect_db():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        ssl={"ssl": False},
    )


def _load_sls_map(cur):
    cur.execute("SELECT kode_sls, id FROM sls")
    return {row[0]: row[1] for row in cur.fetchall()}


def _first(row, *cols):
    for c in cols:
        v = (row.get(c) or "").strip()
        if v:
            return v
    return None


def _parse_dt(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _sql_lit(v):
    """Escape satu nilai jadi literal SQL (backslash-escape, gaya MySQL)."""
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, datetime):
        v = v.strftime("%Y-%m-%d %H:%M:%S")
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + s + "'"


def _rows_usaha(sls_map, path):
    """Baca CSV usaha, resolve sls_id, return list of tuple utk INSERT (dipakai
    bareng oleh eksekusi langsung ke DB maupun generate .sql)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows, skipped = [], 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sls_id = sls_map.get((row.get("level_6_full_code") or "").strip())
            assignment_id = (row.get("assignment_id") or "").strip()
            if sls_id is None or not assignment_id:
                skipped += 1
                continue
            rows.append((
                sls_id, assignment_id,
                _first(row, "nama_usaha", "nama_komersial"),
                (row.get("skala_usaha") or "").strip() or None,
                _first(row, "alamat_usaha", "alamat_usaha_utama"),
                (row.get("assignment_status_alias") or "").strip() or None,
                _parse_dt(row.get("assignment_date_modified")),
                now,
            ))
    return rows, skipped


def _rows_keluarga(sls_map, path):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows, skipped = [], 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sls_id = sls_map.get((row.get("level_6_full_code") or "").strip())
            assignment_id = (row.get("assignment_id") or "").strip()
            if sls_id is None or not assignment_id:
                skipped += 1
                continue
            rows.append((
                sls_id, assignment_id,
                _first(row, "nama_kk", "dtsen_nama_kk"),
                _first(row, "alamat_klrg", "alamat_prelist"),
                (row.get("assignment_status_alias") or "").strip() or None,
                _parse_dt(row.get("assignment_date_modified")),
                now,
            ))
    return rows, skipped


TABLE_USAHA_COLS = ["sls_id", "assignment_id", "nama", "skala_usaha", "alamat", "assignment_status", "tanggal_modified", "imported_at"]
TABLE_KELUARGA_COLS = ["sls_id", "assignment_id", "nama", "alamat", "assignment_status", "tanggal_modified", "imported_at"]


def import_usaha(conn, path):
    cur = conn.cursor()
    rows, skipped = _rows_usaha(_load_sls_map(cur), path)
    cols = ", ".join(TABLE_USAHA_COLS)
    update = ", ".join(f"{c} = VALUES({c})" for c in TABLE_USAHA_COLS if c != "assignment_id")
    SQL = f"INSERT INTO tidak_ditemukan_usaha ({cols}) VALUES ({', '.join(['%s'] * len(TABLE_USAHA_COLS))}) ON DUPLICATE KEY UPDATE {update}"
    cur.executemany(SQL, rows)
    conn.commit()
    cur.close()
    print(f"[usaha] {len(rows)} baris diupsert, {skipped} dilewati (SLS/assignment_id tidak ada).", flush=True)


def import_keluarga(conn, path):
    cur = conn.cursor()
    rows, skipped = _rows_keluarga(_load_sls_map(cur), path)
    cols = ", ".join(TABLE_KELUARGA_COLS)
    update = ", ".join(f"{c} = VALUES({c})" for c in TABLE_KELUARGA_COLS if c != "assignment_id")
    SQL = f"INSERT INTO tidak_ditemukan_keluarga ({cols}) VALUES ({', '.join(['%s'] * len(TABLE_KELUARGA_COLS))}) ON DUPLICATE KEY UPDATE {update}"
    cur.executemany(SQL, rows)
    conn.commit()
    cur.close()
    print(f"[keluarga] {len(rows)} baris diupsert, {skipped} dilewati (SLS/assignment_id tidak ada).", flush=True)


def _write_sql_batch(f, table, columns, rows, batch_size=500):
    update = ", ".join(f"{c} = VALUES({c})" for c in columns if c != "assignment_id")
    cols_sql = ", ".join(columns)
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        values_sql = ",\n  ".join("(" + ", ".join(_sql_lit(v) for v in r) + ")" for r in batch)
        f.write(f"INSERT INTO {table} ({cols_sql}) VALUES\n  {values_sql}\nON DUPLICATE KEY UPDATE {update};\n\n")


def write_sql(sql_out, usaha_rows, keluarga_rows):
    with open(sql_out, "w", encoding="utf-8") as f:
        f.write("-- Generated by scraper/import_tidak_ditemukan.py --sql-out\n")
        f.write(f"-- {len(usaha_rows)} baris usaha, {len(keluarga_rows)} baris keluarga\n\n")
        _write_sql_batch(f, "tidak_ditemukan_usaha", TABLE_USAHA_COLS, usaha_rows)
        _write_sql_batch(f, "tidak_ditemukan_keluarga", TABLE_KELUARGA_COLS, keluarga_rows)
    print(f"[sql-out] ditulis ke {sql_out}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--usaha", default=DEFAULT_USAHA_CSV)
    parser.add_argument("--keluarga", default=DEFAULT_KELUARGA_CSV)
    parser.add_argument("--sql-out", default=None, help="Tulis hasil sbg file .sql, jangan eksekusi langsung ke DB")
    args = parser.parse_args()

    conn = _connect_db()
    if args.sql_out:
        cur = conn.cursor()
        sls_map = _load_sls_map(cur)
        cur.close()
        usaha_rows, usaha_skipped = _rows_usaha(sls_map, args.usaha) if os.path.exists(args.usaha) else ([], 0)
        keluarga_rows, keluarga_skipped = _rows_keluarga(sls_map, args.keluarga) if os.path.exists(args.keluarga) else ([], 0)
        write_sql(args.sql_out, usaha_rows, keluarga_rows)
        print(f"[usaha] {len(usaha_rows)} baris, {usaha_skipped} dilewati.", flush=True)
        print(f"[keluarga] {len(keluarga_rows)} baris, {keluarga_skipped} dilewati.", flush=True)
    else:
        if args.usaha and os.path.exists(args.usaha):
            import_usaha(conn, args.usaha)
        else:
            print(f"[usaha] file tidak ditemukan: {args.usaha}", flush=True)
        if args.keluarga and os.path.exists(args.keluarga):
            import_keluarga(conn, args.keluarga)
        else:
            print(f"[keluarga] file tidak ditemukan: {args.keluarga}", flush=True)
    conn.close()
    print("Selesai!", flush=True)


if __name__ == "__main__":
    main()
