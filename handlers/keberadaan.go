package handlers

import (
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"
)

type KeberadaanRow struct {
	ID               int
	NamaSLS          string
	NamaKec          string
	NamaDesa         string
	NamaPPL          string
	NamaPML          string
	Nama             string
	Skala            string
	Kode             string
	Label            string
	GateLabel        string // alasan gate keluarga/bangunan stop (kalau keberadaan_usaha# tidak pernah ditanya)
	AssignmentStatus string // status submit assignment di FASIH
	SyncedAt         string
	SyncKeterangan   string // NULL/kosong = sync terakhir berhasil; terisi = pesan error fetch terakhir
}

type KeberadaanStat struct {
	Label string
	Kode  string
	Total int
}

// Label sintetis untuk keberadaan_usaha yang keberadaan_label-nya kosong: tiga kondisi
// berbeda yang sebelumnya sama-sama tercampur sebagai "Belum diisi". gate_label sendiri
// menyimpan teks jawaban gate apa adanya ("Tidak Ditemukan"/"...(STOP)" atau "...Baru") —
// LabelGateStop/LabelGateBaru di sini cuma dipakai sebagai nilai filter "label" sintetis.
const (
	LabelBelumDiisi = "Belum diisi"
	LabelGateStop   = "Kel/Bgn Tidak Ditemukan"
	LabelGateBaru   = "Kel/Bgn Baru"
)

// isGateBaru menentukan apakah teks gate_label berarti "keluarga/bangunan baru"
// (bukan "tidak ditemukan").
func isGateBaru(gateLabel string) bool {
	return strings.Contains(strings.ToLower(gateLabel), "baru")
}

type SLSOption struct {
	ID   int
	Nama string
}

