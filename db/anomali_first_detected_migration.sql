-- Migration: tambah kolom first_detected_at ke tabel anomali
--
-- Latar belakang: kolom synced_at selama ini dipakai buat 2 hal sekaligus di UI,
-- padahal maknanya cuma "terakhir kali anomali ini masih terdeteksi aktif" —
-- setiap sync yang masih menemukan assignment yang sama, synced_at-nya di-refresh
-- ke waktu sync itu (lihat upsert_anomali di scraper/sync_anomali.py). Jadi kalau
-- satu anomali sudah aktif dari 10 hari lalu dan belum ditindaklanjuti, synced_at-nya
-- akan selalu menunjukkan "hari ini", bukan kapan dia pertama kali muncul.
--
-- first_detected_at diisi SEKALI saat baris pertama kali di-INSERT, dan TIDAK
-- pernah di-UPDATE lagi setelahnya (lihat ON DUPLICATE KEY UPDATE di upsert_anomali
-- yang sengaja tidak menyertakan kolom ini).
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _anomali_first_detected_migration;

DELIMITER //
CREATE PROCEDURE _anomali_first_detected_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomali' AND COLUMN_NAME = 'first_detected_at') THEN
        ALTER TABLE anomali ADD COLUMN first_detected_at DATETIME DEFAULT NULL AFTER synced_at;
        -- Default awal: samakan dengan synced_at yang ada sekarang (baseline
        -- terbaik selama belum di-backfill dari histori dump lama).
        UPDATE anomali SET first_detected_at = synced_at WHERE first_detected_at IS NULL;
    END IF;
END //
DELIMITER ;

CALL _anomali_first_detected_migration();
DROP PROCEDURE IF EXISTS _anomali_first_detected_migration;

SELECT 'Migration anomali_first_detected selesai.' AS status;
