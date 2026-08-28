package handlers

import (
	"database/sql"
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

// WideAgregatGroupRow adalah satu baris rekap per-Desa atau per-Kecamatan
// (level "desa"/"kec") di adminWideAgregatGroupTable — sama seperti
// WideAgregatRow tapi digabung (SUM) per SLS dalam grup itu, mirip DesaRow/
// KecRow di tab Progres Semua SLS.
type WideAgregatGroupRow struct {
	NamaKec  string
	NamaDesa string // kosong kalau level "kec"
	JmlSLS   int
	Values   map[string]int
	Total    int
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
// prelistKode opsional: kalau diisi, indikator dgn kode itu dipindah ke depan
// (kolom Prelist Awal harus paling kiri) — perlu krn urutan numerik kode
// tidak selalu menaruh Prelist di depan (mis. kode 12341 utk Usaha
// Keluarga, yg sebenarnya konsep "awal" tapi angkanya paling besar).
func queryAgregatIndikatorList(table string, kodeFilter []string, prelistKode string) []KBLIIndikator {
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
	if prelistKode != "" {
		for i, k := range list {
			if k.Kode == prelistKode && i > 0 {
				list = append(list[:i], list[i+1:]...)
				list = append([]KBLIIndikator{k}, list...)
				break
			}
		}
	}
	return list
}

// adminWideAgregatTable adalah handler generik: tabel lebar per SLS, 1 kolom
// per indikator, dari sebuah tabel agregat "kode_indikator -> total_value".
// Dipakai oleh AdminKBLITable & sub-tabel Rekap Keberadaan (Usaha BKU / Usaha
// Keluarga) — sengaja generik supaya kalau nanti ada dataset agregat baru
// dari dashboard-se2026 (skema sama persis), tinggal tambah satu wrapper
// tipis tanpa duplikasi query. kodeFilter opsional (lihat queryAgregatIndikatorList).
// prelistKode/baruKode/ditemukanKode opsional: kalau diisi, template bisa
// tampilkan badge persentase (nilai/Prelist Awal) di tiap kolom kecuali
// kolom Prelist itu sendiri, plus kolom turunan "Total Ditemukan + Baru"
// (lihat admin_keberadaan_*_table.html). Filter kec/pml_id/ppl_id (query
// param, opsional) dipakai oleh filter bertingkat Rekap Keberadaan — dibaca
// langsung dari request, aman dipakai bareng KBLI juga krn UI KBLI tidak
// pernah mengirim param ini.
// indikatorListOverride/mergeMap opsional, dipakai khusus tab "Usaha
// Keseluruhan" (gabungan BKU + Usaha Keluarga): kalau diisi, daftar kolom
// TIDAK di-derive otomatis dari kode_indikator di DB (queryAgregatIndikatorList),
// tapi pakai daftar kategori gabungan yang sudah ditentukan (indikatorListOverride),
// dan tiap kode_indikator mentah di-merge ke kode kategori gabungannya
// (mergeMap: kode mentah -> kode kategori) sebelum dijumlah ke r.Values —
// jadi mis. "Ditemukan" BKU (10264) + "Ditemukan" Usaha Keluarga (10691)
// ketemu di satu kolom "gab_ditemukan". Kalau nil, perilaku persis seperti
// sebelumnya (1 kode_indikator = 1 kolom).
func adminWideAgregatTable(c echo.Context, table, tmplName, wrapID, routePath string, kodeFilter []string, prelistKode, baruKode, ditemukanKode string, indikatorListOverride []KBLIIndikator, mergeMap map[string]string) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	kec := c.QueryParam("kec")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	sortKey := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	like := "%" + q + "%"

	where := ` WHERE (s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ? OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?)`
	args := []interface{}{like, like, like, like, like}
	if kec != "" {
		where += ` AND s.nama_kec = ?`
		args = append(args, kec)
	}
	if pmlID > 0 {
		where += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}

	var total int
	db.DB.QueryRow(`
		SELECT COUNT(*) FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where, args...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	if kec != "" {
		extra += "&kec=" + kec
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}
	if pplID > 0 {
		extra += fmt.Sprintf("&ppl_id=%d", pplID)
	}
	orderBy, sortCol, sortDir := models.BuildOrderBy(sortKey, dir, wideAgregatSortCols, "s.kode_kec, s.kode_desa, s.kode_sls")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, routePath, wrapID, extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	indikatorList := indikatorListOverride
	if indikatorList == nil {
		indikatorList = queryAgregatIndikatorList(table, kodeFilter, prelistKode)
	}

	// Totals per indikator dihitung dari SEMUA baris yang cocok filter (bukan
	// cuma baris di halaman ini) supaya baris "Total" di bawah tabel ikut
	// filter yang aktif (q/kec/pml_id/ppl_id), bukan grand total statis.
	totals := map[string]int{}
	totQuery := fmt.Sprintf(`
		SELECT t.kode_indikator, COALESCE(SUM(t.total_value),0)
		FROM %s t
		JOIN sls s ON s.id = t.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
	`, table) + where
	totArgs := append([]interface{}{}, args...)
	if len(kodeFilter) > 0 {
		kPlaceholders := make([]string, len(kodeFilter))
		for i, k := range kodeFilter {
			kPlaceholders[i] = "?"
			totArgs = append(totArgs, k)
		}
		totQuery += ` AND t.kode_indikator IN (` + strings.Join(kPlaceholders, ",") + `)`
	}
	totQuery += ` GROUP BY t.kode_indikator`
	if totRows, err := db.DB.Query(totQuery, totArgs...); err == nil {
		defer totRows.Close()
		for totRows.Next() {
			var kode string
			var val int
			totRows.Scan(&kode, &val)
			target := kode
			if mergeMap != nil {
				if mapped, ok := mergeMap[kode]; ok {
					target = mapped
				}
			}
			totals[target] += val
		}
	}

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT s.id, s.kode_sls, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`,
		queryArgs...)
	if err != nil {
		return c.Render(http.StatusOK, tmplName, map[string]interface{}{
			"Rows": nil, "Page": pageInfo, "Indikators": indikatorList, "Q": q,
			"PrelistKode": prelistKode, "BaruKode": baruKode, "DitemukanKode": ditemukanKode, "Totals": totals,
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
					target := kode
					if mergeMap != nil {
						if mapped, ok := mergeMap[kode]; ok {
							target = mapped
						}
					}
					r.Values[target] += val
					r.Total += val
				}
			}
		}
	}

	return c.Render(http.StatusOK, tmplName, map[string]interface{}{
		"Rows": list, "Page": pageInfo, "Indikators": indikatorList, "Q": q,
		"PrelistKode": prelistKode, "BaruKode": baruKode, "DitemukanKode": ditemukanKode, "Totals": totals,
	})
}

