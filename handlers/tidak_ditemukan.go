package handlers

import (
	"fmt"
	"net/http"
	"strconv"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	"monitoringse/models"
)

// TidakDitemukanRow adalah satu baris di sub-menu Usaha/Keluarga saat filter status
// "Tidak Ditemukan" dipilih di tab Keberadaan — bersumber dari tidak_ditemukan_usaha/
// tidak_ditemukan_keluarga (hasil import sekali-jalan, lihat scraper/import_tidak_ditemukan.py),
// bukan dari keberadaan_usaha seperti filter status lainnya.
type TidakDitemukanRow struct {
	ID               int
	NamaSLS          string
	NamaKec          string
	NamaDesa         string
	NamaPPL          string
	NamaPML          string
	Nama             string
	Skala            string // kosong utk tipe keluarga
	Alamat           string
	AssignmentStatus string
	TanggalModified  string
}

var tidakDitemukanSortCols = map[string]string{
	"lokasi":  "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas": "ppl.name",
	"nama":    "t.nama",
	"skala":   "t.skala_usaha",
	"status":  "t.assignment_status",
	"tanggal": "t.tanggal_modified",
}

func tidakDitemukanTable(tipe string) string {
	if tipe == "keluarga" {
		return "tidak_ditemukan_keluarga"
	}
	return "tidak_ditemukan_usaha"
}

// tidakDitemukanFilters membaca & membangun klausa WHERE yang dipakai bareng oleh
// tabel (paginated) dan download (semua baris) — supaya filter selalu konsisten.
func tidakDitemukanFilters(c echo.Context, tipe string) (where string, args []interface{}, kecs []string, pmlID, pplID int) {
	q := c.QueryParam("q")
	kecs = c.QueryParams()["kec"]
	pmlID, _ = strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ = strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where = ` WHERE (t.nama LIKE ? OR s.nama_sls LIKE ?)`
	args = []interface{}{like, like}
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

// AdminTidakDitemukanTable — GET /admin/table/tidak-ditemukan?tipe=usaha|keluarga
// Sub-menu Usaha/Keluarga yang muncul di tab Keberadaan saat filter status
// "Tidak Ditemukan" dipilih (lihat panel-keberadaan di templates/admin.html).
func AdminTidakDitemukanTable(c echo.Context) error {
	tipe := c.QueryParam("tipe")
	if tipe != "keluarga" {
		tipe = "usaha"
	}
	table := tidakDitemukanTable(tipe)

	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	q := c.QueryParam("q")

	where, args, kecs, pmlID, pplID := tidakDitemukanFilters(c, tipe)

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) FROM `+table+` t JOIN sls s ON s.id = t.sls_id`+where, args...).Scan(&total)

	extra := "&tipe=" + tipe
	if q != "" {
		extra += "&q=" + q
	}
	for _, v := range kecs {
		extra += "&kec=" + v
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}
	if pplID > 0 {
		extra += fmt.Sprintf("&ppl_id=%d", pplID)
	}

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, tidakDitemukanSortCols, "s.nama_kec, s.nama_desa, s.nama_sls, t.nama")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/tidak-ditemukan", "tidak-ditemukan-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	skalaCol := "''"
	if tipe == "usaha" {
		skalaCol = "COALESCE(t.skala_usaha,'')"
	}

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT t.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       COALESCE(t.nama,''), `+skalaCol+`, COALESCE(t.alamat,''),
		       COALESCE(t.assignment_status,''),
		       COALESCE(DATE_FORMAT(t.tanggal_modified,'%d/%m/%Y %H:%i'),'')
		FROM `+table+` t
		JOIN sls s ON s.id = t.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []TidakDitemukanRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r TidakDitemukanRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.Nama, &r.Skala, &r.Alamat, &r.AssignmentStatus, &r.TanggalModified)
			list = append(list, r)
		}
	}

	pmlSelect := OOBSelect{
		TargetID: "tidak-ditemukan-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/tidak-ditemukan", HxTarget: "#tidak-ditemukan-result", HxInclude: "#tidak-ditemukan-filter-bar, #tidak-ditemukan-result",
	}
	pplSelect := OOBSelect{
		TargetID: "tidak-ditemukan-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/tidak-ditemukan", HxTarget: "#tidak-ditemukan-result", HxInclude: "#tidak-ditemukan-filter-bar, #tidak-ditemukan-result",
	}

	return c.Render(http.StatusOK, "tidak_ditemukan_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"Tipe":      tipe,
		"KecList":   queryKecList(),
		"Q":         q,
		"Kecs":      kecs,
		"PmlID":     pmlID,
		"PplID":     pplID,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}
