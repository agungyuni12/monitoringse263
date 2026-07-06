-- Migration: Tabel riwayat harian progres (untuk tab "Tren Progres" di admin)
-- Menyimpan snapshot agregat submit/draft/approved/rejected/revoked per PPL & per PML,
-- satu baris per (entity_type, entity_id, tanggal). Diisi otomatis oleh scheduler harian.
-- Aman dijalankan berulang.

CREATE TABLE IF NOT EXISTS progress_trend (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    entity_type   ENUM('ppl','pml') NOT NULL,
    entity_id     INT NOT NULL,
    tanggal       DATE NOT NULL,
    jumlah_submit INT NOT NULL DEFAULT 0,
    jumlah_draft  INT NOT NULL DEFAULT 0,
    approved      INT NOT NULL DEFAULT 0,
    rejected      INT NOT NULL DEFAULT 0,
    revoked       INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_progress_trend (entity_type, entity_id, tanggal)
) ENGINE=InnoDB;

SELECT 'Migration selesai. Tabel progress_trend siap.' AS status;
