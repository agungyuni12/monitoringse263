package handlers

import (
	"database/sql"
	"net/http"
	"strconv"
	"strings"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"
)

// UsahaEkonomiRow adalah satu baris di tab "Usaha (Data Ekonomi)" — bersumber
// dari tabel usaha_ekonomi (hasil sync scraper/sync_usaha_ekonomi.py, lihat
// docstring di sana). Mencakup SEMUA usaha (semua kategori KBLI), bukan cuma
// kategori A seperti versi awal fitur ini.
type UsahaEkonomiRow struct {
	ID           int
	NamaSLS      string
	NamaKec      string
	NamaDesa     string
	NamaPPL      string
	NamaPML      string
	NamaUsaha    string
	NamaKK       string // kosong kalau usaha bangunan mandiri (bukan nempel roster keluarga)
	JenisPrelist string // "keluarga" = usaha dalam keluarga, selain itu = bangunan mandiri
	Kategori     string // kategori KBLI 1-huruf (A-U)
	KBLILabel    string
	// Kolom ekonomi: masing-masing punya varian umum/tahunan + "_bln" (bulanan)
	// di DB, tapi praktiknya cuma salah satu yang terisi per usaha (usaha lapor
	// bulanan ATAU tahunan, jarang dua-duanya) — digabung jadi satu tampilan
	// oleh formatRupiahBlnThn/formatAngkaBlnThn, suffix " (bln)" kalau yang
	// terisi itu nilai bulanan.
	Pendapatan       string // format "Rp N.NNN.NNN", "-" kalau NULL
	Pengeluaran      string
	BiayaProduksi    string
	Gaji             string
	Operasional      string
	LuasTanah        string // m², "-" kalau NULL
	TkDibayar        string
	KegUtama         string
	AssignmentStatus string
	TanggalModified  string
	FasihLink        string
	// Klaim evaluasi organik (lihat handlers/organik_evaluasi.go — organik
	// pertama yang mencatat perbaikan utk assignment ini "mengklaim"-nya).
	// ClaimedByID 0 = belum ada organik yang klaim. Cuma dipakai/ditampilkan
	// kalau ShowClaim true (tab Organik), tetap di-query jg utk Admin krn
	// murah (subquery per baris halaman, bukan per seluruh tabel).
	ClaimedByID   int
	ClaimedByName string
}

var usahaEkonomiSortCols = map[string]string{
	"lokasi":      "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas":     "ppl.name",
	"nama":        "t.nama_usaha",
	"kategori":    "t.kategori",
	"kbli":        "t.kbli_label",
	"pendapatan":  "t.pendapatan",
	"pengeluaran": "t.pengeluaran",
	"status":      "t.assignment_status",
	"tanggal":     "t.tanggal_modified",
}

// formatRibuan format angka bulat pakai titik sbg pemisah ribuan (gaya
// Indonesia), mis. 12345678 → "12.345.678".
func formatRibuan(n int64) string {
	neg := n < 0
	if neg {
		n = -n
	}
	s := strconv.FormatInt(n, 10)
	var parts []string
	for len(s) > 3 {
		parts = append([]string{s[len(s)-3:]}, parts...)
		s = s[:len(s)-3]
	}
	parts = append([]string{s}, parts...)
	out := strings.Join(parts, ".")
	if neg {
		out = "-" + out
	}
	return out
}

func formatRupiah(v sql.NullFloat64) string {
	if !v.Valid {
		return "-"
	}
	return "Rp " + formatRibuan(int64(v.Float64))
}

func formatInt(v sql.NullInt64) string {
	if !v.Valid {
		return "-"
	}
	return formatRibuan(v.Int64)
}

// formatRupiahBlnThn gabung kolom Rupiah umum/tahunan + bulanan jadi satu
// tampilan — utamakan nilai bulanan kalau terisi (dikasih suffix " (bln)"
// biar jelas beda basis waktunya dari nilai tahunan), fallback ke nilai
// umum/tahunan. Praktiknya cuma salah satu yang pernah terisi per usaha.
func formatRupiahBlnThn(annual, monthly sql.NullFloat64) string {
	if monthly.Valid {
		return "Rp " + formatRibuan(int64(monthly.Float64)) + " (bln)"
	}
	if annual.Valid {
		return "Rp " + formatRibuan(int64(annual.Float64))
	}
	return "-"
}

