package handlers

import (
	"net/http"
	"sort"
	"strconv"
	"strings"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

// KBLIIndikator adalah satu kategori KBLI (kode + label), diambil dinamis dari
// data yang sudah disinkron — bukan di-hardcode, supaya tidak salah kalau BPS
// ubah daftar/urutan kategori indikator.
type KBLIIndikator struct {
	Kode string
	Nama string
}

type KBLIRow struct {
	ID       int
	KodeSLS  string
	NamaSLS  string
	NamaKec  string
	NamaDesa string
	NamaPPL  string
	NamaPML  string
	Values   map[string]int // kode_indikator -> jumlah usaha
	Total    int            // jumlah usaha semua kategori utk SLS ini
}

var kbliSortCols = map[string]string{
	"kode_sls": "s.kode_sls",
	"nama_sls": "s.nama_sls",
	"ppl":      "ppl.name",
	"pml":      "pml.name",
	"lokasi":   "s.nama_kec, s.nama_desa",
}

// queryKBLIIndikatorList mengambil daftar kategori KBLI yang sudah ada datanya,
// diurutkan numerik berdasarkan kode_indikator (mis. 60, 63, 66, ..., 10254).
func queryKBLIIndikatorList() []KBLIIndikator {
	rows, err := db.DB.Query(`SELECT DISTINCT kode_indikator, nama_indikator FROM kbli_usaha`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []KBLIIndikator
	for rows.Next() {
		var k KBLIIndikator
		rows.Scan(&k.Kode, &k.Nama)
		list = append(list, k)
	}
	sort.Slice(list, func(i, j int) bool {
		ni, _ := strconv.Atoi(list[i].Kode)
		nj, _ := strconv.Atoi(list[j].Kode)
		return ni < nj
	})
	return list
}

// AdminKBLITable — GET /admin/table/kbli
// Tabel lebar: 1 baris per SLS, 1 kolom per kategori KBLI (jumlah usaha).
func AdminKBLITable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	sortKey := c.QueryParam("sort")
	dir := c.QueryParam("dir")
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
	orderBy, sortCol, sortDir := models.BuildOrderBy(sortKey, dir, kbliSortCols, "s.kode_kec, s.kode_desa, s.kode_sls")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/kbli", "admin-kbli-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	indikatorList := queryKBLIIndikatorList()

	rows, err := db.DB.Query(`
		SELECT s.id, s.kode_sls, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
		  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
		`+orderBy+`
		LIMIT ? OFFSET ?`,
		like, like, like, like, like, models.PerPage, offset)
	if err != nil {
		return c.Render(http.StatusOK, "admin_kbli_table.html", map[string]interface{}{
			"Rows": nil, "KBLIPage": pageInfo, "Indikators": indikatorList, "Q": q,
		})
	}
	defer rows.Close()

	var slsIDs []int
	bySLS := map[int]*KBLIRow{}
	var list []*KBLIRow
	for rows.Next() {
		var r KBLIRow
		rows.Scan(&r.ID, &r.KodeSLS, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML)
		r.Values = map[string]int{}
		list = append(list, &r)
		bySLS[r.ID] = &r
		slsIDs = append(slsIDs, r.ID)
	}

	if len(slsIDs) > 0 {
		placeholders := make([]string, len(slsIDs))
		args := make([]interface{}, len(slsIDs))
		for i, id := range slsIDs {
			placeholders[i] = "?"
			args[i] = id
		}
		valRows, err := db.DB.Query(`
			SELECT sls_id, kode_indikator, COALESCE(total_value,0)
			FROM kbli_usaha
			WHERE sls_id IN (`+strings.Join(placeholders, ",")+`)`, args...)
		if err == nil {
			defer valRows.Close()
			for valRows.Next() {
				var slsID int
				var kode string
				var val int
				valRows.Scan(&slsID, &kode, &val)
				if r, ok := bySLS[slsID]; ok {
					r.Values[kode] = val
					r.Total += val
				}
			}
		}
	}

	return c.Render(http.StatusOK, "admin_kbli_table.html", map[string]interface{}{
		"Rows": list, "KBLIPage": pageInfo, "Indikators": indikatorList, "Q": q,
	})
}
