-- Koreksi Total Prelist Awal utk 8 SLS yg total prelist-nya (Dashboard SE2026,
-- coverage_usaha_keluarga kode 14 Keluarga + kode sintetis 90002 Usaha BKU) lebih
-- tinggi dari target resmi (sls.target). Pengurangan diproporsikan antara Prelist
-- Keluarga (kode 14) dan Prelist Usaha BKU (kode 90002) sesuai porsi lama masing2.
--
-- Semua 8 SLS ini komponen usahanya 100% UMK (kode 110); UB(108)/UM(109) = 0,
-- jadi kode 110 ikut dikoreksi sama dgn 90002 supaya konsisten & tidak ke-overwrite
-- balik ke angka lama pas sync_usaha_bku_prelist_total() jalan lagi (kode 90002
-- dihitung ulang otomatis dari SUM(108,109,110) tiap sync).
--
-- SLS  | Lama (K/U/Total) -> Baru (K/U/Total)
--   RT 002 DUSUN JAMBU: K=64/U=6/Tot=70 -> K=59/U=5/Tot=64
--   PERSAWAHAN 1002 KARAMABURA: K=1/U=24/Tot=25 -> K=1/U=16/Tot=17
--   NON SLS GUNUNG PANGGO WOKO: K=0/U=21/Tot=21 -> K=0/U=17/Tot=17
--   SOLAGUDU SONCIU, SOTANDA MOTI SORO: K=0/U=33/Tot=33 -> K=0/U=29/Tot=29
--   PERSAWAHAN MANGGEASI 1001: K=0/U=13/Tot=13 -> K=0/U=4/Tot=4
--   PERSAWAHAN MANGGEASI 2002: K=0/U=34/Tot=34 -> K=0/U=18/Tot=18
--   GUNUNG RANGGO 1002: K=0/U=70/Tot=70 -> K=0/U=55/Tot=55
--   NON SLS GUNUNG LAE: K=0/U=32/Tot=32 -> K=0/U=21/Tot=21

-- RT 002 DUSUN JAMBU (sls_id=52)
UPDATE `coverage_usaha_keluarga`
SET total_value = 59, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 52 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 5, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 52 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 5, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 52 AND kode_indikator = '90002';

-- PERSAWAHAN 1002 KARAMABURA (sls_id=1113)
UPDATE `coverage_usaha_keluarga`
SET total_value = 1, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1113 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 16, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1113 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 16, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1113 AND kode_indikator = '90002';

-- NON SLS GUNUNG PANGGO WOKO (sls_id=84)
UPDATE `coverage_usaha_keluarga`
SET total_value = 0, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 84 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 17, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 84 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 17, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 84 AND kode_indikator = '90002';

-- SOLAGUDU SONCIU, SOTANDA MOTI SORO (sls_id=298)
UPDATE `coverage_usaha_keluarga`
SET total_value = 0, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 298 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 29, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 298 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 29, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 298 AND kode_indikator = '90002';

-- PERSAWAHAN MANGGEASI 1001 (sls_id=1053)
UPDATE `coverage_usaha_keluarga`
SET total_value = 0, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1053 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 4, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1053 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 4, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1053 AND kode_indikator = '90002';

-- PERSAWAHAN MANGGEASI 2002 (sls_id=1054)
UPDATE `coverage_usaha_keluarga`
SET total_value = 0, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1054 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 18, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1054 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 18, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 1054 AND kode_indikator = '90002';

-- GUNUNG RANGGO 1002 (sls_id=100)
UPDATE `coverage_usaha_keluarga`
SET total_value = 0, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 100 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 55, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 100 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 55, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 100 AND kode_indikator = '90002';

-- NON SLS GUNUNG LAE (sls_id=115)
UPDATE `coverage_usaha_keluarga`
SET total_value = 0, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 115 AND kode_indikator = '14';
UPDATE `coverage_usaha_keluarga`
SET total_value = 21, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 115 AND kode_indikator = '110';
UPDATE `coverage_usaha_keluarga`
SET total_value = 21, synced_at = '2026-07-17 09:04:14'
WHERE sls_id = 115 AND kode_indikator = '90002';
