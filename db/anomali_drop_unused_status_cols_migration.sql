-- Migration: hapus kolom anomali yang sudah tidak dipakai lagi
--
-- sudah_ditindaklanjuti_sigempar : heuristik lama SIGEMPAR (assignment hilang
--   dari fetch = dianggap selesai) — sudah diganti oleh status_fasih yang
--   diambil langsung dari case_status API dashboard. Tidak ditulis atau dibaca
--   lagi oleh kode manapun (lihat db/anomali_status_fasih_migration.sql).
-- is_resolved_fasih : boolean lama dari field is_resolved API — TIDAK bisa
--   membedakan status "sesuai" (sudah ditindaklanjuti dengan penjelasan) dari
--   "belum", jadi digantikan status_fasih yang 3-arah ('belum'/'sudah'/'sesuai').
--   Sudah tidak dibaca oleh Go/templates lagi.
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _anomali_drop_unused_status_cols_migration;

DELIMITER //
CREATE PROCEDURE _anomali_drop_unused_status_cols_migration()
BEGIN
    IF EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomali' AND COLUMN_NAME = 'sudah_ditindaklanjuti_sigempar') THEN
        ALTER TABLE anomali DROP COLUMN sudah_ditindaklanjuti_sigempar;
    END IF;
    IF EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomali' AND COLUMN_NAME = 'is_resolved_fasih') THEN
        ALTER TABLE anomali DROP COLUMN is_resolved_fasih;
    END IF;
END //
DELIMITER ;

CALL _anomali_drop_unused_status_cols_migration();
DROP PROCEDURE IF EXISTS _anomali_drop_unused_status_cols_migration;

SELECT 'Migration anomali_drop_unused_status_cols selesai.' AS status;