// adminWideAgregatGroupTable adalah versi rekap dari adminWideAgregatTable:
// bukan 1 baris per SLS, tapi digabung (SUM) per Desa atau per Kecamatan —
// sama seperti tab "Progres Semua SLS" yang punya pilihan Per SLS/Desa/Kec
// (lihat queryAdminSLSByDesa/queryAdminSLSByKec di admin.go). Dipakai kalau
// query param level=desa|kec dikirim ke route Rekap Keberadaan yang sama.
// Filter q/kec/pml_id/ppl_id sama persis dengan level SLS, cuma baris hasil
// & query indikatornya yang di-agregasi per grup.
func adminWideAgregatGroupTable(c echo.Context, table, routePath, wrapID string, kodeFilter []string, prelistKode, baruKode, ditemukanKode, tableDesc, level string, indikatorListOverride []KBLIIndikator, mergeMap map[string]string) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	kec := c.QueryParam("kec")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where := ` WHERE (s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ? OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?)`
	args := []interface{}{like, like, like, like, like}
	if kec != "" {
		where += ` AND s.nama_kec = ?`
		args = append(args, kec)
	}
	if pmlID > 0 {
		where += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}

	extra := "&level=" + level
	if q != "" {
		extra += "&q=" + q
	}
	if kec != "" {
		extra += "&kec=" + kec
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}
	if pplID > 0 {
		extra += fmt.Sprintf("&ppl_id=%d", pplID)
	}

	indikatorList := indikatorListOverride
	if indikatorList == nil {
		indikatorList = queryAgregatIndikatorList(table, kodeFilter, prelistKode)
	}

	// Totals per indikator dari SEMUA baris yang cocok filter (bukan cuma
	// grup di halaman ini) — sama seperti level SLS, biar footer "Total"
	// tetap ikut filter aktif, terlepas dari level grouping-nya.
	totals := map[string]int{}
	totQuery := fmt.Sprintf(`
		SELECT t.kode_indikator, COALESCE(SUM(t.total_value),0)
		FROM %s t
		JOIN sls s ON s.id = t.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
	`, table) + where
	totArgs := append([]interface{}{}, args...)
	if len(kodeFilter) > 0 {
		kPlaceholders := make([]string, len(kodeFilter))
		for i, k := range kodeFilter {
			kPlaceholders[i] = "?"
			totArgs = append(totArgs, k)
		}
		totQuery += ` AND t.kode_indikator IN (` + strings.Join(kPlaceholders, ",") + `)`
	}
	totQuery += ` GROUP BY t.kode_indikator`
	if totRows, err := db.DB.Query(totQuery, totArgs...); err == nil {
		defer totRows.Close()
		for totRows.Next() {
			var kode string
			var val int
			totRows.Scan(&kode, &val)
			target := kode
			if mergeMap != nil {
				if mapped, ok := mergeMap[kode]; ok {
					target = mapped
				}
			}
			totals[target] += val
		}
	}

	offset := (page - 1) * models.PerPage
	var totalGroups int
	var groupRows *sql.Rows
	var err error

	if level == "kec" {
		db.DB.QueryRow(`
			SELECT COUNT(DISTINCT s.nama_kec) FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id`+where, args...).Scan(&totalGroups)
		queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
		groupRows, err = db.DB.Query(`
			SELECT s.nama_kec, COUNT(DISTINCT s.id)
			FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id
			`+where+`
			GROUP BY s.nama_kec, s.kode_kec
			ORDER BY s.kode_kec
			LIMIT ? OFFSET ?`, queryArgs...)
	} else {
		db.DB.QueryRow(`
			SELECT COUNT(DISTINCT CONCAT(s.nama_desa,'|',s.nama_kec)) FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id`+where, args...).Scan(&totalGroups)
		queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
		groupRows, err = db.DB.Query(`
			SELECT s.nama_desa, s.nama_kec, COUNT(DISTINCT s.id)
			FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id
			`+where+`
			GROUP BY s.nama_desa, s.nama_kec, s.kode_desa, s.kode_kec
			ORDER BY s.kode_kec, s.kode_desa
			LIMIT ? OFFSET ?`, queryArgs...)
	}

	pageInfo := models.NewPageInfo(page, totalGroups, routePath, wrapID, extra)
	pageInfo.FilterExtra = extra

	renderData := func(list []*WideAgregatGroupRow) map[string]interface{} {
		return map[string]interface{}{
			"Rows": list, "Page": pageInfo, "Indikators": indikatorList, "Totals": totals,
			"PrelistKode": prelistKode, "BaruKode": baruKode, "DitemukanKode": ditemukanKode,
			"GroupLevel": level, "TableDesc": tableDesc,
		}
	}

	if err != nil {
		return c.Render(http.StatusOK, "admin_keberadaan_group_table.html", renderData(nil))
	}
	defer groupRows.Close()

	type groupKey struct{ desa, kec string }
	byKey := map[groupKey]*WideAgregatGroupRow{}
	var keys []groupKey
	var list []*WideAgregatGroupRow
	for groupRows.Next() {
		r := &WideAgregatGroupRow{Values: map[string]int{}}
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
		valArgs := append([]interface{}{}, args...)
		var valQuery string
		if level == "kec" {
			placeholders := make([]string, len(keys))
			for i, k := range keys {
				placeholders[i] = "?"
				valArgs = append(valArgs, k.kec)
			}
			valQuery = fmt.Sprintf(`
				SELECT s.nama_kec, t.kode_indikator, COALESCE(SUM(t.total_value),0)
				FROM %s t
				JOIN sls s ON s.id = t.sls_id
				JOIN users ppl ON ppl.id = s.ppl_id
				JOIN users pml ON pml.id = s.pml_id
			`, table) + where + ` AND s.nama_kec IN (` + strings.Join(placeholders, ",") + `)`
		} else {
			placeholders := make([]string, len(keys))
			for i, k := range keys {
				placeholders[i] = "(?,?)"
				valArgs = append(valArgs, k.desa, k.kec)
			}
			valQuery = fmt.Sprintf(`
				SELECT s.nama_desa, s.nama_kec, t.kode_indikator, COALESCE(SUM(t.total_value),0)
				FROM %s t
				JOIN sls s ON s.id = t.sls_id
				JOIN users ppl ON ppl.id = s.ppl_id
				JOIN users pml ON pml.id = s.pml_id
			`, table) + where + ` AND (s.nama_desa, s.nama_kec) IN (` + strings.Join(placeholders, ",") + `)`
		}
		if len(kodeFilter) > 0 {
			kPlaceholders := make([]string, len(kodeFilter))
			for i, k := range kodeFilter {
				kPlaceholders[i] = "?"
				valArgs = append(valArgs, k)
			}
			valQuery += ` AND t.kode_indikator IN (` + strings.Join(kPlaceholders, ",") + `)`
		}
		if level == "kec" {
			valQuery += ` GROUP BY s.nama_kec, t.kode_indikator`
		} else {
			valQuery += ` GROUP BY s.nama_desa, s.nama_kec, t.kode_indikator`
		}
		if valRows, err := db.DB.Query(valQuery, valArgs...); err == nil {
			defer valRows.Close()
			for valRows.Next() {
				var desa, kecName, kode string
				var val int
				if level == "kec" {
					valRows.Scan(&kecName, &kode, &val)
				} else {
					valRows.Scan(&desa, &kecName, &kode, &val)
				}
				if r, ok := byKey[groupKey{desa, kecName}]; ok {
					target := kode
					if mergeMap != nil {
						if mapped, ok := mergeMap[kode]; ok {
							target = mapped
						}
					}
					r.Values[target] += val
					r.Total += val
				}
			}
		}
	}

	return c.Render(http.StatusOK, "admin_keberadaan_group_table.html", renderData(list))
}

