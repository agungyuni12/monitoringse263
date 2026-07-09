-- Jumlah usaha per kategori KBLI per SLS, disinkron dari Dashboard SE2026
-- (GET /api/agregat/fasih?level=sub_sls&indikator=...&kabupaten=...) oleh
-- scraper/sync_kbli.py. "sub_sls" di dashboard itu granularitasnya sama
-- persis dengan sls.kode_sls kita (id_wilayah == kode_sls), bukan level baru.
CREATE TABLE IF NOT EXISTS kbli_usaha (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  sls_id         INT NOT NULL,
  kode_indikator VARCHAR(10) NOT NULL,
  nama_indikator VARCHAR(255) NOT NULL,
  satuan         VARCHAR(50) DEFAULT '',
  total_value    INT DEFAULT NULL,
  is_agregat     TINYINT DEFAULT NULL,
  synced_at      DATETIME,
  UNIQUE KEY uk_sls_indikator (sls_id, kode_indikator),
  KEY idx_sls_id (sls_id),
  CONSTRAINT fk_kbli_sls FOREIGN KEY (sls_id) REFERENCES sls(id)
);
