package handlers

import (
	"fmt"
	"net/http"
	"strconv"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	"monitoringse/models"
)

// DuplikatBKURow adalah satu baris di menu "Usaha Keluarga → BKU". Sumbernya
// salah satu dari dua tabel (lihat scraper/sync_usaha.py):
//   - usaha_keluarga_bku_duplikat : usaha keluarga yg hp/email-nya SAMA
//     dengan usaha BKU yg SUDAH ADA (duplikat, perlu dipindahkan/ditutup).
//   - usaha_keluarga_tanpa_bku    : usaha keluarga yg hp/email-nya terisi
//     tapi BELUM ketemu usaha BKU manapun (kandidat diangkat jd BKU baru).
//
// Jenis membedakan asalnya ("duplikat" | "tanpa_bku") — dipakai di sub-tab
// Riwayat yg menggabungkan riwayat dari kedua tabel jadi satu daftar.
type DuplikatBKURow struct {
	ID                   int
	Jenis                string
	NamaSLS              string
	NamaKec              string
	NamaDesa             string
	NamaPPL              string
	NamaPML              string
	NamaUsahaKeluarga    string
	AssignmentIDKeluarga string
	FasihLinkKeluarga    string
	NamaUsahaBKU         string // kosong utk jenis=tanpa_bku
	AssignmentIDBKU      string
	FasihLinkBKU         string
	MatchField           string // 'hp' atau 'email' — utk duplikat: field yg cocok; utk tanpa_bku: field yg terisi
	MatchValue           string
	NamaCocok            string // "ya" | "tidak" | "" (kosong = N/A, mis. jenis=tanpa_bku) — lihat _nama_cocok di sync_usaha.py
	FirstDetectedAt      string
	SyncedAt             string
	ResolvedAt           string // kosong = masih aktif, terisi = riwayat (selesai)
}

var duplikatBKUSortCols = map[string]string{
	"lokasi":   "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas":  "ppl.name",
	"keluarga": "d.nama_usaha_keluarga",
	"bku":      "d.nama_usaha_bku",
	"match":    "d.match_field",
	"muncul":   "d.first_detected_at",
	"sync":     "d.synced_at",
	"selesai":  "d.resolved_at",
}

// duplikatBKUSource membangun derived-table (subquery) yg menormalkan kolom
// dari salah satu/kedua tabel sumber ke bentuk seragam, supaya JOIN sls/users
// + filter + sort + paginasi di bawahnya bisa satu kode buat ketiga sub-tab.
func duplikatBKUSource(view string) string {
	const tanpaBkuSelect = `
		SELECT 'tanpa_bku' AS jenis, t.id, t.sls_id, t.assignment_id_keluarga, t.nama_usaha_keluarga,
		       NULL AS assignment_id_bku, NULL AS nama_usaha_bku,
		       CASE WHEN t.hp IS NOT NULL AND t.hp != '' THEN 'hp' ELSE 'email' END AS match_field,
		       COALESCE(NULLIF(t.hp,''), t.email) AS match_value,
		       NULL AS nama_cocok,
		       t.first_detected_at, t.synced_at, t.resolved_at
		FROM usaha_keluarga_tanpa_bku t`
	const dupSelect = `
		SELECT 'duplikat' AS jenis, d.id, d.sls_id, d.assignment_id_keluarga, d.nama_usaha_keluarga,
		       d.assignment_id_bku, d.nama_usaha_bku, d.match_field, d.match_value,
		       d.nama_cocok,
		       d.first_detected_at, d.synced_at, d.resolved_at
		FROM usaha_keluarga_bku_duplikat d`

	switch view {
	case "tanpa_bku":
		return "(" + tanpaBkuSelect + ` WHERE t.resolved_at IS NULL) d`
	case "riwayat":
		return "(" + dupSelect + ` WHERE d.resolved_at IS NOT NULL
		UNION ALL` + tanpaBkuSelect + ` WHERE t.resolved_at IS NOT NULL) d`
	default: // "duplikat"
		return "(" + dupSelect + ` WHERE d.resolved_at IS NULL) d`
	}
}

