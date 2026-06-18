-- Jalankan sekali: tambah kolom alasan pada laporan_harian
ALTER TABLE laporan_harian ADD COLUMN alasan_lebih20 TEXT DEFAULT NULL;