// AdminKBLITable — GET /admin/table/kbli
// Tabel lebar: 1 baris per SLS, 1 kolom per kategori KBLI (jumlah usaha).
func AdminKBLITable(c echo.Context) error {
	return adminWideAgregatTable(c, "kbli_usaha", "admin_kbli_table.html", "admin-kbli-wrap", "/admin/table/kbli", nil, "", "", "", nil, nil)
}

// Kode indikator coverage_usaha_keluarga per kategori (lihat juga kode
// individual di admin.go yang dipakai utk hitung % coverage, dan
// COVERAGE_INDIKATOR di scraper/sync_kbli.py yang menariknya dari Dashboard
// SE2026). Rekap Keberadaan dipecah jadi 3 tab terpisah (bukan 1 tabel
// raksasa semua kategori sekaligus): Usaha BKU, Usaha Keluarga, dan Keluarga
// — ketiganya tetap tampilkan breakdown lengkap per status, cuma dipisah
// tabelnya per kategori.
// "2" sengaja tidak dimasukkan — itu "Jumlah Prelist Awal" gabungan usaha+
// keluarga, bukan usaha BKU saja. Kolom Prelist yang benar di sini pakai kode
// sintetis "90002" (SUM 108+109+110 = UB+UM+UMK), lihat kodeCovUsahaPrelist.
var kodeCovBKUAll = []string{"90002", "10247", "10264", "10265", "10266", "10268"}
var kodeCovUsahaKeluargaAll = []string{"12341", "10691", "10693", "10694", "10695", "10696"}

