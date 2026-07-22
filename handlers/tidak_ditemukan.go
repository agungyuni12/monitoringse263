package handlers

import (
	"database/sql"
	"fmt"
	"net/http"
	"strconv"
	"strings"

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
		HxGet: "/admin/table/tidak-ditemukan", HxTarget: "#tidak-ditemukan-result", HxInclude: "#tidak-ditemukan-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "tidak-ditemukan-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/tidak-ditemukan", HxTarget: "#tidak-ditemukan-result", HxInclude: "#tidak-ditemukan-filter-bar",
	}

	return c.Render(http.StatusOK, "tidak_ditemukan_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"Tipe":      tipe,
		"Q":         q,
		"Kecs":      kecs,
		"PmlID":     pmlID,
		"PplID":     pplID,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}

// TidakDitemukanRekapRow adalah satu baris di sub-menu Rekap (di samping Usaha/
// Keluarga) — rekap jumlah usaha & keluarga tidak ditemukan per SLS, atau
// digabung (SUM) per Desa/Kecamatan kalau level=desa|kec (mirip pola Rekap
// Keberadaan/Progres Semua SLS). NamaSLS/NamaPPL/NamaPML kosong di level desa/kec;
// JmlSLS cuma dipakai di level desa/kec.
type TidakDitemukanRekapRow struct {
	NamaSLS     string
	NamaKec     string
	NamaDesa    string
	NamaPPL     string
	NamaPML     string
	JmlSLS      int
	UsahaCnt    int
	KeluargaCnt int
}

var tidakDitemukanRekapSortCols = map[string]string{
	"lokasi":   "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas":  "ppl.name",
	"usaha":    "usaha_cnt",
	"keluarga": "keluarga_cnt",
}

