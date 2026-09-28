-- Migration: tabel titik koordinat per-assignment (peta sebaran titik SLS)
--
-- Sumber: FASIH Dashboard SQL Lab, tabel base_table_assignment (kolom
-- assignment_id, level_6_full_code, latitude, longitude) — di-scrape oleh
-- scraper/sync_usaha.py FASE 3 (satu sesi login yang sama dgn sync usaha/
-- keluarga). Satu baris = satu assignment dengan GPS valid (lat/lng = 0
-- dibuang karena belum ada GPS). level_6_full_code (16 digit) dipetakan ke
-- sls.kode_sls untuk info SLS di popup peta & filter per kecamatan.
--
-- Dipakai oleh handlers/admin.go:AdminAssignmentPoints dan menu baru
-- "Peta Titik SLS" di templates/admin.html.

CREATE TABLE IF NOT EXISTS assignment_koordinat (
    id            INT NOT NULL AUTO_INCREMENT,
    assignment_id VARCHAR(64) NOT NULL,
    sls_id        INT DEFAULT NULL,
    latitude      DECIMAL(11,7) NOT NULL,
    longitude     DECIMAL(11,7) NOT NULL,
    updated_at    DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ak_assignment (assignment_id),
    KEY idx_ak_sls (sls_id),
    CONSTRAINT fk_ak_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SELECT 'Migration assignment_koordinat selesai.' AS status;
