-- Migration: tambah kolom is_resolved_fasih ke tabel anomali
--
-- Latar belakang: sebelumnya cuma ada sudah_ditindaklanjuti_sigempar, yaitu status
-- yang DISIMPULKAN SENDIRI oleh SIGEMPAR (assignment hilang dari fetch = dianggap
-- selesai). Padahal API FASIH/dashboard (/api/mikro/anomali-case-kab) sudah punya
-- field is_resolved sendiri yang menyatakan status tindak lanjut versi FASIH —
-- selama ini datanya tidak pernah dibaca/disimpan oleh sync_anomali.py.
--
-- Dua status ini bisa berbeda: karena fetch selalu menyertakan sudah_indikator,
-- assignment yang sudah is_resolved=true di FASIH tetap bisa muncul di fetch
-- berikutnya, sehingga sudah_ditindaklanjuti_sigempar tidak akan ke-set walau
-- sebenarnya sudah ditindaklanjuti di FASIH.
--
-- is_resolved_fasih : NULL/0 = belum (menurut FASIH), 1 = sudah (menurut FASIH).
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _anomali_is_resolved_fasih_migration;

DELIMITER //
CREATE PROCEDURE _anomali_is_resolved_fasih_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomali' AND COLUMN_NAME = 'is_resolved_fasih') THEN
        ALTER TABLE anomali ADD COLUMN is_resolved_fasih TINYINT(1) DEFAULT 0 AFTER sudah_ditindaklanjuti_sigempar;
    END IF;
END //
DELIMITER ;

CALL _anomali_is_resolved_fasih_migration();
DROP PROCEDURE IF EXISTS _anomali_is_resolved_fasih_migration;

SELECT 'Migration anomali_is_resolved_fasih selesai.' AS status;