func querySLSOptions() []SLSOption {
	rows, err := db.DB.Query(`SELECT id, nama_sls FROM sls ORDER BY nama_sls`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []SLSOption
	for rows.Next() {
		var s SLSOption
		rows.Scan(&s.ID, &s.Nama)
		list = append(list, s)
	}
	return list
}

var keberadaanSortCols = map[string]string{
	"lokasi":            "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas":           "ppl.name",
	"nama":              "k.nama",
	"skala":             "k.skala_usaha",
	"status_keberadaan": "k.keberadaan_label",
	"status_assignment": "k.assignment_status",
	"sync":              "k.synced_at",
}

// AdminKeberadaanTable — GET /admin/table/keberadaan
func AdminKeberadaanTable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	label := c.QueryParam("label")
	kecs := c.QueryParams()["kec"]
	skalas := c.QueryParams()["skala"]
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where := ` WHERE (k.nama LIKE ? OR k.skala_usaha LIKE ? OR k.keberadaan_label LIKE ? OR s.nama_sls LIKE ?)`
	args := []interface{}{like, like, like, like}

	switch label {
	case "":
		// tidak ada filter status
	case LabelBelumDiisi:
		where += ` AND (k.keberadaan_label IS NULL OR k.keberadaan_label = '') AND (k.gate_label IS NULL OR k.gate_label = '')`
	case LabelGateStop:
		where += ` AND (k.keberadaan_label IS NULL OR k.keberadaan_label = '') AND k.gate_label IS NOT NULL AND k.gate_label != '' AND LOWER(k.gate_label) NOT LIKE '%baru%'`
	case LabelGateBaru:
		where += ` AND (k.keberadaan_label IS NULL OR k.keberadaan_label = '') AND LOWER(k.gate_label) LIKE '%baru%'`
	default:
		where += ` AND k.keberadaan_label = ?`
		args = append(args, label)
	}
	inClause := func(col string, vals []string) {
		if len(vals) == 0 {
			return
		}
		ph := ""
		for i, v := range vals {
			if i > 0 {
				ph += ","
			}
			ph += "?"
			args = append(args, v)
		}
		where += ` AND ` + col + ` IN (` + ph + `)`
	}
	inClause("s.nama_kec", kecs)
	inClause("k.skala_usaha", skalas)
	if pmlID > 0 {
		where += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}

	var total int
	countArgs := append([]interface{}{}, args...)
	db.DB.QueryRow(`SELECT COUNT(*) FROM keberadaan_usaha k JOIN sls s ON s.id=k.sls_id`+where, countArgs...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	for _, v := range kecs {
		extra += "&kec=" + v
	}
	for _, v := range skalas {
		extra += "&skala=" + v
	}
	if label != "" {
		extra += "&label=" + label
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}
	if pplID > 0 {
		extra += fmt.Sprintf("&ppl_id=%d", pplID)
	}

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, keberadaanSortCols, "s.nama_kec, s.nama_desa, s.nama_sls, k.nama")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/keberadaan", "keberadaan-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT k.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       k.nama, k.skala_usaha,
		       COALESCE(k.keberadaan_kode,''), COALESCE(k.keberadaan_label,''),
		       COALESCE(k.gate_label,''), COALESCE(k.assignment_status,''),
		       COALESCE(DATE_FORMAT(k.synced_at,'%d/%m/%Y %H:%i'),''),
		       COALESCE(k.sync_keterangan,'')
		FROM keberadaan_usaha k
		JOIN sls s ON s.id = k.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []KeberadaanRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r KeberadaanRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
				&r.NamaPPL, &r.NamaPML,
				&r.Nama, &r.Skala, &r.Kode, &r.Label,
				&r.GateLabel, &r.AssignmentStatus, &r.SyncedAt, &r.SyncKeterangan)
			list = append(list, r)
		}
	}

	// Summary per label (untuk chart ringkasan di atas tabel)
	// Entri yang gate-nya stop (keluarga/bangunan tidak ditemukan ATAU baru) dipisah dari
	// "Belum diisi" yang genuine karena statusnya sebenarnya sudah selesai.
	var stats []KeberadaanStat
	statRows, err := db.DB.Query(fmt.Sprintf(`
		SELECT
		  CASE
		    WHEN keberadaan_label IS NOT NULL AND keberadaan_label != '' THEN keberadaan_label
		    WHEN gate_label IS NOT NULL AND gate_label != '' AND LOWER(gate_label) LIKE '%%baru%%' THEN '%s'
		    WHEN gate_label IS NOT NULL AND gate_label != '' THEN '%s'
		    ELSE '%s'
		  END as lbl,
		  CASE
		    WHEN keberadaan_label IS NOT NULL AND keberadaan_label != '' THEN COALESCE(keberadaan_kode,'')
		    WHEN gate_label IS NOT NULL AND gate_label != '' AND LOWER(gate_label) LIKE '%%baru%%' THEN 'GATE_BARU'
		    WHEN gate_label IS NOT NULL AND gate_label != '' THEN 'GATE'
		    ELSE ''
		  END as kode,
		  COUNT(*) as tot
		FROM keberadaan_usaha
		GROUP BY lbl, kode
		ORDER BY tot DESC`, LabelGateBaru, LabelGateStop, LabelBelumDiisi))
	if err == nil {
		defer statRows.Close()
		for statRows.Next() {
			var st KeberadaanStat
			statRows.Scan(&st.Label, &st.Kode, &st.Total)
			stats = append(stats, st)
		}
	}

	// Distinct label list untuk filter dropdown
	var labelList []string
	lblRows, _ := db.DB.Query(`
		SELECT DISTINCT COALESCE(keberadaan_label,'') as lbl
		FROM keberadaan_usaha
		WHERE keberadaan_label IS NOT NULL AND keberadaan_label != ''
		ORDER BY lbl`)
	if lblRows != nil {
		defer lblRows.Close()
		for lblRows.Next() {
			var l string
			lblRows.Scan(&l)
			labelList = append(labelList, l)
		}
	}

	pmlSelect := OOBSelect{
		TargetID: "keberadaan-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/keberadaan", HxTarget: "#keberadaan-result", HxInclude: "#keberadaan-filter-bar, #keberadaan-result",
	}
	pplSelect := OOBSelect{
		TargetID: "keberadaan-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/keberadaan", HxTarget: "#keberadaan-result", HxInclude: "#keberadaan-filter-bar, #keberadaan-result",
	}

	return c.Render(http.StatusOK, "keberadaan_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"Stats":     stats,
		"LabelList": labelList,
		"SkalaList": querySkalaList(),
		"KecList":   queryKecList(),
		"Q":         q,
		"Kecs":      kecs,
		"Skalas":    skalas,
		"Label":     label,
		"PmlID":     pmlID,
		"PplID":     pplID,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}

