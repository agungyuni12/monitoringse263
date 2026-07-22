-- Migration: tabel detail "Tidak Ditemukan" Usaha & Keluarga
--
-- Latar belakang: tab Keberadaan (keberadaan_usaha) cuma menyimpan detail Usaha.
-- Keluarga sama sekali belum punya tabel detail (cuma agregat di
-- coverage_usaha_keluarga). Dua tabel ini menampung hasil import sekali-jalan
-- dari export FASIH "*_tidak_ditemukan_*.csv" (lihat scraper/import_tidak_ditemukan.py),
-- dipakai sub-menu Usaha/Keluarga saat filter status "Tidak Ditemukan" dipilih
-- di tab Keberadaan.

CREATE TABLE IF NOT EXISTS tidak_ditemukan_usaha (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    sls_id            INT NOT NULL,
    assignment_id     VARCHAR(64) NOT NULL,
    nama              VARCHAR(255),
    skala_usaha       VARCHAR(50),
    alamat            VARCHAR(255),
    assignment_status VARCHAR(50),
    tanggal_modified  DATETIME,
    imported_at       DATETIME,
    UNIQUE KEY uq_tdu_assignment (assignment_id),
    KEY idx_tdu_sls (sls_id),
    CONSTRAINT fk_tdu_sls FOREIGN KEY (sls_id) REFERENCES sls(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tidak_ditemukan_keluarga (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    sls_id            INT NOT NULL,
    assignment_id     VARCHAR(64) NOT NULL,
    nama              VARCHAR(255),
    alamat            VARCHAR(255),
    assignment_status VARCHAR(50),
    tanggal_modified  DATETIME,
    imported_at       DATETIME,
    UNIQUE KEY uq_tdk_assignment (assignment_id),
    KEY idx_tdk_sls (sls_id),
    CONSTRAINT fk_tdk_sls FOREIGN KEY (sls_id) REFERENCES sls(id)
) ENGINE=InnoDB;

SELECT 'Migration tidak_ditemukan selesai.' AS status;
