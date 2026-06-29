-- Hapus entri KELUARGA yang salah masuk ke keberadaan_usaha
DELETE FROM keberadaan_usaha WHERE UPPER(skala_usaha) = 'KELUARGA';