type KeberadaanRekapRow struct {
	ID             int
	NamaSLS        string
	NamaKec        string
	NamaDesa       string
	NamaPPL        string
	NamaPML        string
	Prioritas      bool
	Total          int
	BelumDiisi     int
	GateStop       int // keluarga/bangunan tidak ditemukan -> keberadaan_usaha# tidak pernah ditanya
	GateBaru       int // keluarga/bangunan baru (di luar prelisting) -> keberadaan_usaha# tidak pernah ditanya
	Ditemukan      int
	TidakDitemukan int
	Baru           int
	Tutup          int
	Ganda          int
	NonRespon      int
	Diverifikasi   int // assignment_status sudah APPROVED/REJECTED/REVOKED (bukan cuma OPEN/SUBMITTED)
	PctDiisi       float64
	PctVerifikasi  float64
}

var keberadaanRekapSortCols = map[string]string{
	"lokasi":          "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas":         "ppl.name",
	"total":           "total",
	"belum_diisi":     "belum_diisi",
	"gate_stop":       "gate_stop",
	"gate_baru":       "gate_baru",
	"ditemukan":       "ditemukan",
	"tidak_ditemukan": "tidak_ditemukan",
	"baru":            "baru",
	"tutup":           "tutup",
	"ganda":           "ganda",
	"non_respon":      "non_respon",
	"progres":         "(CASE WHEN (total - baru - gate_stop - gate_baru) <= 0 THEN 0 ELSE (total - baru - gate_stop - gate_baru - belum_diisi) / (total - baru - gate_stop - gate_baru) END)",
	"verifikasi":      "(CASE WHEN (total - baru - gate_stop - gate_baru) <= 0 THEN 0 ELSE diverifikasi / (total - baru - gate_stop - gate_baru) END)",
}