// Keluarga: prelist, ditemukan, meninggal, tidak eligible, tidak dapat
// ditemui s/d akhir pendataan, tidak ditemukan, baru, menolak didata,
// bersedia didata, keluarga khusus. Sengaja tidak termasuk kode 24-30/112
// (Anggota Keluarga — satuannya per orang, bukan per keluarga).
var kodeCovKeluargaAll = []string{"14", "15", "16", "17", "18", "19", "20", "21", "22", "59"}

// Tab "Usaha Keseluruhan" = Usaha BKU (mandiri) + Usaha dalam Keluarga
// digabung per status keberadaan yang sama — dua dataset itu pakai
// kode_indikator BEDA utk konsep yang sama (mis. "Ditemukan" BKU=10264 vs
// Usaha Keluarga=10691), jadi digabung lewat mergeMap ke satu kode kategori
// sintetis ("gab_..." — sengaja non-numerik, tidak mungkin tabrakan dgn
// kode_indikator asli yang selalu angka). Cuma 6 status yang ada padanan di
// kedua dataset (Prelist/Ditemukan/Tutup/Ganda/TidakDitemukan/Baru) — status
// khusus Keluarga (Meninggal, Tidak Eligible, dst) tidak relevan di sini.
var kodeCovKeseluruhanMerge = map[string]string{
	kodeCovUsahaPrelist:      "gab_prelist",
	kodeCovUsahaKelPrelist:   "gab_prelist",
	kodeCovUsahaDitemukan:    "gab_ditemukan",
	kodeCovUsahaKelDitemukan: "gab_ditemukan",
	"10265":                  "gab_tutup", // Usaha BKU Ditutup
	"10693":                  "gab_tutup", // Usaha Keluarga Tutup
	"10266":                  "gab_ganda", // Usaha BKU Ganda
	"10694":                  "gab_ganda", // Usaha Keluarga Ganda
	"10247":                  "gab_tidak_ditemukan", // Usaha BKU Tidak Ditemukan
	"10695":                  "gab_tidak_ditemukan", // Usaha Keluarga Tidak Ditemukan
	kodeCovUsahaBaru:         "gab_baru",
	kodeCovUsahaKelBaru:      "gab_baru",
}
var kodeCovKeseluruhanAll = []string{
	kodeCovUsahaPrelist, kodeCovUsahaKelPrelist,
	kodeCovUsahaDitemukan, kodeCovUsahaKelDitemukan,
	"10265", "10693",
	"10266", "10694",
	"10247", "10695",
	kodeCovUsahaBaru, kodeCovUsahaKelBaru,
}
var kodeCovKeseluruhanIndikators = []KBLIIndikator{
	{Kode: "gab_prelist", Nama: "Prelist Awal"},
	{Kode: "gab_ditemukan", Nama: "Ditemukan"},
	{Kode: "gab_tutup", Nama: "Tutup"},
	{Kode: "gab_ganda", Nama: "Ganda"},
	{Kode: "gab_tidak_ditemukan", Nama: "Tidak Ditemukan"},
	{Kode: "gab_baru", Nama: "Baru"},
}

