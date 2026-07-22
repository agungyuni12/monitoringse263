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


def import_usaha(conn, path):
    cur = conn.cursor()
    sls_map = _load_sls_map(cur)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    SQL = """
        INSERT INTO tidak_ditemukan_usaha
          (sls_id, assignment_id, nama, skala_usaha, alamat, assignment_status, tanggal_modified, imported_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          sls_id            = VALUES(sls_id),
          nama              = VALUES(nama),
          skala_usaha       = VALUES(skala_usaha),
          alamat            = VALUES(alamat),
          assignment_status = VALUES(assignment_status),
          tanggal_modified  = VALUES(tanggal_modified),
          imported_at       = VALUES(imported_at)
    """
    upserted = skipped = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sls_id = sls_map.get((row.get("level_6_full_code") or "").strip())
            assignment_id = (row.get("assignment_id") or "").strip()
            if sls_id is None or not assignment_id:
                skipped += 1
                continue
            nama = _first(row, "nama_usaha", "nama_komersial")
            alamat = _first(row, "alamat_usaha", "alamat_usaha_utama")
            cur.execute(SQL, (
                sls_id, assignment_id, nama,
                (row.get("skala_usaha") or "").strip() or None,
                alamat,
                (row.get("assignment_status_alias") or "").strip() or None,
                _parse_dt(row.get("assignment_date_modified")),
                now,
            ))
            upserted += 1
    conn.commit()
    cur.close()
    print(f"[usaha] {upserted} baris diupsert, {skipped} dilewati (SLS/assignment_id tidak ada).", flush=True)


def import_keluarga(conn, path):
    cur = conn.cursor()
    sls_map = _load_sls_map(cur)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    SQL = """
        INSERT INTO tidak_ditemukan_keluarga
          (sls_id, assignment_id, nama, alamat, assignment_status, tanggal_modified, imported_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          sls_id            = VALUES(sls_id),
          nama              = VALUES(nama),
          alamat            = VALUES(alamat),
          assignment_status = VALUES(assignment_status),
          tanggal_modified  = VALUES(tanggal_modified),
          imported_at       = VALUES(imported_at)
    """
    upserted = skipped = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sls_id = sls_map.get((row.get("level_6_full_code") or "").strip())
            assignment_id = (row.get("assignment_id") or "").strip()
            if sls_id is None or not assignment_id:
                skipped += 1
                continue
            nama = _first(row, "nama_kk", "dtsen_nama_kk")
            alamat = _first(row, "alamat_klrg", "alamat_prelist")
            cur.execute(SQL, (
                sls_id, assignment_id, nama, alamat,
                (row.get("assignment_status_alias") or "").strip() or None,
                _parse_dt(row.get("assignment_date_modified")),
                now,
            ))
            upserted += 1
    conn.commit()
    cur.close()
    print(f"[keluarga] {upserted} baris diupsert, {skipped} dilewati (SLS/assignment_id tidak ada).", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--usaha", default=DEFAULT_USAHA_CSV)
    parser.add_argument("--keluarga", default=DEFAULT_KELUARGA_CSV)
    args = parser.parse_args()

    conn = _connect_db()
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
