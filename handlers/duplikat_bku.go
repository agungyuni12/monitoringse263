package handlers

import (
	"fmt"
	"net/http"
	"strconv"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	"monitoringse/models"
)

// DuplikatBKURow adalah satu baris di menu "Usaha Keluarga → BKU" — usaha
// jenis_prelist='keluarga' yang hp/email-nya sama persis dengan usaha BKU
// (mandiri) yang sudah ada, terdeteksi oleh scraper/sync_usaha.py (lihat
// sync_duplikat_bku), sumbernya tabel usaha_keluarga_bku_duplikat.
type DuplikatBKURow struct {
	ID                   int
	NamaSLS              string
	NamaKec              string
	NamaDesa             string
	NamaPPL              string
	NamaPML              string
	NamaUsahaKeluarga    string
	AssignmentIDKeluarga string
	FasihLinkKeluarga    string
	NamaUsahaBKU         string
	AssignmentIDBKU      string
	FasihLinkBKU         string
	MatchField           string // 'hp' atau 'email'
	MatchValue           string
	FirstDetectedAt      string
	SyncedAt             string
	ResolvedAt           string // kosong = masih perlu dipindah, terisi = sudah selesai (riwayat)
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

// AdminDuplikatBKUTable — GET /admin/table/duplikat-bku?status=pending|riwayat
// Menu "Usaha Keluarga → BKU": daftar usaha yang nempel roster keluarga tapi
// hp/email-nya cocok dengan usaha BKU (mandiri) yang sudah ada — kandidat
// duplikat yang perlu dipindahkan/ditutup petugas lewat FASIH-mobile.
// status=pending (default) tampilkan yang masih aktif (resolved_at NULL),
// status=riwayat tampilkan yang sudah tidak terdeteksi lagi di sync terakhir.
func AdminDuplikatBKUTable(c echo.Context) error {
	status := c.QueryParam("status")
	if status != "riwayat" {
		status = "pending"
	}
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	q := c.QueryParam("q")
	kecs := nonEmptyStrings(c.QueryParams()["kec"])
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where := ` WHERE (d.nama_usaha_keluarga LIKE ? OR d.nama_usaha_bku LIKE ? OR s.nama_sls LIKE ?)`
	args := []interface{}{like, like, like}
	if status == "riwayat" {
		where += ` AND d.resolved_at IS NOT NULL`
	} else {
		where += ` AND d.resolved_at IS NULL`
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

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) FROM usaha_keluarga_bku_duplikat d JOIN sls s ON s.id = d.sls_id`+where, args...).Scan(&total)

	extra := "&status=" + status
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
		SELECT d.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       COALESCE(d.nama_usaha_keluarga,''), d.assignment_id_keluarga,
		       COALESCE(d.nama_usaha_bku,''), d.assignment_id_bku,
		       d.match_field, d.match_value,
		       COALESCE(DATE_FORMAT(d.first_detected_at,'%d/%m/%Y %H:%i'),''),
		       COALESCE(DATE_FORMAT(d.synced_at,'%d/%m/%Y %H:%i'),''),
		       COALESCE(DATE_FORMAT(d.resolved_at,'%d/%m/%Y %H:%i'),'')
		FROM usaha_keluarga_bku_duplikat d
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
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.NamaUsahaKeluarga, &r.AssignmentIDKeluarga,
				&r.NamaUsahaBKU, &r.AssignmentIDBKU,
				&r.MatchField, &r.MatchValue,
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
		"Status":    status,
		"Q":         q,
		"Kecs":      kecs,
		"PmlID":     pmlID,
		"PplID":     pplID,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}