// duplikatBKUFilters membangun view + WHERE clause dari query param yang
// dipakai bareng oleh AdminDuplikatBKUTable dan DownloadDuplikatBKU supaya
// hasil download selalu konsisten dengan tabel yang lagi ditampilkan.
func duplikatBKUFilters(c echo.Context) (view, where string, args []interface{}, kecs []string, pmlID, pplID int) {
	view = c.QueryParam("view")
	if view != "duplikat" && view != "riwayat" {
		// Default ke "tanpa_bku" — ini yg jadi tujuan utama menu ini: dorong
		// petugas MEMBUAT usaha BKU baru utk usaha yg belum ada. "duplikat"
		// (BKU-nya sudah ada, tinggal pindah/tutup yg lama) sifatnya cleanup,
		// penting tapi bukan prioritas utama saat menu ini dibuka.
		view = "tanpa_bku"
	}
	q := c.QueryParam("q")
	kecs = nonEmptyStrings(c.QueryParams()["kec"])
	pmlID, _ = strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ = strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where = ` WHERE (d.nama_usaha_keluarga LIKE ? OR d.nama_usaha_bku LIKE ? OR s.nama_sls LIKE ?)`
	args = []interface{}{like, like, like}
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

// AdminDuplikatBKUTable — GET /admin/table/duplikat-bku?view=duplikat|tanpa_bku|riwayat
// Menu "Usaha Keluarga → BKU":
//   - view=duplikat (default) : usaha keluarga yg sudah ada usaha BKU
//     kembarannya (hp/email sama) — duplikat aktif, perlu dipindahkan.
//   - view=tanpa_bku          : usaha keluarga yg hp/email-nya terisi tapi
//     belum ketemu usaha BKU manapun — kandidat diangkat jd BKU baru.
//   - view=riwayat            : gabungan riwayat (resolved) dari kedua kasus
//     di atas — sudah tidak terdeteksi lagi di sync terakhir.
func AdminDuplikatBKUTable(c echo.Context) error {
	view, where, args, kecs, pmlID, pplID := duplikatBKUFilters(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	q := c.QueryParam("q")

	source := duplikatBKUSource(view)

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) FROM `+source+` JOIN sls s ON s.id = d.sls_id`+where, args...).Scan(&total)

	extra := "&view=" + view
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

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, duplikatBKUSortCols, "s.nama_kec, s.nama_desa, s.nama_sls, d.first_detected_at")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/duplikat-bku", "duplikat-bku-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT d.jenis, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       COALESCE(d.nama_usaha_keluarga,''), d.assignment_id_keluarga,
		       COALESCE(d.nama_usaha_bku,''), COALESCE(d.assignment_id_bku,''),
		       d.match_field, d.match_value,
		       CASE WHEN d.nama_cocok IS NULL THEN '' WHEN d.nama_cocok = 1 THEN 'ya' ELSE 'tidak' END,
		       COALESCE(DATE_FORMAT(d.first_detected_at,'%d/%m/%Y %H:%i'),''),
		       COALESCE(DATE_FORMAT(d.synced_at,'%d/%m/%Y %H:%i'),''),
		       COALESCE(DATE_FORMAT(d.resolved_at,'%d/%m/%Y %H:%i'),'')
		FROM `+source+`
		JOIN sls s ON s.id = d.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []DuplikatBKURow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r DuplikatBKURow
			rows.Scan(&r.Jenis, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.NamaUsahaKeluarga, &r.AssignmentIDKeluarga,
				&r.NamaUsahaBKU, &r.AssignmentIDBKU,
				&r.MatchField, &r.MatchValue, &r.NamaCocok,
				&r.FirstDetectedAt, &r.SyncedAt, &r.ResolvedAt)
			r.FasihLinkKeluarga = fasihSMLink(r.AssignmentIDKeluarga)
			r.FasihLinkBKU = fasihSMLink(r.AssignmentIDBKU)
			list = append(list, r)
		}
	}

	pmlSelect := OOBSelect{
		TargetID: "duplikat-bku-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/duplikat-bku", HxTarget: "#duplikat-bku-result", HxInclude: "#duplikat-bku-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "duplikat-bku-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/duplikat-bku", HxTarget: "#duplikat-bku-result", HxInclude: "#duplikat-bku-filter-bar",
	}

	return c.Render(http.StatusOK, "duplikat_bku_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"View":      view,
		"Q":         q,
		"Kecs":      kecs,
		"PmlID":     pmlID,
		"PplID":     pplID,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}
