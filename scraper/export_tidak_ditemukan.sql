-- Query sumber data tidak_ditemukan_per_desa.xlsx (dibuat oleh
-- scraper/export_tidak_ditemukan.py) -- nama-nama usaha & keluarga tidak
-- ditemukan per kecamatan/desa/SLS, BUKAN cuma rekap jumlahnya. Disimpan di
-- sini biar bisa dijalankan langsung lewat mysql client / SQL Lab tanpa
-- perlu Python kalau cuma mau lihat/cek datanya.

-- Usaha tidak ditemukan (bangunan mandiri + roster keluarga, lihat docstring
-- scraper/sync_usaha.py soal jenis_prelist)
SELECT s.nama_kec, s.nama_desa, s.nama_sls, s.kode_sls,
       tu.nama, tu.skala_usaha, tu.jenis_prelist, tu.alamat,
       tu.assignment_status, tu.tanggal_modified
FROM tidak_ditemukan_usaha tu
JOIN sls s ON tu.sls_id = s.id
ORDER BY s.nama_kec, s.nama_desa, tu.nama;

-- Keluarga tidak ditemukan
SELECT s.nama_kec, s.nama_desa, s.nama_sls, s.kode_sls,
       tk.nama, tk.alamat, tk.assignment_status, tk.tanggal_modified
FROM tidak_ditemukan_keluarga tk
JOIN sls s ON tk.sls_id = s.id
ORDER BY s.nama_kec, s.nama_desa, tk.nama;