// formatAngkaBlnThn — sama seperti formatRupiahBlnThn tapi tanpa prefix "Rp",
// dipakai utk luas_tanah_bln/luas_tanah_thn (satuan m²).
func formatAngkaBlnThn(monthly, annual sql.NullFloat64) string {
	if monthly.Valid {
		return formatRibuan(int64(monthly.Float64)) + " (bln)"
	}
	if annual.Valid {
		return formatRibuan(int64(annual.Float64))
	}
	return "-"
}

// usahaEkonomiFilters membaca & membangun klausa WHERE, dipakai bareng oleh
// tabel (paginated) dan download (semua baris) — supaya filter selalu konsisten.
func usahaEkonomiFilters(c echo.Context) (where string, args []interface{}, kecs []string, pmlID, pplID int) {
	q := c.QueryParam("q")
	kecs = nonEmptyStrings(c.QueryParams()["kec"])
	pmlID, _ = strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ = strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where = ` WHERE (t.nama_usaha LIKE ? OR t.nama_kk LIKE ? OR t.kbli_label LIKE ? OR s.nama_sls LIKE ?)`
	args = []interface{}{like, like, like, like}
	if kat := c.QueryParam("kategori"); kat != "" {
		where += ` AND t.kategori = ?`
		args = append(args, kat)
	}
	if status := c.QueryParam("status"); status != "" {
		where += ` AND t.assignment_status LIKE ?`
		args = append(args, "%"+status+"%")
	}
	if len(kecs) > 0 {
		where += ` AND s.nama_kec IN (` + placeholders(len(kecs)) + `)`
		for _, k := range kecs {
			args = append(args, k)
		}
	}
	if pmlID > 0 {
		where += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}
	return
}

// AdminUsahaEkonomiTable — GET /admin/table/usaha-ekonomi
func AdminUsahaEkonomiTable(c echo.Context) error {
	return usahaEkonomiTable(c, "/admin/table/usaha-ekonomi")
}

// OrganikUsahaEkonomiTable — GET /organik/table/usaha-ekonomi
// Sama persis dengan versi admin (data usaha se-kabupaten, bukan cuma wilayah
// sendiri) — organik memang punya akses pengawasan lintas wilayah, dipakai
// sebagai referensi sebelum memilih assignment untuk dievaluasi.
func OrganikUsahaEkonomiTable(c echo.Context) error {
	return usahaEkonomiTable(c, "/organik/table/usaha-ekonomi")
}

