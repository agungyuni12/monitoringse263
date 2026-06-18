-- Migration: Tambah tabel progress + kolom FASIH
-- Jalankan sekali di server Coolify sebelum deploy versi baru
-- Aman dijalankan berulang (IF NOT EXISTS / IF NOT EXIST)

-- Buat tabel progress jika belum ada
CREATE TABLE IF NOT EXISTS progress (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    sls_id          INT NOT NULL,
    jumlah_submit   INT NOT NULL DEFAULT 0,
    jumlah_draft    INT NOT NULL DEFAULT 0,
    fasih_open      INT NOT NULL DEFAULT 0,
    fasih_submitted INT NOT NULL DEFAULT 0,
    fasih_approved  INT NOT NULL DEFAULT 0,
    fasih_rejected  INT NOT NULL DEFAULT 0,
    fasih_revoked   INT NOT NULL DEFAULT 0,
    fasih_total     INT NOT NULL DEFAULT 0,
    fasih_synced_at TIMESTAMP NULL DEFAULT NULL,
    kendala         TEXT,
    jumlah_diperiksa  INT NOT NULL DEFAULT 0,
    jumlah_error      INT NOT NULL DEFAULT 0,
    jumlah_observasi  INT NOT NULL DEFAULT 0,
    status_kendala  ENUM('open','in_progress','resolved','escalated') NOT NULL DEFAULT 'open',
    tindak_lanjut_pml TEXT,
    updated_at      TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_progress_sls (sls_id),
    CONSTRAINT fk_progress_sls FOREIGN KEY (sls_id) REFERENCES sls(id)
) ENGINE=InnoDB;

-- Tambah kolom FASIH jika belum ada (kompatibel MySQL & MariaDB)
DROP PROCEDURE IF EXISTS _add_fasih_columns;

DELIMITER //
CREATE PROCEDURE _add_fasih_columns()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_open') THEN
        ALTER TABLE progress ADD COLUMN fasih_open INT NOT NULL DEFAULT 0 AFTER jumlah_draft;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_submitted') THEN
        ALTER TABLE progress ADD COLUMN fasih_submitted INT NOT NULL DEFAULT 0 AFTER fasih_open;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_approved') THEN
        ALTER TABLE progress ADD COLUMN fasih_approved INT NOT NULL DEFAULT 0 AFTER fasih_submitted;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_rejected') THEN
        ALTER TABLE progress ADD COLUMN fasih_rejected INT NOT NULL DEFAULT 0 AFTER fasih_approved;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_revoked') THEN
        ALTER TABLE progress ADD COLUMN fasih_revoked INT NOT NULL DEFAULT 0 AFTER fasih_rejected;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_total') THEN
        ALTER TABLE progress ADD COLUMN fasih_total INT NOT NULL DEFAULT 0 AFTER fasih_revoked;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'progress' AND COLUMN_NAME = 'fasih_synced_at') THEN
        ALTER TABLE progress ADD COLUMN fasih_synced_at TIMESTAMP NULL DEFAULT NULL AFTER fasih_total;
    END IF;
END //
DELIMITER ;

CALL _add_fasih_columns();
DROP PROCEDURE IF EXISTS _add_fasih_columns;

SELECT 'Migration selesai. Tabel progress siap.' AS status;
