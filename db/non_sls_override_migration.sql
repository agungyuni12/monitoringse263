-- Migration: kolom sls.is_non_sls + trigger auto-approve untuk SLS "Non SLS"
--
-- Latar belakang: SLS "Non SLS" (area kosong seperti gunung/sawah/kebun/ladang
-- tanpa usaha/keluarga nyata — diidentifikasi dari kode_sls, BUKAN nama_sls:
-- segmen 4-digit SLS-nya >= 1000, konvensi baku BPS) selalu punya 1 assignment
-- "dummy" yang seharusnya dianggap approved, terlepas dari status approval
-- asli di FASIH. Kalau cuma di-UPDATE manual atau lewat kode sync, nilainya
-- ke-timpa ulang lagi setiap kali ada sync FASIH baru (baik dari sync_fasih.py
-- ataupun handlers/fasih_sync.go) — dan sync itu berjalan di container/app
-- yang deploy-nya kadang bermasalah, jadi fix di level kode saja tidak cukup.
--
-- Solusi: is_non_sls disimpan sebagai kolom statis di sls (nilainya TIDAK
-- PERNAH berubah lagi setelah di-set, karena klasifikasi SLS itu permanen),
-- dan TRIGGER di tabel progress yang otomatis memaksa fasih_approved_pengawas
-- & jumlah_submit minimal 1 (dibatasi fasih_total) SEBELUM baris tersimpan —
-- jadi berlaku murni di level database, tidak peduli proses/app mana yang
-- menulis ke progress, dan tidak butuh deploy kode apapun.
--
-- Aman dijalankan berulang (kondisional via PROCEDURE, DROP TRIGGER IF EXISTS).

DROP PROCEDURE IF EXISTS _non_sls_override_migration;

DELIMITER //
CREATE PROCEDURE _non_sls_override_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sls' AND COLUMN_NAME = 'is_non_sls') THEN
        ALTER TABLE sls ADD COLUMN is_non_sls TINYINT(1) NOT NULL DEFAULT 0 AFTER kode_sls;
    END IF;
END //
DELIMITER ;

CALL _non_sls_override_migration();
DROP PROCEDURE IF EXISTS _non_sls_override_migration;

UPDATE sls
SET is_non_sls = 1
WHERE CAST(SUBSTRING(kode_sls, 11, 4) AS UNSIGNED) >= 1000;

DROP TRIGGER IF EXISTS trg_progress_non_sls_bi;
DROP TRIGGER IF EXISTS trg_progress_non_sls_bu;

DELIMITER //
CREATE TRIGGER trg_progress_non_sls_bi
BEFORE INSERT ON progress
FOR EACH ROW
BEGIN
    DECLARE v_is_non_sls TINYINT DEFAULT 0;
    SELECT is_non_sls INTO v_is_non_sls FROM sls WHERE id = NEW.sls_id;
    IF v_is_non_sls = 1 AND NEW.fasih_total > 0 THEN
        SET NEW.fasih_approved_pengawas = LEAST(GREATEST(NEW.fasih_approved_pengawas, 1), NEW.fasih_total);
        SET NEW.jumlah_submit = LEAST(GREATEST(NEW.jumlah_submit, NEW.fasih_approved_pengawas), NEW.fasih_total);
    END IF;
END //

CREATE TRIGGER trg_progress_non_sls_bu
BEFORE UPDATE ON progress
FOR EACH ROW
BEGIN
    DECLARE v_is_non_sls TINYINT DEFAULT 0;
    SELECT is_non_sls INTO v_is_non_sls FROM sls WHERE id = NEW.sls_id;
    IF v_is_non_sls = 1 AND NEW.fasih_total > 0 THEN
        SET NEW.fasih_approved_pengawas = LEAST(GREATEST(NEW.fasih_approved_pengawas, 1), NEW.fasih_total);
        SET NEW.jumlah_submit = LEAST(GREATEST(NEW.jumlah_submit, NEW.fasih_approved_pengawas), NEW.fasih_total);
    END IF;
END //
DELIMITER ;

-- Terapkan langsung ke baris yang sudah ada sekarang juga (bukan cuma yang
-- akan datang) supaya efeknya kelihatan seketika.
UPDATE progress p
JOIN sls s ON s.id = p.sls_id
SET p.fasih_approved_pengawas = LEAST(GREATEST(p.fasih_approved_pengawas, 1), p.fasih_total),
    p.jumlah_submit           = LEAST(GREATEST(p.jumlah_submit, LEAST(GREATEST(p.fasih_approved_pengawas, 1), p.fasih_total)), p.fasih_total)
WHERE s.is_non_sls = 1
  AND p.fasih_total > 0;

SELECT 'Migration non_sls_override selesai.' AS status,
       (SELECT COUNT(*) FROM sls WHERE is_non_sls = 1) AS jumlah_non_sls;
