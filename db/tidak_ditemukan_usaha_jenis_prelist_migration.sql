-- Jalankan sekali: tambah kolom jenis_prelist ke tidak_ditemukan_usaha.
-- scraper/sync_usaha.py sudah nulis kolom ini sejak rewrite ke se2026_nested
-- (unified bangunan mandiri + roster keluarga, commit 8653ab8), tapi tabel yang
-- sudah dibuat SEBELUM rewrite itu tidak otomatis dapat kolomnya — CREATE TABLE
-- IF NOT EXISTS di ensure_tables() tidak nge-ALTER tabel yang sudah ada, jadi
-- upsert_usaha() gagal dengan "Unknown column 'jenis_prelist' in 'INSERT INTO'"
-- sampai kolom ini ditambah manual.
ALTER TABLE tidak_ditemukan_usaha ADD COLUMN jenis_prelist VARCHAR(30) DEFAULT NULL AFTER skala_usaha;
