package handlers

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

// KBLIIndikator adalah satu kategori/indikator (kode + label), diambil dinamis
// dari data yang sudah disinkron — bukan di-hardcode, supaya tidak salah kalau
// BPS ubah daftar/urutan kategori indikator.
type KBLIIndikator struct {
	Kode string
	Nama string
}

// WideAgregatRow adalah satu baris SLS di tabel lebar per-indikator, dipakai
// bareng oleh tab "KBLI per SLS" (tabel kbli_usaha) dan "Rekap Keberadaan"
// (tabel coverage_usaha_keluarga) — keduanya skema identik: sls_id + kode_indikator + total_value.
type WideAgregatRow struct {
	ID       int
	KodeSLS  string
	NamaSLS  string
	NamaKec  string
	NamaDesa string
	NamaPPL  string
	NamaPML  string
	Values   map[string]int // kode_indikator -> jumlah
	Total    int            // jumlah semua kategori/indikator utk SLS ini
}

var wideAgregatSortCols = map[string]string{
	"kode_sls": "s.kode_sls",
	"nama_sls": "s.nama_sls",
	"ppl":      "ppl.name",
	"pml":      "pml.name",
	"lokasi":   "s.nama_kec, s.nama_desa",
}

// queryAgregatIndikatorList mengambil daftar indikator yang sudah ada datanya
// di tabel agregat tertentu, diurutkan numerik berdasarkan kode_indikator.
// table adalah nama tabel yang di-hardcode oleh caller (bukan input pengguna),
// jadi aman diselipkan langsung ke query. kodeFilter opsional: kalau diisi,
// cuma indikator dgn kode di daftar itu yang diambil (dipakai utk memecah
// coverage_usaha_keluarga jadi sub-tabel Usaha BKU / Usaha Keluarga).
func queryAgregatIndikatorList(table string, kodeFilter []string) []KBLIIndikator {
	query := fmt.Sprintf(`SELECT DISTINCT kode_indikator, nama_indikator FROM %s`, table)
	var args []interface{}
	if len(kodeFilter) > 0 {
		placeholders := make([]string, len(kodeFilter))
		for i, k := range kodeFilter {
			placeholders[i] = "?"
			args = append(args, k)
		}
		query += ` WHERE kode_indikator IN (` + strings.Join(placeholders, ",") + `)`
	}
	rows, err := db.DB.Query(query, args...)
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

// adminWideAgregatTable adalah handler generik: tabel lebar per SLS, 1 kolom
// per indikator, dari sebuah tabel agregat "kode_indikator -> total_value".
// Dipakai oleh AdminKBLITable & sub-tabel Rekap Keberadaan (Usaha BKU / Usaha
// Keluarga) — sengaja generik supaya kalau nanti ada dataset agregat baru
// dari dashboard-se2026 (skema sama persis), tinggal tambah satu wrapper
// tipis tanpa duplikasi query. kodeFilter opsional (lihat queryAgregatIndikatorList).
func adminWideAgregatTable(c echo.Context, table, tmplName, wrapID, routePath string, kodeFilter []string) error {
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
	orderBy, sortCol, sortDir := models.BuildOrderBy(sortKey, dir, wideAgregatSortCols, "s.kode_kec, s.kode_desa, s.kode_sls")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, routePath, wrapID, extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	indikatorList := queryAgregatIndikatorList(table, kodeFilter)

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
		return c.Render(http.StatusOK, tmplName, map[string]interface{}{
			"Rows": nil, "Page": pageInfo, "Indikators": indikatorList, "Q": q,
		})
	}
	defer rows.Close()

	var slsIDs []int
	bySLS := map[int]*WideAgregatRow{}
	var list []*WideAgregatRow
	for rows.Next() {
		var r WideAgregatRow
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
		valQuery := fmt.Sprintf(`
			SELECT sls_id, kode_indikator, COALESCE(total_value,0)
			FROM %s
			WHERE sls_id IN (%s)`, table, strings.Join(placeholders, ","))
		if len(kodeFilter) > 0 {
			kPlaceholders := make([]string, len(kodeFilter))
			for i, k := range kodeFilter {
				kPlaceholders[i] = "?"
				args = append(args, k)
			}
			valQuery += ` AND kode_indikator IN (` + strings.Join(kPlaceholders, ",") + `)`
		}
		valRows, err := db.DB.Query(valQuery, args...)
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

	return c.Render(http.StatusOK, tmplName, map[string]interface{}{
		"Rows": list, "Page": pageInfo, "Indikators": indikatorList, "Q": q,
	})
}

// AdminKBLITable — GET /admin/table/kbli
// Tabel lebar: 1 baris per SLS, 1 kolom per kategori KBLI (jumlah usaha).
func AdminKBLITable(c echo.Context) error {
	return adminWideAgregatTable(c, "kbli_usaha", "admin_kbli_table.html", "admin-kbli-wrap", "/admin/table/kbli", nil)
}

// Kode indikator coverage_usaha_keluarga per kategori (lihat juga kode
// individual di admin.go yang dipakai utk hitung % coverage, dan
// COVERAGE_INDIKATOR di scraper/sync_kbli.py yang menariknya dari Dashboard
// SE2026). Rekap Keberadaan dipecah jadi 3 tab terpisah (bukan 1 tabel
// raksasa semua kategori sekaligus): Usaha BKU, Usaha Keluarga, dan Keluarga
// — ketiganya tetap tampilkan breakdown lengkap per status, cuma dipisah
// tabelnya per kategori.
var kodeCovBKUAll = []string{"2", "10247", "10264", "10265", "10266", "10268"}
var kodeCovUsahaKeluargaAll = []string{"90001", "10691", "10693", "10694", "10695", "10696"}

// Keluarga: prelist, ditemukan, meninggal, tidak eligible, tidak dapat
// ditemui s/d akhir pendataan, tidak ditemukan, baru, menolak didata,
// bersedia didata, keluarga khusus. Sengaja tidak termasuk kode 24-30/112
// (Anggota Keluarga — satuannya per orang, bukan per keluarga).
var kodeCovKeluargaAll = []string{"14", "15", "16", "17", "18", "19", "20", "21", "22", "59"}

// AdminKeberadaanBKUTable — GET /admin/table/keberadaan-bku
func AdminKeberadaanBKUTable(c echo.Context) error {
	return adminWideAgregatTable(c, "coverage_usaha_keluarga", "admin_keberadaan_bku_table.html", "admin-keberadaan-rekap-wrap", "/admin/table/keberadaan-bku", kodeCovBKUAll)
}

// AdminKeberadaanUsahaKeluargaTable — GET /admin/table/keberadaan-usaha-keluarga
func AdminKeberadaanUsahaKeluargaTable(c echo.Context) error {
	return adminWideAgregatTable(c, "coverage_usaha_keluarga", "admin_keberadaan_usahakeluarga_table.html", "admin-keberadaan-rekap-wrap", "/admin/table/keberadaan-usaha-keluarga", kodeCovUsahaKeluargaAll)
}

// AdminKeberadaanKeluargaTable — GET /admin/table/keberadaan-keluarga
func AdminKeberadaanKeluargaTable(c echo.Context) error {
	return adminWideAgregatTable(c, "coverage_usaha_keluarga", "admin_keberadaan_keluarga_table.html", "admin-keberadaan-rekap-wrap", "/admin/table/keberadaan-keluarga", kodeCovKeluargaAll)
}