// AdminKeberadaanBKUTable — GET /admin/table/keberadaan-bku
// level=desa|kec (opsional) merekap per Desa/Kecamatan, mirip pilihan
// "Per SLS/Desa/Kecamatan" di tab Progres Semua SLS.
func AdminKeberadaanBKUTable(c echo.Context) error {
	if lvl := c.QueryParam("level"); lvl == "desa" || lvl == "kec" {
		return adminWideAgregatGroupTable(c, "coverage_usaha_keluarga", "/admin/table/keberadaan-bku", "admin-keberadaan-rekap-wrap", kodeCovBKUAll, kodeCovUsahaPrelist, kodeCovUsahaBaru, kodeCovUsahaDitemukan, "Status keberadaan Usaha BKU (mandiri)", lvl, nil, nil)
	}
	return adminWideAgregatTable(c, "coverage_usaha_keluarga", "admin_keberadaan_bku_table.html", "admin-keberadaan-rekap-wrap", "/admin/table/keberadaan-bku", kodeCovBKUAll, kodeCovUsahaPrelist, kodeCovUsahaBaru, kodeCovUsahaDitemukan, nil, nil)
}

// AdminKeberadaanUsahaKeluargaTable — GET /admin/table/keberadaan-usaha-keluarga
func AdminKeberadaanUsahaKeluargaTable(c echo.Context) error {
	if lvl := c.QueryParam("level"); lvl == "desa" || lvl == "kec" {
		return adminWideAgregatGroupTable(c, "coverage_usaha_keluarga", "/admin/table/keberadaan-usaha-keluarga", "admin-keberadaan-rekap-wrap", kodeCovUsahaKeluargaAll, kodeCovUsahaKelPrelist, kodeCovUsahaKelBaru, kodeCovUsahaKelDitemukan, "Status keberadaan Usaha dalam Keluarga", lvl, nil, nil)
	}
	return adminWideAgregatTable(c, "coverage_usaha_keluarga", "admin_keberadaan_usahakeluarga_table.html", "admin-keberadaan-rekap-wrap", "/admin/table/keberadaan-usaha-keluarga", kodeCovUsahaKeluargaAll, kodeCovUsahaKelPrelist, kodeCovUsahaKelBaru, kodeCovUsahaKelDitemukan, nil, nil)
}

