-- Migration: Rename kolom fasih lama + tambah kolom breakdown per level
-- Aman dijalankan berulang (kondisional via PROCEDURE)
-- Jalankan di server Coolify sebelum deploy versi baru

DROP PROCEDURE IF EXISTS _fasih_breakdown_migration;

DELIMITER //
CREATE PROCEDURE _fasih_breakdown_migration()
BEGIN
    -- 1. Rename fasih_approved → fasih_approved_pengawas
    IF EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_approved') THEN
        ALTER TABLE progress CHANGE COLUMN fasih_approved fasih_approved_pengawas INT NOT NULL DEFAULT 0;
    END IF;

    -- 2. Rename fasih_rejected → fasih_rejected_pengawas
    IF EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_rejected') THEN
        ALTER TABLE progress CHANGE COLUMN fasih_rejected fasih_rejected_pengawas INT NOT NULL DEFAULT 0;
    END IF;

    -- 3. Rename fasih_revoked → fasih_revoked_pengawas
    IF EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_revoked') THEN
        ALTER TABLE progress CHANGE COLUMN fasih_revoked fasih_revoked_pengawas INT NOT NULL DEFAULT 0;
    END IF;

    -- 4. Pastikan fasih_approved_pengawas ada (jika tabel baru dibuat dari schema lama tanpa rename)
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_approved_pengawas') THEN
        ALTER TABLE progress ADD COLUMN fasih_approved_pengawas INT NOT NULL DEFAULT 0 AFTER fasih_submitted;
    END IF;

    -- 5. Pastikan fasih_rejected_pengawas ada
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_rejected_pengawas') THEN
        ALTER TABLE progress ADD COLUMN fasih_rejected_pengawas INT NOT NULL DEFAULT 0 AFTER fasih_approved_pengawas;
    END IF;

    -- 6. Pastikan fasih_revoked_pengawas ada
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_revoked_pengawas') THEN
        ALTER TABLE progress ADD COLUMN fasih_revoked_pengawas INT NOT NULL DEFAULT 0 AFTER fasih_rejected_pengawas;
    END IF;

    -- 7. Tambah kolom kabupaten
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_approved_kabupaten') THEN
        ALTER TABLE progress ADD COLUMN fasih_approved_kabupaten INT NOT NULL DEFAULT 0 AFTER fasih_revoked_pengawas;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_rejected_kabupaten') THEN
        ALTER TABLE progress ADD COLUMN fasih_rejected_kabupaten INT NOT NULL DEFAULT 0 AFTER fasih_approved_kabupaten;
    END IF;

    -- 8. Tambah kolom provinsi
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_approved_provinsi') THEN
        ALTER TABLE progress ADD COLUMN fasih_approved_provinsi INT NOT NULL DEFAULT 0 AFTER fasih_rejected_kabupaten;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_rejected_provinsi') THEN
        ALTER TABLE progress ADD COLUMN fasih_rejected_provinsi INT NOT NULL DEFAULT 0 AFTER fasih_approved_provinsi;
    END IF;

    -- 9. Tambah kolom pusat
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_approved_pusat') THEN
        ALTER TABLE progress ADD COLUMN fasih_approved_pusat INT NOT NULL DEFAULT 0 AFTER fasih_rejected_provinsi;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_rejected_pusat') THEN
        ALTER TABLE progress ADD COLUMN fasih_rejected_pusat INT NOT NULL DEFAULT 0 AFTER fasih_approved_pusat;
    END IF;
END //
DELIMITER ;

CALL _fasih_breakdown_migration();
DROP PROCEDURE IF EXISTS _fasih_breakdown_migration;

SELECT 'Migration fasih_breakdown selesai.' AS status;
