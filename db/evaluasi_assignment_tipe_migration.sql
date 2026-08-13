-- Migration: evaluasi_assignment mendukung lebih dari satu tipe sumber.
-- Sebelumnya evaluasi cuma utk usaha (tabel usaha_ekonomi, form lengkap:
-- rincian kuesioner + jenis kesalahan + rekomendasi). Sekarang ditambah tipe
-- "tidak ditemukan" (usaha / keluarga / usaha dalam keluarga dari tabel
-- tidak_ditemukan_*) yang form-nya cukup catatan bebas.
--
-- tipe: 'usaha' (default, usaha_ekonomi), 'td_usaha', 'td_keluarga',
--       'td_usaha_keluarga'.
-- nama: snapshot nama record saat dicatat, supaya rekap & dasbor PML/PPL gak
--       perlu join ke tabel sumber yang berbeda-beda per tipe.
-- rincian_kuesioner & jenis_kesalahan jadi NULL utk tipe tidak-ditemukan
-- (catatan bebas disimpan di rekomendasi).
ALTER TABLE evaluasi_assignment
    ADD COLUMN tipe VARCHAR(30) NOT NULL DEFAULT 'usaha' AFTER assignment_id,
    ADD COLUMN nama VARCHAR(255) NULL AFTER tipe,
    MODIFY COLUMN rincian_kuesioner VARCHAR(150) NULL,
    MODIFY COLUMN jenis_kesalahan ENUM('konflik_isian','nilai_ekstrem','tidak_konsisten','lainnya') NULL,
    ADD KEY idx_ea_tipe (tipe);

SELECT 'Migration evaluasi_assignment tipe selesai.' AS status;
