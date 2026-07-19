-- Koreksi manual progress.jumlah_submit/fasih_total utk SLS "LAHAN PERTANIAN
-- SO KABISA" (kode_sls 5205051005100200, Kec MANGGALEWA, Desa DOROMELO) —
-- data di tabel progress ketinggalan (stale) dari FASIH.
--
-- Dikonfirmasi langsung ke FASIH (endpoint report-progress-by-responsibility,
-- baik mode target=TARGET_ONLY maupun target=ALL memberi hasil identik):
--   status "SUBMITTED BY Pencacah" count=5 — TIDAK ADA status lain sama sekali.
-- Artinya nilai asli yang seharusnya: fasih_total=5, fasih_submitted=5,
-- jumlah_submit=5, fasih_approved_pengawas=0 (belum ada yang di-approve
-- Pengawas beneran).
--
-- fasih_approved_pengawas di-set ke 1 (bukan 0) di sini supaya konsisten dgn
-- override "Non SLS" yang otomatis jalan tiap sync FASIH selesai (lihat
-- applyNonSLSApprovedOverride() di handlers/fasih_sync.go) — kode_sls ini
-- segmen SLS-nya (posisi 11-14) = "1002" >= 1000, jadi tergolong Non SLS
-- (lahan pertanian, bukan pemukiman), dan Pengawas dianggap otomatis approve
-- minimal 1 assignment utk kategori ini.
--
-- CATATAN: ini tambalan sementara utk SATU SLS ini saja. SLS lain kemungkinan
-- ada yang stale dengan cara serupa — solusi permanennya tetap re-run sync
-- FASIH penuh (scraper/sync_fasih.py atau handler Go-nya), yang akan
-- membetulkan semua SLS sekaligus, bukan cuma yang ini.

UPDATE progress p
JOIN sls s ON s.id = p.sls_id
SET p.fasih_total = 5,
    p.fasih_submitted = 5,
    p.jumlah_submit = 5,
    p.fasih_approved_pengawas = 1
WHERE s.kode_sls = '5205051005100200';
