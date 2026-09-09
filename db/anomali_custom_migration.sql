-- Anomali Custom: hasil query SQL ad-hoc per rule dari "Daftar Anomali
-- Masing-masing Tim.xlsx" — rule di luar 15 rule bawaan FASIH (128-135 usaha,
-- 136-144 keluarga) yang disepakati Kab. Dompu (kolom "5205" = Sepakat) dan
-- dijalankan lewat Superset SQL Lab FASIH Dashboard (fasih-dashboard.bps.go.id),
-- BUKAN API dashboard-se2026 biasa yang dipakai tabel `anomali`.
-- Lihat scraper/sync_anomali_custom.py & scraper/anomali_custom_rules.json.
CREATE TABLE IF NOT EXISTS anomali_custom_rule (
  rule_no          INT PRIMARY KEY,
  jenis            VARCHAR(20) NOT NULL DEFAULT '',
  deskripsi        VARCHAR(255) NOT NULL DEFAULT '',
  pengusul         VARCHAR(100) NOT NULL DEFAULT '',
  keterangan       TEXT,
  last_synced_at   DATETIME DEFAULT NULL,
  last_row_count   INT NOT NULL DEFAULT 0,
  last_error       TEXT
);

-- row_hash = MD5 gabungan kolom kunci hasil query (beda-beda tiap rule, tidak
-- semuanya punya assignment_id tunggal) — dipakai sbg dedup key pengganti PK
-- alami karena skema kolom hasil query tidak seragam antar rule.
CREATE TABLE IF NOT EXISTS anomali_custom (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  rule_no           INT NOT NULL,
  row_hash          CHAR(32) NOT NULL,
  assignment_id     CHAR(36) NOT NULL DEFAULT '',
  nama              VARCHAR(255) NOT NULL DEFAULT '',
  kode_wilayah      VARCHAR(30) NOT NULL DEFAULT '',
  sls_id            INT DEFAULT NULL,
  data_json         JSON,
  first_detected_at DATETIME DEFAULT NULL,
  synced_at         DATETIME DEFAULT NULL,
  UNIQUE KEY uk_rule_row (rule_no, row_hash),
  KEY idx_rule_no (rule_no),
  KEY idx_sls_id (sls_id),
  KEY idx_assignment (assignment_id),
  CONSTRAINT fk_anomali_custom_rule FOREIGN KEY (rule_no) REFERENCES anomali_custom_rule(rule_no)
  -- SENGAJA tidak FK ke sls(id): sls adalah tabel yang paling sering dibaca
  -- oleh script sync lain (sync_usaha.py dkk) — CREATE TABLE dengan FK ke
  -- tabel itu butuh metadata lock yang gampang nyangkut lama nunggu kosong
  -- (terbukti di produksi: nunggu >85 detik gak pernah dapat giliran).
  -- sls_id tetap diisi dari lookup yang valid di kode Go/Python, jadi
  -- integritas referensialnya dijaga di level aplikasi.
);
