package handlers

import (
	"net/http"
	"strconv"
	"time"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

type OrganikSLSResult struct {
	ID       int
	KodeSLS  string
	NamaSLS  string
	NamaKec  string
	NamaDesa string
	NamaPPL  string
	NamaPML  string
}

type LaporanOrganikRow struct {
	ID            int
	NamaSLS       string
	NamaKec       string
	NamaDesa      string
	NamaPPL       string
	NamaPML       string
	Tanggal       string
	JumlahDiawasi int
	Kendala       string
	Solusi        string
}

type AdminOrganikRow struct {
	ID            int
	NamaOrganik   string
	NamaSLS       string
	NamaKec       string
	NamaDesa      string
	NamaPPL       string
	NamaPML       string
	Tanggal       string
	JumlahDiawasi int
	Kendala       string
	Solusi        string
}

func OrganikDashboard(c echo.Context) error {
	userID := mw.SessionUserID(c)
	today := time.Now().Format("2006-01-02")

	rows, err := db.DB.Query(`
		SELECT lo.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       lo.tanggal, lo.jumlah_diawasi,
		       COALESCE(lo.kendala,''), COALESCE(lo.solusi,'')
		FROM laporan_organik lo
		JOIN sls s ON s.id = lo.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE lo.organik_id = ?
		ORDER BY lo.tanggal DESC, lo.id DESC
		LIMIT 50`, userID)

	var laporan []LaporanOrganikRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r LaporanOrganikRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
				&r.NamaPPL, &r.NamaPML, &r.Tanggal, &r.JumlahDiawasi,
				&r.Kendala, &r.Solusi)
			laporan = append(laporan, r)
		}
	}

	msg := c.QueryParam("ok")
	errMsg := c.QueryParam("err")

	return c.Render(http.StatusOK, "organik.html", map[string]interface{}{
		"Name":    mw.SessionName(c),
		"Today":   today,
		"Laporan": laporan,
		"OK":      msg == "1",
		"Err":     errMsg,
		"KecList": queryKecList(),
	})
}

func OrganikSearchSLS(c echo.Context) error {
	q := c.QueryParam("q")
	if len(q) < 2 {
		return c.HTML(http.StatusOK, "")
	}
	like := "%" + q + "%"
	rows, err := db.DB.Query(`
		SELECT s.id, s.kode_sls, s.nama_sls,
		       COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE s.nama_sls LIKE ? OR s.nama_desa LIKE ? OR s.nama_kec LIKE ?
		  OR ppl.name LIKE ?
		ORDER BY s.nama_kec, s.nama_desa, s.nama_sls
		LIMIT 20`, like, like, like, like)
	if err != nil {
		return c.HTML(http.StatusOK, "")
	}
	defer rows.Close()

	var results []OrganikSLSResult
	for rows.Next() {
		var r OrganikSLSResult
		rows.Scan(&r.ID, &r.KodeSLS, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML)
		results = append(results, r)
	}
	return c.Render(http.StatusOK, "organik_sls_results.html", map[string]interface{}{
		"Results": results,
	})
}

func OrganikSaveLaporan(c echo.Context) error {
	userID := mw.SessionUserID(c)
	slsID, _ := strconv.Atoi(c.FormValue("sls_id"))
	tanggal := c.FormValue("tanggal")
	jumlah, _ := strconv.Atoi(c.FormValue("jumlah_diawasi"))
	kendala := c.FormValue("kendala")
	solusi := c.FormValue("solusi")

	if slsID == 0 || tanggal == "" {
		return c.Redirect(http.StatusFound, "/organik?err=invalid")
	}

	_, err := db.DB.Exec(`
		INSERT INTO laporan_organik (organik_id, sls_id, tanggal, jumlah_diawasi, kendala, solusi)
		VALUES (?, ?, ?, ?, ?, ?)
		ON DUPLICATE KEY UPDATE
		  jumlah_diawasi = VALUES(jumlah_diawasi),
		  kendala        = VALUES(kendala),
		  solusi         = VALUES(solusi),
		  updated_at     = CURRENT_TIMESTAMP`,
		userID, slsID, tanggal, jumlah, kendala, solusi)
	if err != nil {
		return c.Redirect(http.StatusFound, "/organik?err=db")
	}
	return c.Redirect(http.StatusFound, "/organik?ok=1")
}

// ── Admin: tabel laporan organik ────────────────────────────────────────────

func AdminTableOrganik(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	list, pageInfo := queryAdminOrganik(page, q, sort, dir)
	return c.Render(http.StatusOK, "admin_organik_table.html", map[string]interface{}{
		"OrganikRows": list,
		"OrganikPage": pageInfo,
		"Q":           q,
	})
}

var adminOrganikSortCols = map[string]string{
	"tanggal": "lo.tanggal",
	"organik": "org.name",
	"sls":     "s.nama_sls",
	"ppl":     "ppl.name",
	"pml":     "pml.name",
	"diawasi": "lo.jumlah_diawasi",
	"kendala": "lo.kendala",
	"solusi":  "lo.solusi",
}

