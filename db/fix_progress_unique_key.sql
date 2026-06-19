-- Fix: hapus duplikat progress dan tambah UNIQUE KEY pada sls_id
-- Jalankan SEKALI di server jika total target terus bertambah setiap refresh

-- Langkah 1: hapus baris duplikat, simpan yang id-nya terkecil (paling lama)
DELETE p1 FROM progress p1
JOIN progress p2 ON p1.sls_id = p2.sls_id AND p1.id > p2.id;

-- Langkah 2: tambah UNIQUE KEY jika belum ada
DROP PROCEDURE IF EXISTS _add_progress_unique_key;

DELIMITER //
CREATE PROCEDURE _add_progress_unique_key()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'progress'
          AND INDEX_NAME = 'uq_progress_sls'
    ) THEN
        ALTER TABLE progress ADD UNIQUE KEY uq_progress_sls (sls_id);
    END IF;
END //
DELIMITER ;

CALL _add_progress_unique_key();
DROP PROCEDURE IF EXISTS _add_progress_unique_key;

SELECT COUNT(*) AS jumlah_row_progress FROM progress;
SELECT 'UNIQUE KEY berhasil ditambahkan. Sync berikutnya akan UPDATE, bukan INSERT.' AS status;