// tidakDitemukanRekapFilters sama seperti tidakDitemukanFilters, tapi WHERE-nya
// terhadap tabel sls langsung (bukan tidak_ditemukan_usaha/keluarga), krn rekap
// menghitung DUA tabel sumber sekaligus per lokasi.
func tidakDitemukanRekapFilters(c echo.Context) (where string, args []interface{}, kecs []string, pmlID, pplID int, q string) {
	q = c.QueryParam("q")
	kecs = c.QueryParams()["kec"]
	pmlID, _ = strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ = strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where = ` WHERE (s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ? OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?)`
	args = []interface{}{like, like, like, like, like}
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

func tidakDitemukanRekapExtra(level, q string, kecs []string, pmlID, pplID int) string {
	extra := "&level=" + level
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
	return extra
}

// tidakDitemukanRekapTotals menghitung total baris tidak_ditemukan_usaha/keluarga
// yang cocok filter lokasi aktif (SEMUA baris, bukan cuma halaman/grup ini) —
// dipakai sbg footer "Total" yang tetap ikut filter, terlepas dari level grouping.
func tidakDitemukanRekapTotals(where string, args []interface{}) (totalUsaha, totalKeluarga int) {
	db.DB.QueryRow(`
		SELECT COUNT(*) FROM tidak_ditemukan_usaha tu
		JOIN sls s ON s.id = tu.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where, args...).Scan(&totalUsaha)
	db.DB.QueryRow(`
		SELECT COUNT(*) FROM tidak_ditemukan_keluarga tk
		JOIN sls s ON s.id = tk.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where, args...).Scan(&totalKeluarga)
	return
}

// AdminTidakDitemukanRekapTable — GET /admin/table/tidak-ditemukan-rekap?level=sls|desa|kec
// Sub-menu Rekap di samping Usaha/Keluarga: jumlah usaha & keluarga tidak
// ditemukan per lokasi, bukan daftar detail per baris.
func AdminTidakDitemukanRekapTable(c echo.Context) error {
	level := c.QueryParam("level")
	if level != "desa" && level != "kec" {
		level = "sls"
	}
	where, args, kecs, pmlID, pplID, q := tidakDitemukanRekapFilters(c)
	extra := tidakDitemukanRekapExtra(level, q, kecs, pmlID, pplID)
	totalUsaha, totalKeluarga := tidakDitemukanRekapTotals(where, args)

	pmlSelect := OOBSelect{
		TargetID: "tidak-ditemukan-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/tidak-ditemukan-rekap", HxTarget: "#tidak-ditemukan-result", HxInclude: "#tidak-ditemukan-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "tidak-ditemukan-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/tidak-ditemukan-rekap", HxTarget: "#tidak-ditemukan-result", HxInclude: "#tidak-ditemukan-filter-bar",
	}

	if level == "sls" {
		return tidakDitemukanRekapSLS(c, where, args, extra, totalUsaha, totalKeluarga, pmlSelect, pplSelect)
	}
	return tidakDitemukanRekapGroup(c, where, args, extra, level, totalUsaha, totalKeluarga, pmlSelect, pplSelect)
}

func tidakDitemukanRekapSLS(c echo.Context, where string, args []interface{}, extra string, totalUsaha, totalKeluarga int, pmlSelect, pplSelect OOBSelect) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) FROM sls s JOIN users ppl ON ppl.id=s.ppl_id JOIN users pml ON pml.id=s.pml_id`+where, args...).Scan(&total)

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, tidakDitemukanRekapSortCols, "s.nama_kec, s.nama_desa, s.nama_sls")
	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/tidak-ditemukan-rekap", "tidak-ditemukan-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''), ppl.name, pml.name,
		  (SELECT COUNT(*) FROM tidak_ditemukan_usaha tu WHERE tu.sls_id = s.id) AS usaha_cnt,
		  (SELECT COUNT(*) FROM tidak_ditemukan_keluarga tk WHERE tk.sls_id = s.id) AS keluarga_cnt
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []TidakDitemukanRekapRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r TidakDitemukanRekapRow
			rows.Scan(&r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML, &r.UsahaCnt, &r.KeluargaCnt)
			list = append(list, r)
		}
	}

	return c.Render(http.StatusOK, "tidak_ditemukan_rekap_table.html", map[string]interface{}{
		"Rows": list, "PageInfo": pageInfo, "GroupLevel": "sls",
		"TotalUsaha": totalUsaha, "TotalKeluarga": totalKeluarga,
		"PMLSelect": pmlSelect, "PPLSelect": pplSelect,
	})
}

// tidakDitemukanRekapGroup menangani level=desa|kec: query grup (mirip
// adminWideAgregatGroupTable di kbli.go), lalu isi UsahaCnt/KeluargaCnt lewat
// query terpisah ke masing2 tabel sumber, di-join per key desa/kec.
func tidakDitemukanRekapGroup(c echo.Context, where string, args []interface{}, extra, level string, totalUsaha, totalKeluarga int, pmlSelect, pplSelect OOBSelect) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	offset := (page - 1) * models.PerPage

	var totalGroups int
	var groupRows *sql.Rows
	var err error

	if level == "kec" {
		db.DB.QueryRow(`SELECT COUNT(DISTINCT s.nama_kec) FROM sls s JOIN users ppl ON ppl.id=s.ppl_id JOIN users pml ON pml.id=s.pml_id`+where, args...).Scan(&totalGroups)
		queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
		groupRows, err = db.DB.Query(`
			SELECT s.nama_kec, COUNT(DISTINCT s.id)
			FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id`+where+`
			GROUP BY s.nama_kec, s.kode_kec
			ORDER BY s.kode_kec
			LIMIT ? OFFSET ?`, queryArgs...)
	} else {
		db.DB.QueryRow(`SELECT COUNT(DISTINCT CONCAT(s.nama_desa,'|',s.nama_kec)) FROM sls s JOIN users ppl ON ppl.id=s.ppl_id JOIN users pml ON pml.id=s.pml_id`+where, args...).Scan(&totalGroups)
		queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
		groupRows, err = db.DB.Query(`
			SELECT s.nama_desa, s.nama_kec, COUNT(DISTINCT s.id)
			FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id`+where+`
			GROUP BY s.nama_desa, s.nama_kec, s.kode_desa, s.kode_kec
			ORDER BY s.kode_kec, s.kode_desa
			LIMIT ? OFFSET ?`, queryArgs...)
	}

	pageInfo := models.NewPageInfo(page, totalGroups, "/admin/table/tidak-ditemukan-rekap", "tidak-ditemukan-result", extra)
	pageInfo.FilterExtra = extra

	renderData := func(list []*TidakDitemukanRekapRow) map[string]interface{} {
		return map[string]interface{}{
			"Rows": list, "PageInfo": pageInfo, "GroupLevel": level,
			"TotalUsaha": totalUsaha, "TotalKeluarga": totalKeluarga,
			"PMLSelect": pmlSelect, "PPLSelect": pplSelect,
		}
	}

	if err != nil {
		return c.Render(http.StatusOK, "tidak_ditemukan_rekap_table.html", renderData(nil))
	}
	defer groupRows.Close()

	type groupKey struct{ desa, kec string }
	byKey := map[groupKey]*TidakDitemukanRekapRow{}
	var keys []groupKey
	var list []*TidakDitemukanRekapRow
	for groupRows.Next() {
		r := &TidakDitemukanRekapRow{}
		if level == "kec" {
			groupRows.Scan(&r.NamaKec, &r.JmlSLS)
		} else {
			groupRows.Scan(&r.NamaDesa, &r.NamaKec, &r.JmlSLS)
		}
		list = append(list, r)
		k := groupKey{r.NamaDesa, r.NamaKec}
		byKey[k] = r
		keys = append(keys, k)
	}

	if len(keys) > 0 {
		fillCount := func(table string, apply func(r *TidakDitemukanRekapRow, n int)) {
			valArgs := append([]interface{}{}, args...)
			var valQuery string
			if level == "kec" {
				ph := make([]string, len(keys))
				for i, k := range keys {
					ph[i] = "?"
					valArgs = append(valArgs, k.kec)
				}
				valQuery = fmt.Sprintf(`
					SELECT s.nama_kec, COUNT(*)
					FROM %s t
					JOIN sls s ON s.id = t.sls_id
					JOIN users ppl ON ppl.id = s.ppl_id
					JOIN users pml ON pml.id = s.pml_id
				`, table) + where + ` AND s.nama_kec IN (` + strings.Join(ph, ",") + `) GROUP BY s.nama_kec`
			} else {
				ph := make([]string, len(keys))
				for i, k := range keys {
					ph[i] = "(?,?)"
					valArgs = append(valArgs, k.desa, k.kec)
				}
				valQuery = fmt.Sprintf(`
					SELECT s.nama_desa, s.nama_kec, COUNT(*)
					FROM %s t
					JOIN sls s ON s.id = t.sls_id
					JOIN users ppl ON ppl.id = s.ppl_id
					JOIN users pml ON pml.id = s.pml_id
				`, table) + where + ` AND (s.nama_desa, s.nama_kec) IN (` + strings.Join(ph, ",") + `) GROUP BY s.nama_desa, s.nama_kec`
			}
			if valRows, err := db.DB.Query(valQuery, valArgs...); err == nil {
				defer valRows.Close()
				for valRows.Next() {
					var desa, kecName string
					var n int
					if level == "kec" {
						valRows.Scan(&kecName, &n)
					} else {
						valRows.Scan(&desa, &kecName, &n)
					}
					if r, ok := byKey[groupKey{desa, kecName}]; ok {
						apply(r, n)
					}
				}
			}
		}
		fillCount("tidak_ditemukan_usaha", func(r *TidakDitemukanRekapRow, n int) { r.UsahaCnt = n })
		fillCount("tidak_ditemukan_keluarga", func(r *TidakDitemukanRekapRow, n int) { r.KeluargaCnt = n })
	}

	return c.Render(http.StatusOK, "tidak_ditemukan_rekap_table.html", renderData(list))
}