func queryAdminOrganik(page int, q, sort, dir string) ([]AdminOrganikRow, models.PageInfo) {
	like := "%" + q + "%"
	var total int
	db.DB.QueryRow(`
		SELECT COUNT(*) FROM laporan_organik lo
		JOIN users org ON org.id = lo.organik_id
		JOIN sls s ON s.id = lo.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		WHERE org.name LIKE ? OR s.nama_sls LIKE ? OR ppl.name LIKE ?
		  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?`,
		like, like, like, like, like).Scan(&total)

	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, adminOrganikSortCols, "lo.tanggal DESC, lo.id DESC")
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT lo.id, org.name, s.nama_sls,
		       COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       lo.tanggal, lo.jumlah_diawasi,
		       COALESCE(lo.kendala,''), COALESCE(lo.solusi,'')
		FROM laporan_organik lo
		JOIN users org ON org.id = lo.organik_id
		JOIN sls s ON s.id = lo.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE org.name LIKE ? OR s.nama_sls LIKE ? OR ppl.name LIKE ?
		  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
		`+orderBy+`
		LIMIT ? OFFSET ?`,
		like, like, like, like, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/organik", "admin-organik-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []AdminOrganikRow
	for rows.Next() {
		var r AdminOrganikRow
		rows.Scan(&r.ID, &r.NamaOrganik, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
			&r.NamaPPL, &r.NamaPML, &r.Tanggal, &r.JumlahDiawasi,
			&r.Kendala, &r.Solusi)
		list = append(list, r)
	}
	return list, pageInfo
}

// ── Tab Keluarga (Organik) ──────────────────────────────────────────────────
// Daftar per-keluarga dari tabel detail keberadaan_usaha (skala_usaha
// mengandung "KELUARGA" — beda dgn coverage_usaha_keluarga yang cuma agregat
// jumlah per SLS), digabung dgn flag anomali (COUNT dari tabel anomali per
// assignment_id yang sama) supaya organik bisa langsung lihat keluarga mana
// yang perlu diperiksa lebih lanjut.
type KeluargaKeberadaanRow struct {
	ID               int
	AssignmentID     string
	Nama             string
	SkalaUsaha       string
	KeberadaanKode   string
	KeberadaanLabel  string
	GateLabel        string
	AssignmentStatus string
	SyncedAt         string
	NamaSLS          string
	NamaKec          string
	NamaDesa         string
	NamaPPL          string
	NamaPML          string
	JmlAnomali       int
	FasihLink        string
}

var organikKeluargaSortCols = map[string]string{
	"nama":    "ku.nama",
	"lokasi":  "s.nama_kec, s.nama_desa, s.nama_sls",
	"ppl":     "ppl.name",
	"pml":     "pml.name",
	"status":  "ku.keberadaan_label",
	"anomali": "jml_anomali",
	"tanggal": "ku.synced_at",
}

// OrganikKeluargaTable — GET /organik/table/keluarga
func OrganikKeluargaTable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	kec := c.QueryParam("kec")
	kecs := nonEmptyStrings([]string{kec})
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	onlyAnomali := c.QueryParam("anomali") == "1"
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	like := "%" + q + "%"

	where := ` WHERE ku.skala_usaha LIKE '%KELUARGA%' AND (ku.nama LIKE ? OR s.nama_sls LIKE ? OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?)`
	args := []interface{}{like, like, like, like}
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
	having := ""
	if onlyAnomali {
		having = ` HAVING jml_anomali > 0`
	}

	fromJoin := `
		FROM keberadaan_usaha ku
		JOIN sls s ON s.id = ku.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`

	var total int
	countQuery := `SELECT COUNT(*) FROM (
		SELECT ku.id, (SELECT COUNT(*) FROM anomali a WHERE a.assignment_id = ku.assignment_id COLLATE utf8mb4_unicode_ci) AS jml_anomali
		` + fromJoin + where + ` ` + having + `
	) t`
	db.DB.QueryRow(countQuery, args...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	if kec != "" {
		extra += "&kec=" + kec
	}
	if pmlID > 0 {
		extra += "&pml_id=" + strconv.Itoa(pmlID)
	}
	if pplID > 0 {
		extra += "&ppl_id=" + strconv.Itoa(pplID)
	}
	if onlyAnomali {
		extra += "&anomali=1"
	}

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, organikKeluargaSortCols, "s.nama_kec, s.nama_desa, s.nama_sls, ku.nama")
	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/organik/table/keluarga", "organik-keluarga-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(append([]interface{}{}, args...), models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT ku.id, ku.assignment_id, COALESCE(ku.nama,''), COALESCE(ku.skala_usaha,''),
		       COALESCE(ku.keberadaan_kode,''), COALESCE(ku.keberadaan_label,''), COALESCE(ku.gate_label,''),
		       COALESCE(ku.assignment_status,''), COALESCE(DATE_FORMAT(ku.synced_at,'%d/%m/%Y %H:%i'),''),
		       s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       (SELECT COUNT(*) FROM anomali a WHERE a.assignment_id = ku.assignment_id COLLATE utf8mb4_unicode_ci) AS jml_anomali
		`+fromJoin+where+`
		`+having+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []KeluargaKeberadaanRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r KeluargaKeberadaanRow
			rows.Scan(&r.ID, &r.AssignmentID, &r.Nama, &r.SkalaUsaha,
				&r.KeberadaanKode, &r.KeberadaanLabel, &r.GateLabel,
				&r.AssignmentStatus, &r.SyncedAt,
				&r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML,
				&r.JmlAnomali)
			r.FasihLink = fasihSMLink(r.AssignmentID)
			list = append(list, r)
		}
	}

	pmlSelect := OOBSelect{
		TargetID: "keluarga-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/organik/table/keluarga", HxTarget: "#organik-keluarga-wrap", HxInclude: "#keluarga-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "keluarga-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/organik/table/keluarga", HxTarget: "#organik-keluarga-wrap", HxInclude: "#keluarga-filter-bar",
	}

	return c.Render(http.StatusOK, "organik_keluarga_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}
