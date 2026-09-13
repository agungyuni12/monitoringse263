package handlers

import (
	"fmt"
	"math"
	"net/http"
	"strconv"
	"time"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
	"github.com/xuri/excelize/v2"
)

// Rekap status listing FASIH (tabel listing_status, hasil scraper/sync_listing.py)
// per SLS / per Desa / per Kecamatan — pola sama seperti "Progres Semua SLS"
// (lihat queryAdminSLS/ByDesa/ByKec di admin.go), tapi ukurannya done_listing,
// bukan submit/approve assignment usaha-keluarga.

type ListingSLSRow struct {
	ID              int
	KodeSLS         string
	NamaSLS         string
	NamaPPL         string
	NamaPML         string
	NamaKec         string
	NamaDesa        string
	DoneListing     bool
	DoneTarikSample bool
	SyncedAt        string
}

type ListingDesaRow struct {
	NamaDesa string
	NamaKec  string
	JmlSLS   int
	JmlDone  int
	PctDone  float64
}

type ListingKecRow struct {
	NamaKec string
	JmlSLS  int
	JmlDone int
	PctDone float64
}

var adminListingSLSSortCols = map[string]string{
	"kode_sls": "s.kode_sls",
	"nama_sls": "s.nama_sls",
	"ppl":      "ppl.name",
	"pml":      "pml.name",
	"lokasi":   "s.nama_kec, s.nama_desa",
	"status":   "COALESCE(ls.done_listing,0)",
}

