-- Jalankan SETIAP KALI SETELAH scraper/sync_usaha.py selesai sync (bukan
-- migration sekali-jalan): bersihkan baris yang sudah TIDAK "tidak ditemukan"
-- lagi dari tidak_ditemukan_usaha & tidak_ditemukan_keluarga.
--
-- upsert_usaha() / upsert_keluarga() di sync_usaha.py cuma INSERT/UPDATE baris
-- yang MASIH tidak ditemukan di hasil scrape terbaru (ON DUPLICATE KEY UPDATE)
-- -- baris yang usaha/keluarganya sudah DITEMUKAN sejak sync sebelumnya tidak
-- lagi muncul di hasil scrape, jadi imported_at-nya berhenti ter-update dan
-- baris itu numpuk basi selamanya kalau tidak dibersihkan manual di sini.
--
-- Patokan: baris dengan imported_at LEBIH LAMA dari sync run TERBARU (nilai
-- imported_at maksimum di tabel yang sama) berarti tidak ke-upsert di run
-- terakhir -> sudah ditemukan -> hapus. Subquery dibungkus derived table (t)
-- krn MySQL tidak izinkan SELECT langsung dari tabel yang sama yg sedang di-
-- DELETE.

DELETE FROM tidak_ditemukan_usaha
WHERE imported_at < (
  SELECT max_imported_at FROM (
    SELECT MAX(imported_at) AS max_imported_at FROM tidak_ditemukan_usaha
  ) AS t
);

DELETE FROM tidak_ditemukan_keluarga
WHERE imported_at < (
  SELECT max_imported_at FROM (
    SELECT MAX(imported_at) AS max_imported_at FROM tidak_ditemukan_keluarga
  ) AS t
);
