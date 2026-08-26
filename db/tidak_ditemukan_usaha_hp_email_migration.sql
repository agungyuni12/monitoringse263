-- Jalankan sekali (opsional): scraper/sync_usaha.py sudah auto-migrate kolom
-- ini lewat _ensure_column() saat run berikutnya, tapi bisa juga diterapkan
-- manual duluan kalau perlu tersedia sebelum sync jalan.
ALTER TABLE tidak_ditemukan_usaha ADD COLUMN hp VARCHAR(30) DEFAULT NULL AFTER kbli_kategori_prelist;
ALTER TABLE tidak_ditemukan_usaha ADD COLUMN email VARCHAR(150) DEFAULT NULL AFTER hp;
