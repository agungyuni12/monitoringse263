-- Migration: tambah kolom gate_label & assignment_status ke keberadaan_usaha
--
-- Latar belakang: kolom keberadaan_label (Ditemukan/Tidak Ditemukan/Baru/dst) cuma
-- terisi kalau pertanyaan "keberadaan_usaha#" benar-benar ditanyakan di kuesioner FASIH.
-- Tapi kalau keluarga/bangunannya sendiri sudah ditemukan "Tidak Ditemukan" di pertanyaan
-- gate sebelumnya (FASIH menandai jawaban itu dengan literal "(STOP)" di label-nya), alur
-- kuesioner berhenti duluan dan pertanyaan keberadaan_usaha# tidak pernah muncul — meski
-- assignment-nya sendiri tetap bisa SUBMITTED. Tanpa kolom ini, kasus tsb salah kehitung
-- sebagai "Belum Diisi" padahal sebenarnya sudah selesai (memang tidak ada usaha).
--
-- gate_label        : label jawaban gate keluarga/bangunan yang menghentikan alur
--                      (mis. "Tidak Ditemukan (STOP)"), NULL kalau tidak ada gate-stop.
-- assignment_status : assignment_status_alias dari FASIH (OPEN, SUBMITTED BY Pencacah,
--                      APPROVED BY Pengawas, dst) — status submit assignment ybs.
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _keberadaan_gate_migration;

DELIMITER //
CREATE PROCEDURE _keberadaan_gate_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'keberadaan_usaha' AND COLUMN_NAME = 'gate_label') THEN
        ALTER TABLE keberadaan_usaha ADD COLUMN gate_label VARCHAR(150) DEFAULT NULL AFTER keberadaan_label;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'keberadaan_usaha' AND COLUMN_NAME = 'assignment_status') THEN
        ALTER TABLE keberadaan_usaha ADD COLUMN assignment_status VARCHAR(50) DEFAULT NULL AFTER gate_label;
    END IF;
END //
DELIMITER ;

CALL _keberadaan_gate_migration();
DROP PROCEDURE IF EXISTS _keberadaan_gate_migration;

SELECT 'Migration keberadaan_gate selesai.' AS status;
