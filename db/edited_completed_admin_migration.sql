-- Migration: kolom fasih_edited_admin & fasih_completed_admin di tabel progress
--
-- Latar belakang: status "EDITED BY Admin ..." dan "COMPLETED BY Admin ..."
-- (Kabupaten/Provinsi/Pusat) sudah dihitung sebagai "submit" (lihat fix
-- sebelumnya di sync_fasih.py / handlers/fasih_sync.go), tapi jumlahnya tidak
-- pernah disimpan terpisah — cuma numpuk ke total jumlah_submit. Supaya kolom
-- "Approved" di Per PML/Per PPL/Progres Semua SLS bisa ikut menghitung
-- assignment yang sudah di-edit/completed oleh admin (di level manapun) sebagai
-- bagian dari "sudah diperiksa", perlu kolom penyimpanan sendiri.
--
-- Digabung semua level admin (Kabupaten+Provinsi+Pusat) jadi satu kolom per
-- jenis status (bukan dipecah per level) — sesuai kebutuhan yang diminta.
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _edited_completed_admin_migration;

DELIMITER //
CREATE PROCEDURE _edited_completed_admin_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_edited_admin') THEN
        ALTER TABLE progress ADD COLUMN fasih_edited_admin INT NOT NULL DEFAULT 0 AFTER fasih_rejected_pusat;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_completed_admin') THEN
        ALTER TABLE progress ADD COLUMN fasih_completed_admin INT NOT NULL DEFAULT 0 AFTER fasih_edited_admin;
    END IF;
END //
DELIMITER ;

CALL _edited_completed_admin_migration();
DROP PROCEDURE IF EXISTS _edited_completed_admin_migration;

SELECT 'Migration edited_completed_admin selesai.' AS status;
