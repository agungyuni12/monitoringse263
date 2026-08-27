-- Jalankan sekali (opsional): scraper/sync_usaha.py sudah membuat tabel ini
-- otomatis lewat ensure_tables() saat run berikutnya (CREATE TABLE IF NOT
-- EXISTS), tapi bisa juga diterapkan manual duluan kalau perlu tersedia
-- sebelum sync jalan.
--
-- Kebalikan dari usaha_keluarga_bku_duplikat: usaha jenis_prelist='keluarga'
-- yg hp/email-nya TERISI tapi TIDAK ketemu pasangan usaha BKU manapun (belum
-- pernah dibuatkan usaha BKU tersendiri). Kandidat utk "diangkat" jadi usaha
-- BKU baru oleh petugas. KBLI kategori A (Pertanian) dikecualikan — usaha
-- pertanian nempel keluarga itu wajar (carry-over ST2023), bukan kandidat BKU.
--
-- resolved_at NULL = masih terdeteksi (belum ada BKU-nya).
-- resolved_at diisi = sudah tidak masuk kriteria ini lagi di sync terakhir
-- (bisa krn sudah ketemu usaha BKU yg cocok — otomatis lanjut muncul sbg
-- duplikat aktif di usaha_keluarga_bku_duplikat — atau hp/email-nya
-- berubah/dihapus). Baris TETAP disimpan sbg riwayat, tidak dihapus.
CREATE TABLE IF NOT EXISTS usaha_keluarga_tanpa_bku (
  id                      INT NOT NULL AUTO_INCREMENT,
  sls_id                  INT NOT NULL,
  assignment_id_keluarga  VARCHAR(64) NOT NULL,
  nama_usaha_keluarga     VARCHAR(255) DEFAULT NULL,
  hp                      VARCHAR(30) DEFAULT NULL,
  email                   VARCHAR(150) DEFAULT NULL,
  first_detected_at       DATETIME NOT NULL,
  synced_at               DATETIME NOT NULL,
  resolved_at             DATETIME DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ukttb_assignment (assignment_id_keluarga),
  KEY idx_ukttb_sls (sls_id),
  KEY idx_ukttb_resolved (resolved_at),
  CONSTRAINT fk_ukttb_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