// usahaEkonomiTable — logika bersama tab "Usaha (Data Ekonomi)", dipakai baik
// oleh Admin maupun Organik. basePath dipakai utk link pagination/sorting
// (models.PageInfo.BaseURL) dan hx-get filter berjenjang PML/PPL, supaya tiap
// role tetap memanggil route miliknya sendiri (route admin diproteksi
// RequireRole("admin"), route organik RequireRole("organik")) — target DOM id
// ("usaha-ekonomi-result" dkk) sengaja tetap sama krn masing-masing halaman
// (admin.html/organik.html) punya markup filter bar sendiri dgn id yg sama.
func usahaEkonomiTable(c echo.Context, basePath string) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	q := c.QueryParam("q")
	kategori := c.QueryParam("kategori")

	where, args, kecs, pmlID, pplID := usahaEkonomiFilters(c)

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) FROM usaha_ekonomi t JOIN sls s ON s.id = t.sls_id`+where, args...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	if kategori != "" {
		extra += "&kategori=" + kategori
	}
	if status := c.QueryParam("status"); status != "" {
		extra += "&status=" + status
	}
	for _, v := range kecs {
		extra += "&kec=" + v
	}
	if pmlID > 0 {
		extra += "&pml_id=" + strconv.Itoa(pmlID)
	}
	if pplID > 0 {
		extra += "&ppl_id=" + strconv.Itoa(pplID)
	}

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, usahaEkonomiSortCols, "s.nama_kec, s.nama_desa, s.nama_sls, t.nama_usaha")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, basePath, "usaha-ekonomi-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT t.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       COALESCE(t.nama_usaha,''), COALESCE(t.nama_kk,''), COALESCE(t.jenis_prelist,''),
		       COALESCE(t.kategori,''), COALESCE(t.kbli_label,''),
		       t.pendapatan, t.pendapatan_bln, t.pengeluaran, t.pengeluaran_bln,
		       t.biaya_produksi, t.biaya_produksi_bln, t.gaji, t.gaji_bln,
		       t.operasional, t.operasional_bln, t.luas_tanah_bln, t.luas_tanah_thn,
		       t.tk_dibayar,
		       COALESCE(t.keg_utama,''),
		       COALESCE(t.assignment_status,''),
		       COALESCE(DATE_FORMAT(t.tanggal_modified,'%d/%m/%Y %H:%i'),''),
		       t.assignment_id,
		       (SELECT ea.organik_id FROM evaluasi_assignment ea WHERE ea.assignment_id = t.assignment_id ORDER BY ea.created_at ASC LIMIT 1),
		       (SELECT u2.name FROM evaluasi_assignment ea JOIN users u2 ON u2.id = ea.organik_id
		          WHERE ea.assignment_id = t.assignment_id ORDER BY ea.created_at ASC LIMIT 1)
		FROM usaha_ekonomi t
		JOIN sls s ON s.id = t.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []UsahaEkonomiRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r UsahaEkonomiRow
			var pendapatan, pendapatanBln, pengeluaran, pengeluaranBln sql.NullFloat64
			var biayaProduksi, biayaProduksiBln, gaji, gajiBln sql.NullFloat64
			var operasional, operasionalBln, luasTanahBln, luasTanahThn sql.NullFloat64
			var tkDibayar sql.NullInt64
			var assignmentID string
			var claimedByID sql.NullInt64
			var claimedByName sql.NullString
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.NamaUsaha, &r.NamaKK, &r.JenisPrelist, &r.Kategori, &r.KBLILabel,
				&pendapatan, &pendapatanBln, &pengeluaran, &pengeluaranBln,
				&biayaProduksi, &biayaProduksiBln, &gaji, &gajiBln,
				&operasional, &operasionalBln, &luasTanahBln, &luasTanahThn,
				&tkDibayar,
				&r.KegUtama, &r.AssignmentStatus, &r.TanggalModified, &assignmentID,
				&claimedByID, &claimedByName)
			r.Pendapatan = formatRupiahBlnThn(pendapatan, pendapatanBln)
			r.Pengeluaran = formatRupiahBlnThn(pengeluaran, pengeluaranBln)
			r.BiayaProduksi = formatRupiahBlnThn(biayaProduksi, biayaProduksiBln)
			r.Gaji = formatRupiahBlnThn(gaji, gajiBln)
			r.Operasional = formatRupiahBlnThn(operasional, operasionalBln)
			r.LuasTanah = formatAngkaBlnThn(luasTanahBln, luasTanahThn)
			r.TkDibayar = formatInt(tkDibayar)
			r.FasihLink = fasihSMLink(assignmentID)
			if claimedByID.Valid {
				r.ClaimedByID = int(claimedByID.Int64)
				r.ClaimedByName = claimedByName.String
			}
			list = append(list, r)
		}
	}

	pmlSelect := OOBSelect{
		TargetID: "usaha-ekonomi-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: basePath, HxTarget: "#usaha-ekonomi-result", HxInclude: "#usaha-ekonomi-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "usaha-ekonomi-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: basePath, HxTarget: "#usaha-ekonomi-result", HxInclude: "#usaha-ekonomi-filter-bar",
	}

	return c.Render(http.StatusOK, "usaha_ekonomi_table.html", map[string]interface{}{
		"Rows":          list,
		"PageInfo":      pageInfo,
		"Q":             q,
		"Kategori":      kategori,
		"Kecs":          kecs,
		"PmlID":         pmlID,
		"PplID":         pplID,
		"PMLSelect":     pmlSelect,
		"PPLSelect":     pplSelect,
		"ShowClaim":     mw.SessionRole(c) == "organik",
		"CurrentUserID": mw.SessionUserID(c),
	})
}
