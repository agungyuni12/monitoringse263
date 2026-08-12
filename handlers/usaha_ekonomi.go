package handlers

import (
	"database/sql"
	"net/http"
	"strconv"
	"strings"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	"monitoringse/models"
)

// UsahaEkonomiRow adalah satu baris di tab "Usaha (Data Ekonomi)" — bersumber
// dari tabel usaha_ekonomi (hasil sync scraper/sync_usaha_ekonomi.py, lihat
// docstring di sana). Mencakup SEMUA usaha (semua kategori KBLI), bukan cuma
// kategori A seperti versi awal fitur ini.
type UsahaEkonomiRow struct {
	ID               int
	NamaSLS          string
	NamaKec          string
	NamaDesa         string
	NamaPPL          string
	NamaPML          string
	NamaUsaha        string
	NamaKK           string // kosong kalau usaha bangunan mandiri (bukan nempel roster keluarga)
	JenisPrelist     string // "keluarga" = usaha dalam keluarga, selain itu = bangunan mandiri
	Kategori         string // kategori KBLI 1-huruf (A-U)
	KBLILabel        string
	Pendapatan       string // format "Rp N.NNN.NNN", "-" kalau NULL
	PendapatanBln    string
	Pengeluaran      string
	PengeluaranBln   string
	BiayaProduksi    string
	Gaji             string
	Operasional      string
	LuasTanahBln     string // m², "-" kalau NULL — jarang terisi (khusus usaha yg lapor per bulan)
	LuasTanahThn     string // m², "-" kalau NULL — coverage tinggi (93,9%, di luar blok ekonomi)
	TkDibayar        string
	KegUtama         string
	AssignmentStatus string
	TanggalModified  string
	FasihLink        string
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

// formatAngka format angka bulat TANPA prefix "Rp" — dipakai buat kolom
// non-Rupiah kayak luas_tanah (satuan m²).
func formatAngka(v sql.NullFloat64) string {
	if !v.Valid {
		return "-"
	}
	return formatRibuan(int64(v.Float64))
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
		       t.biaya_produksi, t.gaji, t.operasional, t.luas_tanah_bln, t.luas_tanah_thn,
		       t.tk_dibayar,
		       COALESCE(t.keg_utama,''),
		       COALESCE(t.assignment_status,''),
		       COALESCE(DATE_FORMAT(t.tanggal_modified,'%d/%m/%Y %H:%i'),''),
		       t.assignment_id
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
			var biayaProduksi, gaji, operasional, luasTanahBln, luasTanahThn sql.NullFloat64
			var tkDibayar sql.NullInt64
			var assignmentID string
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.NamaUsaha, &r.NamaKK, &r.JenisPrelist, &r.Kategori, &r.KBLILabel,
				&pendapatan, &pendapatanBln, &pengeluaran, &pengeluaranBln,
				&biayaProduksi, &gaji, &operasional, &luasTanahBln, &luasTanahThn,
				&tkDibayar,
				&r.KegUtama, &r.AssignmentStatus, &r.TanggalModified, &assignmentID)
			r.Pendapatan = formatRupiah(pendapatan)
			r.PendapatanBln = formatRupiah(pendapatanBln)
			r.Pengeluaran = formatRupiah(pengeluaran)
			r.PengeluaranBln = formatRupiah(pengeluaranBln)
			r.BiayaProduksi = formatRupiah(biayaProduksi)
			r.Gaji = formatRupiah(gaji)
			r.Operasional = formatRupiah(operasional)
			r.LuasTanahBln = formatAngka(luasTanahBln)
			r.LuasTanahThn = formatAngka(luasTanahThn)
			r.TkDibayar = formatInt(tkDibayar)
			r.FasihLink = fasihSMLink(assignmentID)
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
		"Rows":      list,
		"PageInfo":  pageInfo,
		"Q":         q,
		"Kategori":  kategori,
		"Kecs":      kecs,
		"PmlID":     pmlID,
		"PplID":     pplID,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}