// AdminKeberadaanKeluargaTable — GET /admin/table/keberadaan-keluarga
func AdminKeberadaanKeluargaTable(c echo.Context) error {
	if lvl := c.QueryParam("level"); lvl == "desa" || lvl == "kec" {
		return adminWideAgregatGroupTable(c, "coverage_usaha_keluarga", "/admin/table/keberadaan-keluarga", "admin-keberadaan-rekap-wrap", kodeCovKeluargaAll, kodeCovKeluargaPrelist, kodeCovKeluargaBaru, kodeCovKeluargaDitemukan, "Status keberadaan Keluarga", lvl, nil, nil)
	}
	return adminWideAgregatTable(c, "coverage_usaha_keluarga", "admin_keberadaan_keluarga_table.html", "admin-keberadaan-rekap-wrap", "/admin/table/keberadaan-keluarga", kodeCovKeluargaAll, kodeCovKeluargaPrelist, kodeCovKeluargaBaru, kodeCovKeluargaDitemukan, nil, nil)
}

// AdminKeberadaanUsahaKeseluruhanTable — GET /admin/table/keberadaan-usaha-keseluruhan
// Rekap gabungan Usaha BKU + Usaha dalam Keluarga per status keberadaan
// (lihat kodeCovKeseluruhanMerge).
func AdminKeberadaanUsahaKeseluruhanTable(c echo.Context) error {
	if lvl := c.QueryParam("level"); lvl == "desa" || lvl == "kec" {
		return adminWideAgregatGroupTable(c, "coverage_usaha_keluarga", "/admin/table/keberadaan-usaha-keseluruhan", "admin-keberadaan-rekap-wrap", kodeCovKeseluruhanAll, "gab_prelist", "gab_baru", "gab_ditemukan", "Status keberadaan Usaha Keseluruhan (BKU + Usaha Keluarga)", lvl, kodeCovKeseluruhanIndikators, kodeCovKeseluruhanMerge)
	}
	return adminWideAgregatTable(c, "coverage_usaha_keluarga", "admin_keberadaan_usahakeseluruhan_table.html", "admin-keberadaan-rekap-wrap", "/admin/table/keberadaan-usaha-keseluruhan", kodeCovKeseluruhanAll, "gab_prelist", "gab_baru", "gab_ditemukan", kodeCovKeseluruhanIndikators, kodeCovKeseluruhanMerge)
}

// OptionsPMLByKec — GET /admin/options/pml-by-kec?kec=X
// Dipakai filter bertingkat Kecamatan → PML → PPL di Rekap Keberadaan: begitu
// Kecamatan dipilih, dropdown PML ikut mengecil ke PML yang punya SLS di
// kecamatan itu saja. kec kosong = semua PML (sama seperti queryPMLUsers).
func OptionsPMLByKec(c echo.Context) error {
	kec := c.QueryParam("kec")
	query := `SELECT u.id, u.name FROM users u JOIN sls s ON s.pml_id=u.id WHERE u.role='pml'`
	var args []interface{}
	if kec != "" {
		query += ` AND s.nama_kec = ?`
		args = append(args, kec)
	}
	query += ` GROUP BY u.id, u.name ORDER BY u.name`
	rows, err := db.DB.Query(query, args...)
	if err != nil {
		return c.JSON(http.StatusOK, []PMLUser{})
	}
	defer rows.Close()
	list := []PMLUser{}
	for rows.Next() {
		var p PMLUser
		rows.Scan(&p.ID, &p.Name)
		list = append(list, p)
	}
	return c.JSON(http.StatusOK, list)
}

// OptionsPPLByFilter — GET /admin/options/ppl-by-filter?kec=X&pml_id=Y
// Sama seperti OptionsPMLByKec tapi utk dropdown PPL — ikut mengecil kalau
// Kecamatan dan/atau PML sudah dipilih.
func OptionsPPLByFilter(c echo.Context) error {
	kec := c.QueryParam("kec")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	query := `SELECT u.id, u.name FROM users u JOIN sls s ON s.ppl_id=u.id WHERE u.role='ppl'`
	var args []interface{}
	if kec != "" {
		query += ` AND s.nama_kec = ?`
		args = append(args, kec)
	}
	if pmlID > 0 {
		query += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	query += ` GROUP BY u.id, u.name ORDER BY u.name`
	rows, err := db.DB.Query(query, args...)
	if err != nil {
		return c.JSON(http.StatusOK, []PPLUser{})
	}
	defer rows.Close()
	list := []PPLUser{}
	for rows.Next() {
		var p PPLUser
		rows.Scan(&p.ID, &p.Name)
		list = append(list, p)
	}
	return c.JSON(http.StatusOK, list)
}
