-- Migration: tambah kolom sync_keterangan ke tabel keberadaan_usaha
--
-- Latar belakang: sync_keberadaan.py fetch detail tiap assignment ke FASIH satu-satu.
-- Kalau fetch-nya gagal (HTTP error/timeout/exception — lihat __fetch_error di
-- _page_fetch_batch), baris tsb tetap diupsert dengan kode/label NULL — SAMA PERSIS
-- dengan kasus "assignment memang belum diisi PPL". Dua kondisi ini beda jauh secara
-- operasional (satu perlu di-retry sync-nya, satu perlu ditindaklanjuti PPL-nya) tapi
-- sebelum kolom ini ada, keduanya tidak bisa dibedakan dari data di DB.
--
-- sync_keterangan : NULL = fetch terakhir berhasil. Diisi pesan error (mis.
--                   "Gagal: HTTP 500") kalau fetch terakhir gagal.
--
-- Aman dijalankan berulang (kondisional via PROCEDURE).

DROP PROCEDURE IF EXISTS _keberadaan_sync_keterangan_migration;

DELIMITER //
CREATE PROCEDURE _keberadaan_sync_keterangan_migration()
BEGIN
    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'keberadaan_usaha' AND COLUMN_NAME = 'sync_keterangan') THEN
        ALTER TABLE keberadaan_usaha ADD COLUMN sync_keterangan VARCHAR(255) DEFAULT NULL AFTER synced_at;
    END IF;
END //
DELIMITER ;

CALL _keberadaan_sync_keterangan_migration();
DROP PROCEDURE IF EXISTS _keberadaan_sync_keterangan_migration;

SELECT 'Migration keberadaan_sync_keterangan selesai.' AS status;
