-- Jalankan sekali (opsional): scraper/sync_usaha.py sudah menambahkan index
-- ini otomatis lewat _ensure_index() di ensure_tables() saat run berikutnya,
-- tapi bisa juga diterapkan manual duluan kalau tidak mau menunggu.
--
-- Latar belakang: find_duplikat_bku/find_tanpa_bku (deteksi menu "Usaha
-- Keluarga -> BKU") melakukan self-JOIN & NOT EXISTS di tidak_ditemukan_usaha
-- berdasarkan hp/email. Tanpa index di kedua kolom itu, MySQL nested-loop
-- scan PENUH tiap baris — di tabel ~126rb baris ini kejadian nyata bikin
-- query kelamaan sampai koneksi putus ("Lost connection to MySQL server
-- during query", 2026-08-27).
ALTER TABLE tidak_ditemukan_usaha ADD INDEX idx_tdu_hp (hp);
ALTER TABLE tidak_ditemukan_usaha ADD INDEX idx_tdu_email (email);
