-- Jalankan sekali (opsional): scraper/sync_usaha.py sudah membuat tabel ini
-- otomatis lewat ensure_tables() saat run berikutnya (CREATE TABLE IF NOT
-- EXISTS), tapi bisa juga diterapkan manual duluan kalau perlu tersedia
-- sebelum sync jalan.
--
-- Menyimpan pasangan usaha jenis_prelist='keluarga' yg hp/email-nya SAMA
-- PERSIS dengan usaha BKU (mandiri) yg sudah ada — indikasi usaha yg sama
-- sudah tercatat dobel (nempel keluarga & berdiri sendiri sbg BKU). Petugas
-- perlu memindahkan/menutup yg di keluarga lewat FASIH-mobile masing2.
--
-- resolved_at NULL = masih terdeteksi duplikat (perlu dipindah).
-- resolved_at diisi = sudah tidak terdeteksi lagi di sync terakhir (asumsi
-- sudah dipindahkan petugas) — baris TETAP disimpan sbg riwayat, tidak
-- dihapus.
CREATE TABLE IF NOT EXISTS usaha_keluarga_bku_duplikat (
  id                      INT NOT NULL AUTO_INCREMENT,
  sls_id                  INT NOT NULL,
  assignment_id_keluarga  VARCHAR(64) NOT NULL,
  nama_usaha_keluarga     VARCHAR(255) DEFAULT NULL,
  assignment_id_bku       VARCHAR(64) NOT NULL,
  nama_usaha_bku          VARCHAR(255) DEFAULT NULL,
  match_field             VARCHAR(10) NOT NULL,
  match_value             VARCHAR(150) NOT NULL,
  first_detected_at       DATETIME NOT NULL,
  synced_at               DATETIME NOT NULL,
  resolved_at             DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ukbd_pair (assignment_id_keluarga, assignment_id_bku, match_field),
  KEY idx_ukbd_sls (sls_id),
  KEY idx_ukbd_resolved (resolved_at),
  CONSTRAINT fk_ukbd_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
