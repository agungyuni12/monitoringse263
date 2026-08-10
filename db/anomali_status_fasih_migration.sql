-- Migration: tambah kolom status_fasih ke tabel anomali
--
-- Latar belakang: dashboard SE2026 sebenarnya punya TIGA status tindak lanjut per
-- jenis anomali (lihat anomali_config / anomali_keluarga_config di /api/admin/config):
--   belumKode   -> "Belum Ditindaklanjuti"
--   sudahKode   -> "Sudah Ditindaklanjuti dengan Perbaikan" (data diperbaiki)
--   sesuaiKode  -> "Sudah Ditindaklanjuti dengan Penjelasan (Sesuai Kondisi Lapangan)"
--
-- sync_anomali.py sebelumnya CUMA fetch belumKode + sudahKode — kasus sesuaiKode
-- (status "sesuai") tidak pernah di-fetch sama sekali, jadi hilang total dari
-- tabel ini. Field is_resolved_fasih juga tidak bisa dipakai untuk membedakan
-- 3 status ini: API mengembalikan is_resolved=false untuk status "sesuai" juga
-- (sama seperti "belum"), padahal itu sebenarnya sudah ditindaklanjuti.
--
-- status_fasih menyimpan field case_status API apa adanya: 'belum' | 'sudah' | 'sesuai'.
-- Kolom is_resolved_fasih dibiarkan ada (histori lama), tapi tidak dipakai lagi.
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _anomali_status_fasih_migration;

DELIMITER //
CREATE PROCEDURE _anomali_status_fasih_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomali' AND COLUMN_NAME = 'status_fasih') THEN
        ALTER TABLE anomali ADD COLUMN status_fasih VARCHAR(10) NOT NULL DEFAULT 'belum' AFTER is_resolved_fasih;
        UPDATE anomali SET status_fasih = 'sudah' WHERE is_resolved_fasih = 1;
    END IF;
END //
DELIMITER ;

CALL _anomali_status_fasih_migration();
DROP PROCEDURE IF EXISTS _anomali_status_fasih_migration;

SELECT 'Migration anomali_status_fasih selesai.' AS status;
