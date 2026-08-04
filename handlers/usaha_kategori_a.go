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

// UsahaKategoriARow adalah satu baris di tab "Usaha Kategori A" — bersumber
// dari tabel usaha_kategori_a (hasil sync scraper/sync_kategori_a.py, lihat
// docstring di sana: kategori KBLI 'A' = Pertanian/Kehutanan/Perikanan).
type UsahaKategoriARow struct {
	ID               int
	NamaSLS          string
	NamaKec          string
	NamaDesa         string
	NamaPPL          string
	NamaPML          string
	NamaUsaha        string
	NamaKK           string // kosong kalau usaha bangunan mandiri (bukan nempel roster keluarga)
	JenisPrelist     string // "keluarga" = usaha dalam keluarga, selain itu = bangunan mandiri
	KBLILabel        string
	Skala            string
	Pendapatan       string // format "Rp N.NNN.NNN", "-" kalau NULL
	Pengeluaran      string
	Keuntungan       string // Pendapatan - Pengeluaran, "-" kalau salah satu NULL
	Alamat           string
	AssignmentStatus string
	TanggalModified  string
}

var usahaKategoriASortCols = map[string]string{
	"lokasi":      "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas":     "ppl.name",
	"nama":        "t.nama_usaha",
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

// usahaKategoriAFilters membaca & membangun klausa WHERE, dipakai bareng oleh
// tabel (paginated) dan download (semua baris) — supaya filter selalu konsisten.
func usahaKategoriAFilters(c echo.Context) (where string, args []interface{}, kecs []string, pmlID, pplID int) {
	q := c.QueryParam("q")
	kecs = nonEmptyStrings(c.QueryParams()["kec"])
	pmlID, _ = strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ = strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where = ` WHERE (t.nama_usaha LIKE ? OR t.nama_kk LIKE ? OR t.kbli_label LIKE ? OR s.nama_sls LIKE ?)`
	args = []interface{}{like, like, like, like}
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

// AdminUsahaKategoriATable — GET /admin/table/usaha-kategori-a
func AdminUsahaKategoriATable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	q := c.QueryParam("q")

	where, args, kecs, pmlID, pplID := usahaKategoriAFilters(c)

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) FROM usaha_kategori_a t JOIN sls s ON s.id = t.sls_id`+where, args...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
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

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, usahaKategoriASortCols, "s.nama_kec, s.nama_desa, s.nama_sls, t.nama")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/usaha-kategori-a", "usaha-kategori-a-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT t.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       COALESCE(t.nama_usaha,''), COALESCE(t.nama_kk,''), COALESCE(t.jenis_prelist,''),
		       COALESCE(t.kbli_label,''), COALESCE(t.skala_usaha,''),
		       t.pendapatan, t.pengeluaran, COALESCE(t.alamat,''),
		       COALESCE(t.assignment_status,''),
		       COALESCE(DATE_FORMAT(t.tanggal_modified,'%d/%m/%Y %H:%i'),'')
		FROM usaha_kategori_a t
		JOIN sls s ON s.id = t.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []UsahaKategoriARow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r UsahaKategoriARow
			var pendapatan, pengeluaran sql.NullFloat64
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.NamaUsaha, &r.NamaKK, &r.JenisPrelist, &r.KBLILabel, &r.Skala,
				&pendapatan, &pengeluaran, &r.Alamat, &r.AssignmentStatus, &r.TanggalModified)
			r.Pendapatan = formatRupiah(pendapatan)
			r.Pengeluaran = formatRupiah(pengeluaran)
			if pendapatan.Valid && pengeluaran.Valid {
				r.Keuntungan = formatRupiah(sql.NullFloat64{Float64: pendapatan.Float64 - pengeluaran.Float64, Valid: true})
			} else {
				r.Keuntungan = "-"
			}
			list = append(list, r)
		}
	}

	pmlSelect := OOBSelect{
		TargetID: "usaha-kategori-a-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/usaha-kategori-a", HxTarget: "#usaha-kategori-a-result", HxInclude: "#usaha-kategori-a-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "usaha-kategori-a-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/usaha-kategori-a", HxTarget: "#usaha-kategori-a-result", HxInclude: "#usaha-kategori-a-filter-bar",
	}

	return c.Render(http.StatusOK, "usaha_kategori_a_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"Q":         q,
		"Kecs":      kecs,
		"PmlID":     pmlID,
		"PplID":     pplID,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}