// AdminKeberadaanRekapTable — GET /admin/table/keberadaan-rekap
// Rekap keberadaan usaha per SLS: jumlah per status + progres (belum diisi / total kecuali baru).
func AdminKeberadaanRekapTable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	slsID, _ := strconv.Atoi(c.QueryParam("sls_id"))
	prioritasOnly := c.QueryParam("prioritas") == "1"
	like := "%" + q + "%"

	where := ` WHERE (s.nama_sls LIKE ? OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?)`
	args := []interface{}{like, like, like}
	if pmlID > 0 {
		where += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}
	if slsID > 0 {
		where += ` AND s.id = ?`
		args = append(args, slsID)
	}
	if prioritasOnly {
		where += ` AND s.prioritas = 1`
	}

	var total int
	countArgs := append([]interface{}{}, args...)
	db.DB.QueryRow(`SELECT COUNT(*) FROM sls s`+where, countArgs...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}
	if pplID > 0 {
		extra += fmt.Sprintf("&ppl_id=%d", pplID)
	}
	if slsID > 0 {
		extra += fmt.Sprintf("&sls_id=%d", slsID)
	}
	if prioritasOnly {
		extra += "&prioritas=1"
	}

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, keberadaanRekapSortCols, "s.nama_kec, s.nama_desa, s.nama_sls")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/keberadaan-rekap", "keberadaan-rekap-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT s.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name, s.prioritas,
		       COUNT(k.id) AS total,
		       SUM(CASE WHEN k.id IS NOT NULL
		                 AND (k.keberadaan_label IS NULL OR k.keberadaan_label = '')
		                 AND (k.gate_label IS NULL OR k.gate_label = '')
		                THEN 1 ELSE 0 END) AS belum_diisi,
		       SUM(CASE WHEN (k.keberadaan_label IS NULL OR k.keberadaan_label = '')
		                 AND k.gate_label IS NOT NULL AND k.gate_label != ''
		                 AND LOWER(k.gate_label) NOT LIKE '%baru%'
		                THEN 1 ELSE 0 END) AS gate_stop,
		       SUM(CASE WHEN (k.keberadaan_label IS NULL OR k.keberadaan_label = '')
		                 AND LOWER(k.gate_label) LIKE '%baru%'
		                THEN 1 ELSE 0 END) AS gate_baru,
		       SUM(CASE WHEN k.keberadaan_label = 'Ditemukan' THEN 1 ELSE 0 END) AS ditemukan,
		       SUM(CASE WHEN k.keberadaan_label = 'Tidak Ditemukan' THEN 1 ELSE 0 END) AS tidak_ditemukan,
		       SUM(CASE WHEN k.keberadaan_label = 'Baru' THEN 1 ELSE 0 END) AS baru,
		       SUM(CASE WHEN k.keberadaan_label = 'Tutup' THEN 1 ELSE 0 END) AS tutup,
		       SUM(CASE WHEN k.keberadaan_label = 'Ganda' THEN 1 ELSE 0 END) AS ganda,
		       SUM(CASE WHEN k.keberadaan_label = 'Non Respon' THEN 1 ELSE 0 END) AS non_respon,
		       SUM(CASE WHEN k.assignment_status IS NOT NULL AND k.assignment_status NOT IN ('', 'OPEN')
		                 AND k.assignment_status NOT LIKE 'SUBMITTED%'
		                THEN 1 ELSE 0 END) AS diverifikasi
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN keberadaan_usaha k ON k.sls_id = s.id`+where+`
		GROUP BY s.id, s.nama_sls, s.nama_kec, s.nama_desa, ppl.name, pml.name, s.prioritas
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []KeberadaanRekapRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r KeberadaanRekapRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML, &r.Prioritas,
				&r.Total, &r.BelumDiisi, &r.GateStop, &r.GateBaru, &r.Ditemukan, &r.TidakDitemukan,
				&r.Baru, &r.Tutup, &r.Ganda, &r.NonRespon, &r.Diverifikasi)
			// Baru, gate-stop, & gate-baru dikecualikan dari penyebut: semuanya sudah
			// "selesai" (bukan lagi pekerjaan yang tersisa untuk PPL).
			denom := r.Total - r.Baru - r.GateStop - r.GateBaru
			if denom > 0 {
				r.PctDiisi = math.Min(float64(denom-r.BelumDiisi)*100/float64(denom), 100)
				r.PctVerifikasi = math.Min(float64(r.Diverifikasi)*100/float64(denom), 100)
			}
			list = append(list, r)
		}
	}

	pplSelect := OOBSelect{
		TargetID: "kebrekap-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(nil, pmlID), Selected: pplID,
		HxGet: "/admin/table/keberadaan-rekap", HxTarget: "#keberadaan-rekap-result", HxInclude: "#keberadaan-rekap-filter-bar",
	}
	slsSelect := OOBSelect{
		TargetID: "kebrekap-sls-select", Name: "sls_id", Placeholder: "Semua SLS",
		Options: querySLSOptionsByFilter(nil, pmlID, pplID), Selected: slsID,
		HxGet: "/admin/table/keberadaan-rekap", HxTarget: "#keberadaan-rekap-result", HxInclude: "#keberadaan-rekap-filter-bar",
	}

	return c.Render(http.StatusOK, "admin_keberadaan_rekap_table.html", map[string]interface{}{
		"Rows":          list,
		"PageInfo":      pageInfo,
		"Q":             q,
		"PmlID":         pmlID,
		"PplID":         pplID,
		"SlsID":         slsID,
		"PrioritasOnly": prioritasOnly,
		"PPLSelect":     pplSelect,
		"SLSSelect":     slsSelect,
	})
}

