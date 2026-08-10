-- Tabel usaha_ekonomi (tab "Usaha (Data Ekonomi)" di admin) — SEMUA usaha
-- (semua kategori KBLI, bukan cuma kategori A) lengkap dgn data ekonomi
-- (pendapatan, pengeluaran, biaya, gaji, tenaga kerja) hasil sync
-- scraper/sync_usaha_ekonomi.py dari Superset SQL Lab FASIH Dashboard —
-- lihat docstring modul itu utk detail sumber & alasan tiap kolom.
--
-- JALANKAN INI DULU sebelum apply db/usaha_ekonomi_result_*.sql (data-nya),
-- kalau belum ada tabelnya di DB target (error umum: "#1146 - Table
-- 'se2026.usaha_ekonomi' doesn't exist").
CREATE TABLE IF NOT EXISTS usaha_ekonomi (
  id                  INT NOT NULL AUTO_INCREMENT,
  sls_id              INT NOT NULL,
  assignment_id       VARCHAR(64) NOT NULL,
  nama_usaha          VARCHAR(255) DEFAULT NULL,
  nama_kk             VARCHAR(255) DEFAULT NULL,
  jenis_prelist       VARCHAR(30) DEFAULT NULL,
  kategori            VARCHAR(5) DEFAULT NULL,
  kbli_label          TEXT DEFAULT NULL,
  pendapatan          DOUBLE DEFAULT NULL,
  pendapatan_bln      DOUBLE DEFAULT NULL,
  pengeluaran         DOUBLE DEFAULT NULL,
  pengeluaran_bln     DOUBLE DEFAULT NULL,
  biaya_produksi      DOUBLE DEFAULT NULL,
  biaya_produksi_bln  DOUBLE DEFAULT NULL,
  gaji                DOUBLE DEFAULT NULL,
  gaji_bln            DOUBLE DEFAULT NULL,
  operasional         DOUBLE DEFAULT NULL,
  operasional_bln     DOUBLE DEFAULT NULL,
  non_operasional     DOUBLE DEFAULT NULL,
  non_operasional_bln DOUBLE DEFAULT NULL,
  tk_dibayar          INT DEFAULT NULL,
  keg_utama           TEXT DEFAULT NULL,
  assignment_status   VARCHAR(50) DEFAULT NULL,
  tanggal_modified    DATETIME DEFAULT NULL,
  imported_at         DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ue_assignment (assignment_id),
  KEY idx_ue_sls (sls_id),
  CONSTRAINT fk_ue_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
