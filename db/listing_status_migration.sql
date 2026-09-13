-- Migration: Tabel status listing FASIH per SLS (untuk menu "Rekap Listing"
-- dan perhitungan "SLS Selesai" di menu Pembayaran).
-- Diisi otomatis oleh scraper/sync_listing.py dari endpoint
-- /app/api/assignment-general/api/assignment-region/datatable.
-- Aman dijalankan berulang.

CREATE TABLE IF NOT EXISTS listing_status (
  sls_id            INT NOT NULL,
  done_listing      TINYINT(1) NOT NULL DEFAULT 0,
  done_tarik_sample TINYINT(1) NOT NULL DEFAULT 0,
  synced_at         DATETIME DEFAULT NULL,
  PRIMARY KEY (sls_id),
  CONSTRAINT fk_listing_sls FOREIGN KEY (sls_id) REFERENCES sls (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SELECT 'Migration selesai. Tabel listing_status siap.' AS status;