// PPLKeberadaan — GET /ppl/keberadaan
func PPLKeberadaan(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	label := c.QueryParam("label")
	skalas := c.QueryParams()["skala"]
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	slsID, _ := strconv.Atoi(c.QueryParam("sls_id"))
	like := "%" + q + "%"

	where := ` WHERE s.ppl_id = ? AND (k.nama LIKE ? OR k.skala_usaha LIKE ? OR k.keberadaan_label LIKE ? OR s.nama_sls LIKE ?)`
	args := []interface{}{userID, like, like, like, like}

	if label != "" {
		where += ` AND k.keberadaan_label = ?`
		args = append(args, label)
	}
	if slsID > 0 {
		where += ` AND s.id = ?`
		args = append(args, slsID)
	}
	if len(skalas) > 0 {
		ph := ""
		for i, v := range skalas {
			if i > 0 {
				ph += ","
			}
			ph += "?"
			args = append(args, v)
		}
		where += ` AND k.skala_usaha IN (` + ph + `)`
	}

	var total int
	countArgs := append([]interface{}{}, args...)
	db.DB.QueryRow(`SELECT COUNT(*) FROM keberadaan_usaha k JOIN sls s ON s.id=k.sls_id`+where, countArgs...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	for _, v := range skalas {
		extra += "&skala=" + v
	}
	if label != "" {
		extra += "&label=" + label
	}
	if slsID > 0 {
		extra += fmt.Sprintf("&sls_id=%d", slsID)
	}

	pplKeberadaanSortCols := map[string]string{
		"sls":               "s.nama_sls",
		"nama":              "k.nama",
		"skala":             "k.skala_usaha",
		"status_keberadaan": "k.keberadaan_label",
		"status_assignment": "k.assignment_status",
		"sync":              "k.synced_at",
	}
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, pplKeberadaanSortCols, "s.nama_sls, k.nama")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/ppl/keberadaan", "ppl-keberadaan-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT k.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       k.nama, k.skala_usaha,
		       COALESCE(k.keberadaan_kode,''), COALESCE(k.keberadaan_label,''),
		       COALESCE(k.gate_label,''), COALESCE(k.assignment_status,''),
		       COALESCE(DATE_FORMAT(k.synced_at,'%d/%m/%Y %H:%i'),'')
		FROM keberadaan_usaha k
		JOIN sls s ON s.id = k.sls_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	type PPLKeberadaanRow struct {
		ID               int
		NamaSLS          string
		NamaKec          string
		NamaDesa         string
		Nama             string
		Skala            string
		Kode             string
		Label            string
		GateLabel        string
		AssignmentStatus string
		SyncedAt         string
	}
	var list []PPLKeberadaanRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r PPLKeberadaanRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
				&r.Nama, &r.Skala, &r.Kode, &r.Label,
				&r.GateLabel, &r.AssignmentStatus, &r.SyncedAt)
			list = append(list, r)
		}
	}

	// Skala list hanya untuk SLS milik PPL ini
	var skalaList []string
	skRows, _ := db.DB.Query(`SELECT DISTINCT COALESCE(skala_usaha, '') AS skala_usaha FROM keberadaan_usaha k JOIN sls s ON s.id=k.sls_id WHERE s.ppl_id=? ORDER BY skala_usaha`, userID)
	if skRows != nil {
		defer skRows.Close()
		for skRows.Next() {
			var s string
			skRows.Scan(&s)
			skalaList = append(skalaList, s)
		}
	}

	// SLS list milik PPL ini
	type SLSOpt struct {
		ID   int
		Nama string
	}
	var slsList []SLSOpt
	slsRows, _ := db.DB.Query(`SELECT id, nama_sls FROM sls WHERE ppl_id=? ORDER BY nama_sls`, userID)
	if slsRows != nil {
		defer slsRows.Close()
		for slsRows.Next() {
			var s SLSOpt
			slsRows.Scan(&s.ID, &s.Nama)
			slsList = append(slsList, s)
		}
	}

	return c.Render(http.StatusOK, "ppl_keberadaan_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"SkalaList": skalaList,
		"SLSList":   slsList,
		"Skalas":    skalas,
		"SlsID":     slsID,
		"Q":         q,
		"Label":     label,
	})
}