func queryAdminListing(page int, q, sort, dir string) ([]ListingSLSRow, models.PageInfo) {
	like := "%" + q + "%"
	var total int
	db.DB.QueryRow(`
		SELECT COUNT(*) FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
		  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?`,
		like, like, like, like, like).Scan(&total)

	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, adminListingSLSSortCols, "s.kode_kec, s.kode_desa, s.kode_sls")

	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.id, s.kode_sls, s.nama_sls, ppl.name, pml.name,
		       COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       COALESCE(ls.done_listing,0), COALESCE(ls.done_tarik_sample,0),
		       COALESCE(ls.synced_at, '')
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN listing_status ls ON ls.sls_id = s.id
		WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
		  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
		`+orderBy+`
		LIMIT ? OFFSET ?`,
		like, like, like, like, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/listing", "admin-listing-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []ListingSLSRow
	for rows.Next() {
		var r ListingSLSRow
		var synced *string
		rows.Scan(&r.ID, &r.KodeSLS, &r.NamaSLS, &r.NamaPPL, &r.NamaPML,
			&r.NamaKec, &r.NamaDesa, &r.DoneListing, &r.DoneTarikSample, &synced)
		if synced != nil {
			r.SyncedAt = *synced
		}
		list = append(list, r)
	}
	return list, pageInfo
}

var adminListingDesaSortCols = map[string]string{
	"nama_desa": "s.nama_desa",
	"nama_kec":  "s.nama_kec",
	"jml_sls":   "COUNT(DISTINCT s.id)",
	"jml_done":  "SUM(COALESCE(ls.done_listing,0))",
	"pct_done":  "(CASE WHEN COUNT(DISTINCT s.id)=0 THEN 0 ELSE SUM(COALESCE(ls.done_listing,0))/COUNT(DISTINCT s.id) END)",
}

func queryAdminListingByDesa(page int, q, sort, dir string) ([]ListingDesaRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := "&level=desa"
	if q != "" {
		extra = "&q=" + q + "&level=desa"
	}
	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT CONCAT(s.nama_desa,'|',s.nama_kec)) FROM sls s
		WHERE s.nama_desa LIKE ? OR s.nama_kec LIKE ?`, like, like).Scan(&total)

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, adminListingDesaSortCols, "s.kode_kec, s.kode_desa")
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.nama_desa, s.nama_kec,
		       COUNT(DISTINCT s.id),
		       SUM(COALESCE(ls.done_listing,0))
		FROM sls s
		LEFT JOIN listing_status ls ON ls.sls_id = s.id
		WHERE s.nama_desa LIKE ? OR s.nama_kec LIKE ?
		GROUP BY s.nama_desa, s.nama_kec, s.kode_desa, s.kode_kec
		`+orderBy+`
		LIMIT ? OFFSET ?`, like, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/listing", "admin-listing-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []ListingDesaRow
	for rows.Next() {
		var r ListingDesaRow
		rows.Scan(&r.NamaDesa, &r.NamaKec, &r.JmlSLS, &r.JmlDone)
		if r.JmlSLS > 0 {
			r.PctDone = math.Min(float64(r.JmlDone)*100/float64(r.JmlSLS), 100)
		}
		list = append(list, r)
	}
	return list, pageInfo
}

var adminListingKecSortCols = map[string]string{
	"nama_kec": "s.nama_kec",
	"jml_sls":  "COUNT(DISTINCT s.id)",
	"jml_done": "SUM(COALESCE(ls.done_listing,0))",
	"pct_done": "(CASE WHEN COUNT(DISTINCT s.id)=0 THEN 0 ELSE SUM(COALESCE(ls.done_listing,0))/COUNT(DISTINCT s.id) END)",
}

func queryAdminListingByKec(page int, q, sort, dir string) ([]ListingKecRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := "&level=kec"
	if q != "" {
		extra = "&q=" + q + "&level=kec"
	}
	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT s.nama_kec) FROM sls s WHERE s.nama_kec LIKE ?`, like).Scan(&total)

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, adminListingKecSortCols, "s.kode_kec")
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.nama_kec,
		       COUNT(DISTINCT s.id),
		       SUM(COALESCE(ls.done_listing,0))
		FROM sls s
		LEFT JOIN listing_status ls ON ls.sls_id = s.id
		WHERE s.nama_kec LIKE ?
		GROUP BY s.nama_kec, s.kode_kec
		`+orderBy+`
		LIMIT ? OFFSET ?`, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/listing", "admin-listing-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []ListingKecRow
	for rows.Next() {
		var r ListingKecRow
		rows.Scan(&r.NamaKec, &r.JmlSLS, &r.JmlDone)
		if r.JmlSLS > 0 {
			r.PctDone = math.Min(float64(r.JmlDone)*100/float64(r.JmlSLS), 100)
		}
		list = append(list, r)
	}
	return list, pageInfo
}

func AdminTableListing(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	level := c.QueryParam("level")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")

	switch level {
	case "desa":
		list, pageInfo := queryAdminListingByDesa(page, q, sort, dir)
		return c.Render(http.StatusOK, "admin_listing_desa_table.html", map[string]interface{}{
			"DesaList": list, "DesaPage": pageInfo,
		})
	case "kec":
		list, pageInfo := queryAdminListingByKec(page, q, sort, dir)
		return c.Render(http.StatusOK, "admin_listing_kec_table.html", map[string]interface{}{
			"KecList": list, "KecPage": pageInfo,
		})
	default:
		list, pageInfo := queryAdminListing(page, q, sort, dir)
		return c.Render(http.StatusOK, "admin_listing_sls_table.html", map[string]interface{}{
			"SLSList": list, "SLSPage": pageInfo, "Q": q,
		})
	}
}

func DownloadListing(c echo.Context) error {
	q := c.QueryParam("q")
	level := c.QueryParam("level")
	like := "%" + q + "%"

	suffix := level
	if suffix == "" {
		suffix = "sls"
	}
	fname := fmt.Sprintf("monitoring_listing_%s_%s.xlsx", suffix, time.Now().In(wita).Format("20060102"))

	switch level {
	case "kec":
		rows, err := db.DB.Query(`
			SELECT s.nama_kec, COUNT(DISTINCT s.id), SUM(COALESCE(ls.done_listing,0))
			FROM sls s
			LEFT JOIN listing_status ls ON ls.sls_id = s.id
			WHERE s.nama_kec LIKE ?
			GROUP BY s.nama_kec, s.kode_kec ORDER BY s.kode_kec`, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct {
			kec          string
			jml, jmlDone int
		}
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.kec, &r.jml, &r.jmlDone)
			data = append(data, r)
		}
		headers := []string{"Kecamatan", "Jml SLS", "Selesai Listing", "% Selesai"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				pct := 0.0
				if r.jml > 0 {
					pct = math.Min(float64(r.jmlDone)*100/float64(r.jml), 100)
				}
				f.SetCellValue(sheet, cell(1, n), r.kec)
				f.SetCellValue(sheet, cell(2, n), r.jml)
				f.SetCellValue(sheet, cell(3, n), r.jmlDone)
				f.SetCellValue(sheet, cell(4, n), roundPct(pct))
			}
		})

	case "desa":
		rows, err := db.DB.Query(`
			SELECT s.nama_desa, s.nama_kec, COUNT(DISTINCT s.id), SUM(COALESCE(ls.done_listing,0))
			FROM sls s
			LEFT JOIN listing_status ls ON ls.sls_id = s.id
			WHERE s.nama_desa LIKE ? OR s.nama_kec LIKE ?
			GROUP BY s.nama_desa, s.nama_kec, s.kode_desa, s.kode_kec
			ORDER BY s.kode_kec, s.kode_desa`, like, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct {
			desa, kec    string
			jml, jmlDone int
		}
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.desa, &r.kec, &r.jml, &r.jmlDone)
			data = append(data, r)
		}
		headers := []string{"Desa", "Kecamatan", "Jml SLS", "Selesai Listing", "% Selesai"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				pct := 0.0
				if r.jml > 0 {
					pct = math.Min(float64(r.jmlDone)*100/float64(r.jml), 100)
				}
				f.SetCellValue(sheet, cell(1, n), r.desa)
				f.SetCellValue(sheet, cell(2, n), r.kec)
				f.SetCellValue(sheet, cell(3, n), r.jml)
				f.SetCellValue(sheet, cell(4, n), r.jmlDone)
				f.SetCellValue(sheet, cell(5, n), roundPct(pct))
			}
		})

	default:
		rows, err := db.DB.Query(`
			SELECT s.kode_sls, s.nama_sls, ppl.name, pml.name,
			       COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
			       COALESCE(ls.done_listing,0), COALESCE(ls.done_tarik_sample,0)
			FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id
			LEFT JOIN listing_status ls ON ls.sls_id = s.id
			WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
			  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
			ORDER BY s.kode_kec, s.kode_desa, s.kode_sls`, like, like, like, like, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		var list []ListingSLSRow
		for rows.Next() {
			var r ListingSLSRow
			rows.Scan(&r.KodeSLS, &r.NamaSLS, &r.NamaPPL, &r.NamaPML,
				&r.NamaKec, &r.NamaDesa, &r.DoneListing, &r.DoneTarikSample)
			list = append(list, r)
		}
		headers := []string{"Kode SLS", "Nama SLS", "PPL", "PML", "Kecamatan", "Desa", "Selesai Listing", "Selesai Tarik Sample"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range list {
				n := i + 2
				f.SetCellValue(sheet, cell(1, n), r.KodeSLS)
				f.SetCellValue(sheet, cell(2, n), r.NamaSLS)
				f.SetCellValue(sheet, cell(3, n), r.NamaPPL)
				f.SetCellValue(sheet, cell(4, n), r.NamaPML)
				f.SetCellValue(sheet, cell(5, n), r.NamaKec)
				f.SetCellValue(sheet, cell(6, n), r.NamaDesa)
				f.SetCellValue(sheet, cell(7, n), boolYaTidak(r.DoneListing))
				f.SetCellValue(sheet, cell(8, n), boolYaTidak(r.DoneTarikSample))
			}
		})
	}
}

func boolYaTidak(b bool) string {
	if b {
		return "Ya"
	}
	return "Belum"
}
