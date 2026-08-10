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
	Skala            string
	Pendapatan       string // format "Rp N.NNN.NNN", "-" kalau NULL
	PendapatanBln    string
	Pengeluaran      string
	PengeluaranBln   string
	BiayaPembelian   string
	BiayaProduksi    string
	Gaji             string
	Operasional      string
	NonOperasional   string
	TkDibayar        string // "L/P" mis. "3/2", "-" kalau keduanya NULL
	TkTdkDibayar     string
	KegUtama         string
	Alamat           string
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
	"skala":       "t.skala_usaha",
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

func formatTk(laki, pr sql.NullInt64) string {
	if !laki.Valid && !pr.Valid {
		return "-"
	}
	l, p := int64(0), int64(0)
	if laki.Valid {
		l = laki.Int64
	}
	if pr.Valid {
		p = pr.Int64
	}
	return strconv.FormatInt(l, 10) + "/" + strconv.FormatInt(p, 10)
}

func formatInt(v sql.NullInt64) string {
	if !v.Valid {
		return "-"
	}
	return formatRibuan(v.Int64)
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
	pageInfo := models.NewPageInfo(page, total, "/admin/table/usaha-ekonomi", "usaha-ekonomi-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT t.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       COALESCE(t.nama_usaha,''), COALESCE(t.nama_kk,''), COALESCE(t.jenis_prelist,''),
		       COALESCE(t.kategori,''), COALESCE(t.kbli_label,''), COALESCE(t.skala_usaha,''),
		       t.pendapatan, t.pendapatan_bln, t.pengeluaran, t.pengeluaran_bln,
		       t.biaya_pembelian, t.biaya_produksi, t.gaji, t.operasional, t.non_operasional,
		       t.tk_laki, t.tk_pr, t.tk_tdk_dibayar,
		       COALESCE(t.keg_utama,''), COALESCE(t.alamat,''),
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
			var biayaPembelian, biayaProduksi, gaji, operasional, nonOperasional sql.NullFloat64
			var tkLaki, tkPr, tkTdkDibayar sql.NullInt64
			var assignmentID string
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.NamaUsaha, &r.NamaKK, &r.JenisPrelist, &r.Kategori, &r.KBLILabel, &r.Skala,
				&pendapatan, &pendapatanBln, &pengeluaran, &pengeluaranBln,
				&biayaPembelian, &biayaProduksi, &gaji, &operasional, &nonOperasional,
				&tkLaki, &tkPr, &tkTdkDibayar,
				&r.KegUtama, &r.Alamat, &r.AssignmentStatus, &r.TanggalModified, &assignmentID)
			r.Pendapatan = formatRupiah(pendapatan)
			r.PendapatanBln = formatRupiah(pendapatanBln)
			r.Pengeluaran = formatRupiah(pengeluaran)
			r.PengeluaranBln = formatRupiah(pengeluaranBln)
			r.BiayaPembelian = formatRupiah(biayaPembelian)
			r.BiayaProduksi = formatRupiah(biayaProduksi)
			r.Gaji = formatRupiah(gaji)
			r.Operasional = formatRupiah(operasional)
			r.NonOperasional = formatRupiah(nonOperasional)
			r.TkDibayar = formatTk(tkLaki, tkPr)
			r.TkTdkDibayar = formatInt(tkTdkDibayar)
			r.FasihLink = fasihSMLink(assignmentID)
			list = append(list, r)
		}
	}

	pmlSelect := OOBSelect{
		TargetID: "usaha-ekonomi-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/usaha-ekonomi", HxTarget: "#usaha-ekonomi-result", HxInclude: "#usaha-ekonomi-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "usaha-ekonomi-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/usaha-ekonomi", HxTarget: "#usaha-ekonomi-result", HxInclude: "#usaha-ekonomi-filter-bar",
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