// PMLKeberadaan — GET /pml/keberadaan
func PMLKeberadaan(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	label := c.QueryParam("label")
	skalas := c.QueryParams()["skala"]
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	slsID, _ := strconv.Atoi(c.QueryParam("sls_id"))
	like := "%" + q + "%"

	where := ` WHERE s.pml_id = ? AND (k.nama LIKE ? OR k.skala_usaha LIKE ? OR k.keberadaan_label LIKE ? OR s.nama_sls LIKE ?)`
	args := []interface{}{userID, like, like, like, like}

	if label != "" {
		where += ` AND k.keberadaan_label = ?`
		args = append(args, label)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}
	if slsID > 0 {
		where += ` AND s.id = ?`
		args = append(args, slsID)
	}
	if len(skalas) > 0 {
		ph := ""
		for i, v := range skalas {
			if i > 0 {
				ph += ","
			}
			ph += "?"
			args = append(args, v)
		}
		where += ` AND k.skala_usaha IN (` + ph + `)`
	}

	var total int
	countArgs := append([]interface{}{}, args...)
	db.DB.QueryRow(`SELECT COUNT(*) FROM keberadaan_usaha k JOIN sls s ON s.id=k.sls_id`+where, countArgs...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	for _, v := range skalas {
		extra += "&skala=" + v
	}
	if label != "" {
		extra += "&label=" + label
	}
	if pplID > 0 {
		extra += fmt.Sprintf("&ppl_id=%d", pplID)
	}
	if slsID > 0 {
		extra += fmt.Sprintf("&sls_id=%d", slsID)
	}

	pmlKeberadaanSortCols := map[string]string{
		"sls":               "s.nama_sls",
		"ppl":               "ppl.name",
		"nama":              "k.nama",
		"skala":             "k.skala_usaha",
		"status_keberadaan": "k.keberadaan_label",
		"status_assignment": "k.assignment_status",
		"sync":              "k.synced_at",
	}
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, pmlKeberadaanSortCols, "s.nama_kec, s.nama_sls, k.nama")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/pml/keberadaan", "pml-keberadaan-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT k.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name,
		       k.nama, k.skala_usaha,
		       COALESCE(k.keberadaan_kode,''), COALESCE(k.keberadaan_label,''),
		       COALESCE(k.gate_label,''), COALESCE(k.assignment_status,''),
		       COALESCE(DATE_FORMAT(k.synced_at,'%d/%m/%Y %H:%i'),'')
		FROM keberadaan_usaha k
		JOIN sls s ON s.id = k.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	type PMLKeberadaanRow struct {
		ID               int
		NamaSLS          string
		NamaKec          string
		NamaDesa         string
		NamaPPL          string
		Nama             string
		Skala            string
		Kode             string
		Label            string
		GateLabel        string
		AssignmentStatus string
		SyncedAt         string
	}
	var list []PMLKeberadaanRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r PMLKeberadaanRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
				&r.NamaPPL, &r.Nama, &r.Skala, &r.Kode, &r.Label,
				&r.GateLabel, &r.AssignmentStatus, &r.SyncedAt)
			list = append(list, r)
		}
	}

	// PPL list di bawah PML ini
	var pplList []PPLUser
	pplRows, _ := db.DB.Query(`SELECT u.id, u.name FROM users u JOIN sls s ON s.ppl_id=u.id WHERE s.pml_id=? GROUP BY u.id, u.name ORDER BY u.name`, userID)
	if pplRows != nil {
		defer pplRows.Close()
		for pplRows.Next() {
			var p PPLUser
			pplRows.Scan(&p.ID, &p.Name)
			pplList = append(pplList, p)
		}
	}

	// Skala list
	var skalaList []string
	skRows, _ := db.DB.Query(`SELECT DISTINCT COALESCE(skala_usaha, '') AS skala_usaha FROM keberadaan_usaha k JOIN sls s ON s.id=k.sls_id WHERE s.pml_id=? ORDER BY skala_usaha`, userID)
	if skRows != nil {
		defer skRows.Close()
		for skRows.Next() {
			var s string
			skRows.Scan(&s)
			skalaList = append(skalaList, s)
		}
	}

	// SLS list — filter berdasarkan PPL yang dipilih (jika ada)
	type SLSOpt struct {
		ID   int
		Nama string
	}
	var slsList []SLSOpt
	slsQ := `SELECT id, nama_sls FROM sls WHERE pml_id=?`
	slsArgs := []interface{}{userID}
	if pplID > 0 {
		slsQ += ` AND ppl_id=?`
		slsArgs = append(slsArgs, pplID)
	}
	slsRows, _ := db.DB.Query(slsQ+` ORDER BY nama_sls`, slsArgs...)
	if slsRows != nil {
		defer slsRows.Close()
		for slsRows.Next() {
			var s SLSOpt
			slsRows.Scan(&s.ID, &s.Nama)
			slsList = append(slsList, s)
		}
	}

	return c.Render(http.StatusOK, "pml_keberadaan_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"PPLList":   pplList,
		"SLSList":   slsList,
		"SkalaList": skalaList,
		"Skalas":    skalas,
		"Q":         q,
		"Label":     label,
		"PplID":     pplID,
		"SlsID":     slsID,
	})
}
