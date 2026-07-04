-- Migration: tambah kolom sudah_ditindaklanjuti_sigempar ke tabel anomali
--
-- Latar belakang: sebelumnya sync_anomali.py DELETE baris lama saat sebuah anomali
-- hilang dari fetch FASIH. Ternyata reject_anomali.py masih perlu baca SEMUA baris
-- assignment_id dari tabel anomali (termasuk yang sudah resolved) supaya progressnya
-- tidak kacau. Solusinya: jangan hapus baris, cukup tandai kapan SIGEMPAR menyimpulkan
-- anomali itu sudah tidak aktif lagi (hilang dari fetch belumKode FASIH).
--
-- sudah_ditindaklanjuti_sigempar : NULL = masih aktif (masih muncul di fetch FASIH).
--                                  timestamp = kapan SIGEMPAR pertama kali mendeteksi
--                                  anomali ini sudah tidak muncul lagi di fetch.
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _anomali_resolved_migration;

DELIMITER //
CREATE PROCEDURE _anomali_resolved_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomali' AND COLUMN_NAME = 'sudah_ditindaklanjuti_sigempar') THEN
        ALTER TABLE anomali ADD COLUMN sudah_ditindaklanjuti_sigempar DATETIME DEFAULT NULL AFTER rule_msg;
    END IF;
END //
DELIMITER ;

CALL _anomali_resolved_migration();
DROP PROCEDURE IF EXISTS _anomali_resolved_migration;

SELECT 'Migration anomali_resolved selesai.' AS status;
