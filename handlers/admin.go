package handlers

import (
	"fmt"
	"math"
	"net/http"
	"strconv"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

type GeoStat struct {
	Submit     int     `json:"submit"`
	Draft      int     `json:"draft"`
	Target     int     `json:"target"`
	FasihTotal int     `json:"fasih_total"`
	Pct        float64 `json:"pct"`
}

func AdminGeoStats(c echo.Context) error {
	level := c.QueryParam("level") // "kec", "desa", atau "" (SLS)
	var prefixLen int
	switch level {
	case "kec":
		prefixLen = 7
	case "desa":
		prefixLen = 10
	default:
		prefixLen = 14
	}

	rows, err := db.DB.Query(fmt.Sprintf(`
		SELECT SUBSTRING(s.kode_sls, 1, %d),
		       COALESCE(SUM(p.jumlah_submit), 0),
		       COALESCE(SUM(p.jumlah_draft), 0),
		       COALESCE(SUM(s.target), 0),
		       COALESCE(SUM(p.fasih_total), 0)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		GROUP BY SUBSTRING(s.kode_sls, 1, %d)
	`, prefixLen, prefixLen))
	if err != nil {
		return c.JSON(http.StatusInternalServerError, map[string]string{"error": err.Error()})
	}
	defer rows.Close()

	result := map[string]GeoStat{}
	for rows.Next() {
		var key string
		var g GeoStat
		rows.Scan(&key, &g.Submit, &g.Draft, &g.Target, &g.FasihTotal)
		if g.FasihTotal > 0 {
			g.Pct = math.Min(float64(g.Submit)*100/float64(g.FasihTotal), 100)
		}
		result[key] = g
	}
	return c.JSON(http.StatusOK, result)
}

func AdminGeoJSON(c echo.Context) error {
	level := c.QueryParam("level")
	switch level {
	case "kec":
		return c.File("geo/peta_kec.geojson")
	case "desa":
		return c.File("geo/peta_desa.geojson")
	default:
		return c.File("geo/peta_sls_202525205.geojson")
	}
}

type AdminSummary struct {
	TotalSLS         int
	TotalTarget      int
	TotalSubmit      int // jumlah_submit = semua status submit (untuk % progress)
	TotalFasihSubmit int // fasih_submitted = pending review PML (untuk kolom Submit)
	TotalDraft       int
	// Approved per level
	TotalApprovedPengawas  int
	TotalApprovedKabupaten int
	TotalApprovedProvinsi  int
	TotalApprovedPusat     int
	// Rejected & Revoked per level
	TotalRejectedPengawas  int
	TotalRevokedPengawas   int
	TotalRejectedKabupaten int
	TotalRejectedProvinsi  int
	TotalRejectedPusat     int
	// Legacy aliases untuk progress bar (gunakan approved pengawas)
	TotalDiperiksa  int // = TotalApprovedPengawas
	TotalError      int // = TotalRejectedPengawas
	TotalObservasi  int
	TotalFasihTotal int
	PctSubmit       float64
	PctDiperiksa    float64
}

type PMLRow struct {
	ID           int
	Name         string
	JmlPPL       int
	JmlSLS       int
	Submit       int // fasih_submitted: pending
	JumlahSubmit int // jumlah_submit: semua status (untuk %)
	Draft        int
	// Breakdown per level
	ApprovedPengawas  int
	RejectedPengawas  int
	RevokedPengawas   int
	ApprovedKabupaten int
	RejectedKabupaten int
	ApprovedProvinsi  int
	RejectedProvinsi  int
	ApprovedPusat     int
	RejectedPusat     int
	// Alias lama (tetap diisi untuk kompatibilitas template)
	Diperiksa      int // = ApprovedPengawas
	Error          int // = RejectedPengawas
	FasihTotal     int
	PctSubmit      float64
	Observasi      int
	KendalaTerbuka int
	// Terverifikasi = semua status kecuali open, submit, draft (approved+rejected+revoked di semua level)
	Terverifikasi    int
	PctTerverifikasi float64
}

type PPLRow struct {
	ID           int
	Name         string
	PMLName      string
	JmlSLS       int
	Submit       int // fasih_submitted: pending
	JumlahSubmit int // jumlah_submit: semua status (untuk %)
	Draft        int
	// Breakdown per level
	ApprovedPengawas  int
	RejectedPengawas  int
	ApprovedKabupaten int
	RejectedKabupaten int
	ApprovedProvinsi  int
	RejectedProvinsi  int
	ApprovedPusat     int
	RejectedPusat     int
	// Alias lama
	Diperiksa  int // = ApprovedPengawas
	Error      int // = RejectedPengawas
	FasihTotal int
	PctSubmit  float64
}

type PMLUser struct {
	ID   int
	Name string
}

func queryPMLUsers() []PMLUser {
	rows, err := db.DB.Query(`SELECT u.id, u.name FROM users u JOIN sls s ON s.pml_id=u.id WHERE u.role='pml' GROUP BY u.id, u.name ORDER BY u.name`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []PMLUser
	for rows.Next() {
		var p PMLUser
		rows.Scan(&p.ID, &p.Name)
		list = append(list, p)
	}
	return list
}

type SLSAdminRow struct {
	ID              int
	KodeSLS         string
	NamaSLS         string
	NamaPPL         string
	NamaPML         string
	NamaKec         string
	NamaDesa        string
	Target          int
	FasihSubmit     int // fasih_submitted: pending review PML (kolom Submit)
	JumlahSubmit    int // jumlah_submit: semua status (untuk % progress)
	JumlahDraft     int
	JumlahDiperiksa int
	JumlahError     int
	JumlahObservasi int
	FasihTotal      int
	PctSubmit       float64
	StatusKendala   string
	Kendala         string
}

// Tiga metode persentase "Progres" di tab Progres Semua SLS (per SLS/Desa/Kec),
// dipilih via query param ?metode=1|2|3:
//  1. Total/Total     — JumlahSubmit / FasihTotal: dari SEMUA assignment yang ada
//     sekarang di FASIH, berapa persen yang sudah disubmit. (perilaku lama, default)
//  2. Total/Prelist   — JumlahSubmit / Target: dari TARGET RESMI prelist, berapa
//     persen yang sudah dihasilkan. Bisa >100% kalau ada tambahan assignment baru.
//  3. Prelist/Prelist — (Target - sisa yang belum) / Target, dengan
//     "sisa yang belum" = max(FasihTotal - JumlahSubmit, 0) — jumlah assignment
//     yang ADA di FASIH sekarang tapi belum disubmit (termasuk backlog dari
//     assignment tambahan). Backlog itu tetap mengurangi kuota resmi.
const (
	MetodeTotalVsTotal     = "1"
	MetodeTotalVsPrelist   = "2"
	MetodePrelistVsPrelist = "3"
)

func normalizeMetode(m string) string {
	if m != MetodeTotalVsPrelist && m != MetodePrelistVsPrelist {
		return MetodeTotalVsTotal
	}
	return m
}

func computePctProgres(metode string, jumlahSubmit, fasihTotal, target int) float64 {
	switch metode {
	case MetodeTotalVsPrelist:
		if target > 0 {
			return float64(jumlahSubmit) * 100 / float64(target)
		}
	case MetodePrelistVsPrelist:
		if target > 0 {
			sisaBelum := math.Max(float64(fasihTotal-jumlahSubmit), 0)
			return math.Max(math.Min((float64(target)-sisaBelum)*100/float64(target), 100), 0)
		}
	default: // MetodeTotalVsTotal
		if fasihTotal > 0 {
			return math.Min(float64(jumlahSubmit)*100/float64(fasihTotal), 100)
		}
	}
	return 0
}

// progresSortExprGeneric membangun ekspresi SQL sort utk kolom "progres", sesuai
// metode yang dipilih. submitExpr/totalExpr/targetExpr adalah fragmen SQL yang
// beda tergantung level (SLS: kolom mentah; Desa/Kec: SUM(...) aggregate).
func progresSortExprGeneric(metode, submitExpr, totalExpr, targetExpr string) string {
	switch metode {
	case MetodeTotalVsPrelist:
		return fmt.Sprintf("(CASE WHEN %s=0 THEN 0 ELSE %s/%s END)", targetExpr, submitExpr, targetExpr)
	case MetodePrelistVsPrelist:
		return fmt.Sprintf("(CASE WHEN %s=0 THEN 0 ELSE (%s-GREATEST(%s-%s,0))/%s END)", targetExpr, targetExpr, totalExpr, submitExpr, targetExpr)
	default:
		return fmt.Sprintf("(CASE WHEN %s=0 THEN 0 ELSE %s/%s END)", totalExpr, submitExpr, totalExpr)
	}
}

type DesaRow struct {
	NamaDesa        string
	NamaKec         string
	JmlSLS          int
	Target          int
	FasihSubmit     int // fasih_submitted: pending review PML (kolom Submit)
	JumlahSubmit    int // jumlah_submit: semua status (untuk % progress)
	JumlahDraft     int
	JumlahDiperiksa int
	JumlahError     int
	FasihTotal      int
	PctSubmit       float64
}

type KecRow struct {
	NamaKec         string
	JmlSLS          int
	Target          int
	FasihSubmit     int // fasih_submitted: pending review PML (kolom Submit)
	JumlahSubmit    int // jumlah_submit: semua status (untuk % progress)
	JumlahDraft     int
	JumlahDiperiksa int
	JumlahError     int
	FasihTotal      int
	PctSubmit       float64
}

func AdminDashboard(c echo.Context) error {
	var s AdminSummary
	db.DB.QueryRow(`
		SELECT
		  (SELECT COUNT(*) FROM sls),
		  (SELECT COALESCE(SUM(target),0) FROM sls),
		  (SELECT COALESCE(SUM(jumlah_submit),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_submitted),0) FROM progress),
		  (SELECT COALESCE(SUM(jumlah_draft),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_approved_pengawas),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_rejected_pengawas),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_revoked_pengawas),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_approved_kabupaten),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_rejected_kabupaten),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_approved_provinsi),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_rejected_provinsi),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_approved_pusat),0) FROM progress),
		  (SELECT COALESCE(SUM(fasih_rejected_pusat),0) FROM progress),
		  (SELECT COALESCE(SUM(jumlah_observasi),0) FROM verifikasi_harian),
		  (SELECT COALESCE(SUM(fasih_total),0) FROM progress)`).
		Scan(&s.TotalSLS, &s.TotalTarget, &s.TotalSubmit, &s.TotalFasihSubmit, &s.TotalDraft,
			&s.TotalApprovedPengawas, &s.TotalRejectedPengawas, &s.TotalRevokedPengawas,
			&s.TotalApprovedKabupaten, &s.TotalRejectedKabupaten,
			&s.TotalApprovedProvinsi, &s.TotalRejectedProvinsi,
			&s.TotalApprovedPusat, &s.TotalRejectedPusat,
			&s.TotalObservasi, &s.TotalFasihTotal)
	// Isi alias lama supaya template lama tetap kompatibel
	s.TotalDiperiksa = s.TotalApprovedPengawas
	s.TotalError = s.TotalRejectedPengawas
	if s.TotalFasihTotal > 0 {
		s.PctSubmit = math.Min(float64(s.TotalSubmit)*100/float64(s.TotalFasihTotal), 100)
	}
	if s.TotalSubmit > 0 {
		s.PctDiperiksa = math.Min(float64(s.TotalDiperiksa)*100/float64(s.TotalSubmit), 100)
	}

	pmlPage, _ := strconv.Atoi(c.QueryParam("pml_page"))
	if pmlPage < 1 {
		pmlPage = 1
	}
	pplPage, _ := strconv.Atoi(c.QueryParam("ppl_page"))
	if pplPage < 1 {
		pplPage = 1
	}
	slsPage, _ := strconv.Atoi(c.QueryParam("sls_page"))
	if slsPage < 1 {
		slsPage = 1
	}
	orgPage, _ := strconv.Atoi(c.QueryParam("org_page"))
	if orgPage < 1 {
		orgPage = 1
	}
	q := c.QueryParam("q")

	pmls, pmlPage2 := queryAdminPML(pmlPage, "", "", "")
	ppls, pplPage2 := queryAdminPPL(pplPage, "", 0, "", "")
	slsList, slsPage2 := queryAdminSLS(slsPage, q, "", "", MetodeTotalVsTotal)
	orgList, orgPage2 := queryAdminOrganik(orgPage, "", "", "")

	return c.Render(http.StatusOK, "admin.html", map[string]interface{}{
		"Name":        mw.SessionName(c),
		"Summary":     s,
		"PMLs":        pmls,
		"PPLs":        ppls,
		"SLSList":     slsList,
		"Metode":      MetodeTotalVsTotal,
		"OrganikRows": orgList,
		"PMLPage":     pmlPage2,
		"PPLPage":     pplPage2,
		"SLSPage":     slsPage2,
		"OrganikPage": orgPage2,
		"Q":           q,
		"StatusOpts":  models.StatusOptions,
		"PMLUserList": queryPMLUsers(),
		"PPLUserList": queryPPLUsers(),
		"KecList":     queryKecList(),
		"SkalaList":   querySkalaList(),
		"LastSync":    LastSyncFromDB(),
		"KebSLSList":  querySLSOptions(),
	})
}

func AdminTablePML(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pmls, pageInfo := queryAdminPML(page, q, sort, dir)
	return c.Render(http.StatusOK, "admin_pml_table.html", map[string]interface{}{
		"PMLs": pmls, "PMLPage": pageInfo,
	})
}

func AdminTablePPL(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	ppls, pageInfo := queryAdminPPL(page, q, pmlID, sort, dir)
	return c.Render(http.StatusOK, "admin_ppl_table.html", map[string]interface{}{
		"PPLs": ppls, "PPLPage": pageInfo,
	})
}

func AdminTableSLS(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	level := c.QueryParam("level")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	metode := normalizeMetode(c.QueryParam("metode"))

	switch level {
	case "desa":
		list, pageInfo := queryAdminSLSByDesa(page, q, sort, dir, metode)
		return c.Render(http.StatusOK, "admin_desa_table.html", map[string]interface{}{
			"DesaList": list, "DesaPage": pageInfo, "Metode": metode,
		})
	case "kec":
		list, pageInfo := queryAdminSLSByKec(page, q, sort, dir, metode)
		return c.Render(http.StatusOK, "admin_kec_table.html", map[string]interface{}{
			"KecList": list, "KecPage": pageInfo, "Metode": metode,
		})
	default:
		slsList, pageInfo := queryAdminSLS(page, q, sort, dir, metode)
		return c.Render(http.StatusOK, "admin_sls_table.html", map[string]interface{}{
			"SLSList": slsList, "SLSPage": pageInfo, "Q": q, "Metode": metode,
		})
	}
}

var adminPMLSortCols = map[string]string{
	"nama":     "u.name",
	"jml_ppl":  "COUNT(DISTINCT s.ppl_id)",
	"jml_sls":  "COUNT(s.id)",
	"total":    "COALESCE(SUM(p.fasih_total),0)",
	"submit":   "COALESCE(SUM(p.fasih_submitted),0)",
	"draft":    "COALESCE(SUM(p.jumlah_draft),0)",
	"approved": "COALESCE(SUM(p.fasih_approved_pengawas),0)",
	"rejected": "COALESCE(SUM(p.fasih_rejected_pengawas),0)",
	"progres":  "(CASE WHEN COALESCE(SUM(p.fasih_total),0)=0 THEN 0 ELSE COALESCE(SUM(p.jumlah_submit),0)/SUM(p.fasih_total) END)",
	"terverifikasi": "(COALESCE(SUM(p.fasih_approved_pengawas),0)+COALESCE(SUM(p.fasih_rejected_pengawas),0)+COALESCE(SUM(p.fasih_revoked_pengawas),0)+" +
		"COALESCE(SUM(p.fasih_approved_kabupaten),0)+COALESCE(SUM(p.fasih_rejected_kabupaten),0)+" +
		"COALESCE(SUM(p.fasih_approved_provinsi),0)+COALESCE(SUM(p.fasih_rejected_provinsi),0)+" +
		"COALESCE(SUM(p.fasih_approved_pusat),0)+COALESCE(SUM(p.fasih_rejected_pusat),0))",
}

func queryAdminPML(page int, q, sort, dir string) ([]PMLRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT u.id) FROM users u JOIN sls s ON s.pml_id=u.id WHERE u.role='pml' AND u.name LIKE ?`, like).Scan(&total)
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, adminPMLSortCols, "u.name")
	offset := (page - 1) * models.PerPage

	rows, err := db.DB.Query(`
		SELECT u.id, u.name,
		       COUNT(DISTINCT s.ppl_id), COUNT(s.id),
		       COALESCE(SUM(p.fasih_submitted),0), COALESCE(SUM(p.jumlah_submit),0),
		       COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0), COALESCE(SUM(p.fasih_rejected_pengawas),0),
		       COALESCE(SUM(p.fasih_revoked_pengawas),0),
		       COALESCE(SUM(p.fasih_approved_kabupaten),0), COALESCE(SUM(p.fasih_rejected_kabupaten),0),
		       COALESCE(SUM(p.fasih_approved_provinsi),0),  COALESCE(SUM(p.fasih_rejected_provinsi),0),
		       COALESCE(SUM(p.fasih_approved_pusat),0),     COALESCE(SUM(p.fasih_rejected_pusat),0),
		       COALESCE(SUM(p.fasih_total),0),
		       COALESCE((SELECT SUM(vh2.jumlah_observasi) FROM verifikasi_harian vh2 WHERE vh2.sls_id IN (SELECT id FROM sls WHERE pml_id=u.id)),0),
		       COUNT(DISTINCT CASE WHEN vh3.status_kendala IN ('open','in_progress','escalated') THEN s.id END)
		FROM users u
		JOIN sls s ON s.pml_id = u.id
		LEFT JOIN progress p ON p.sls_id = s.id
		LEFT JOIN (
		  SELECT sls_id, status_kendala
		  FROM verifikasi_harian vh4
		  WHERE tanggal = (SELECT MAX(tanggal) FROM verifikasi_harian WHERE sls_id = vh4.sls_id)
		) vh3 ON vh3.sls_id = s.id
		WHERE u.role = 'pml' AND u.name LIKE ?
		GROUP BY u.id, u.name
		`+orderBy+`
		LIMIT ? OFFSET ?`, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/pml", "admin-pml-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var pmls []PMLRow
	for rows.Next() {
		var r PMLRow
		rows.Scan(&r.ID, &r.Name, &r.JmlPPL, &r.JmlSLS,
			&r.Submit, &r.JumlahSubmit, &r.Draft,
			&r.ApprovedPengawas, &r.RejectedPengawas, &r.RevokedPengawas,
			&r.ApprovedKabupaten, &r.RejectedKabupaten,
			&r.ApprovedProvinsi, &r.RejectedProvinsi,
			&r.ApprovedPusat, &r.RejectedPusat,
			&r.FasihTotal, &r.Observasi, &r.KendalaTerbuka)
		// Isi alias lama
		r.Diperiksa = r.ApprovedPengawas
		r.Error = r.RejectedPengawas
		if r.FasihTotal > 0 {
			r.PctSubmit = math.Min(float64(r.JumlahSubmit)*100/float64(r.FasihTotal), 100)
			if r.PctSubmit > 100 {
				r.PctSubmit = 100.0
			}
		}
		// Terverifikasi = semua status kecuali open, submit, draft
		r.Terverifikasi = r.ApprovedPengawas + r.RejectedPengawas + r.RevokedPengawas +
			r.ApprovedKabupaten + r.RejectedKabupaten +
			r.ApprovedProvinsi + r.RejectedProvinsi +
			r.ApprovedPusat + r.RejectedPusat
		// Pembagi pakai JumlahSubmit (submit+approve+reject+revoke, tidak termasuk open/draft),
		// bukan FasihTotal — supaya assignment yang belum pernah disubmit sama sekali
		// tidak ikut mengencerkan persentase verifikasi PML.
		if r.JumlahSubmit > 0 {
			r.PctTerverifikasi = math.Min(float64(r.Terverifikasi)*100/float64(r.JumlahSubmit), 100)
		}
		pmls = append(pmls, r)
	}
	return pmls, pageInfo
}

var adminPPLSortCols = map[string]string{
	"nama":     "u.name",
	"pml":      "pml.name",
	"jml_sls":  "COUNT(s.id)",
	"total":    "COALESCE(SUM(p.fasih_total),0)",
	"submit":   "COALESCE(SUM(p.fasih_submitted),0)",
	"draft":    "COALESCE(SUM(p.jumlah_draft),0)",
	"approved": "COALESCE(SUM(p.fasih_approved_pengawas),0)",
	"rejected": "COALESCE(SUM(p.fasih_rejected_pengawas),0)",
	"progres":  "(CASE WHEN COALESCE(SUM(p.fasih_total),0)=0 THEN 0 ELSE COALESCE(SUM(p.jumlah_submit),0)/SUM(p.fasih_total) END)",
}

func queryAdminPPL(page int, q string, pmlID int, sort, dir string) ([]PPLRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}

	pmlFilter := ""
	var countArgs, queryArgs []interface{}
	offset := (page - 1) * models.PerPage
	if pmlID > 0 {
		pmlFilter = " AND s.pml_id = ?"
		countArgs = []interface{}{pmlID, like, like}
		queryArgs = []interface{}{pmlID, like, like, models.PerPage, offset}
	} else {
		countArgs = []interface{}{like, like}
		queryArgs = []interface{}{like, like, models.PerPage, offset}
	}

	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT u.id) FROM users u JOIN sls s ON s.ppl_id=u.id JOIN users pml ON pml.id=s.pml_id WHERE u.role='ppl'`+pmlFilter+` AND (u.name LIKE ? OR pml.name LIKE ?)`, countArgs...).Scan(&total)
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, adminPPLSortCols, "pml.name, u.name")

	rows, err := db.DB.Query(`
		SELECT u.id, u.name, pml.name,
		       COUNT(s.id),
		       COALESCE(SUM(p.fasih_submitted),0),
		       COALESCE(SUM(p.jumlah_submit),0),
		       COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0),
		       COALESCE(SUM(p.fasih_rejected_pengawas),0),
		       COALESCE(SUM(p.fasih_approved_kabupaten),0),
		       COALESCE(SUM(p.fasih_rejected_kabupaten),0),
		       COALESCE(SUM(p.fasih_approved_provinsi),0),
		       COALESCE(SUM(p.fasih_rejected_provinsi),0),
		       COALESCE(SUM(p.fasih_approved_pusat),0),
		       COALESCE(SUM(p.fasih_rejected_pusat),0),
		       COALESCE(SUM(p.fasih_total),0)
		FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE u.role = 'ppl'`+pmlFilter+` AND (u.name LIKE ? OR pml.name LIKE ?)
		GROUP BY u.id, u.name, pml.name
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/ppl", "admin-ppl-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var ppls []PPLRow
	for rows.Next() {
		var r PPLRow
		rows.Scan(&r.ID, &r.Name, &r.PMLName, &r.JmlSLS,
			&r.Submit, &r.JumlahSubmit, &r.Draft,
			&r.ApprovedPengawas, &r.RejectedPengawas,
			&r.ApprovedKabupaten, &r.RejectedKabupaten,
			&r.ApprovedProvinsi, &r.RejectedProvinsi,
			&r.ApprovedPusat, &r.RejectedPusat,
			&r.FasihTotal)
		// Isi alias lama
		r.Diperiksa = r.ApprovedPengawas
		r.Error = r.RejectedPengawas
		if r.FasihTotal > 0 {
			r.PctSubmit = math.Min(float64(r.JumlahSubmit)*100/float64(r.FasihTotal), 100)
			if r.PctSubmit > 100 {
				r.PctSubmit = 100.0
			}
		}
		ppls = append(ppls, r)
	}
	return ppls, pageInfo
}

var adminSLSSortCols = map[string]string{
	"kode_sls": "s.kode_sls",
	"nama_sls": "s.nama_sls",
	"ppl":      "ppl.name",
	"pml":      "pml.name",
	"lokasi":   "s.nama_kec, s.nama_desa",
	"total":    "COALESCE(p.fasih_total,0)",
	"submit":   "COALESCE(p.fasih_submitted,0)",
	"draft":    "COALESCE(p.jumlah_draft,0)",
	"approved": "COALESCE(p.fasih_approved_pengawas,0)",
	"rejected": "COALESCE(p.fasih_rejected_pengawas,0)",
	"progres":  "(CASE WHEN COALESCE(p.fasih_total,0)=0 THEN 0 ELSE COALESCE(p.jumlah_submit,0)/p.fasih_total END)",
}

func queryAdminSLS(page int, q, sort, dir, metode string) ([]SLSAdminRow, models.PageInfo) {
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
		extra = fmt.Sprintf("&q=%s", q)
	}
	if metode != MetodeTotalVsTotal {
		extra += "&metode=" + metode
	}
	sortCols := make(map[string]string, len(adminSLSSortCols))
	for k, v := range adminSLSSortCols {
		sortCols[k] = v
	}
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(p.jumlah_submit,0)", "COALESCE(p.fasih_total,0)", "s.target")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "s.kode_kec, s.kode_desa, s.kode_sls")

	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.id, s.kode_sls, s.nama_sls,
		       ppl.name, pml.name,
		       COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''), s.target,
		       COALESCE(p.fasih_submitted,0), COALESCE(p.jumlah_submit,0),
		       COALESCE(p.jumlah_draft,0),
		       COALESCE(p.fasih_approved_pengawas,0), COALESCE(p.fasih_rejected_pengawas,0),
		       COALESCE((SELECT SUM(vh.jumlah_observasi) FROM verifikasi_harian vh WHERE vh.sls_id=s.id),0),
		       COALESCE(p.fasih_total,0),
		       COALESCE((SELECT vh2.status_kendala FROM verifikasi_harian vh2 WHERE vh2.sls_id=s.id ORDER BY vh2.tanggal DESC LIMIT 1),'open'),
		       COALESCE((SELECT vh3.kendala FROM verifikasi_harian vh3 WHERE vh3.sls_id=s.id ORDER BY vh3.tanggal DESC LIMIT 1),'')
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
		  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
		`+orderBy+`
		LIMIT ? OFFSET ?`,
		like, like, like, like, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/sls", "admin-sls-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []SLSAdminRow
	for rows.Next() {
		var r SLSAdminRow
		rows.Scan(&r.ID, &r.KodeSLS, &r.NamaSLS, &r.NamaPPL, &r.NamaPML,
			&r.NamaKec, &r.NamaDesa, &r.Target,
			&r.FasihSubmit, &r.JumlahSubmit, &r.JumlahDraft,
			&r.JumlahDiperiksa, &r.JumlahError, &r.JumlahObservasi,
			&r.FasihTotal, &r.StatusKendala, &r.Kendala)
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.Target)
		list = append(list, r)
	}
	return list, pageInfo
}

var adminDesaSortCols = map[string]string{
	"nama_desa": "s.nama_desa",
	"nama_kec":  "s.nama_kec",
	"jml_sls":   "COUNT(DISTINCT s.id)",
	"total":     "COALESCE(SUM(p.fasih_total),0)",
	"submit":    "COALESCE(SUM(p.fasih_submitted),0)",
	"draft":     "COALESCE(SUM(p.jumlah_draft),0)",
	"approved":  "COALESCE(SUM(p.fasih_approved_pengawas),0)",
	"rejected":  "COALESCE(SUM(p.fasih_rejected_pengawas),0)",
	"progres":   "(CASE WHEN COALESCE(SUM(p.fasih_total),0)=0 THEN 0 ELSE COALESCE(SUM(p.jumlah_submit),0)/SUM(p.fasih_total) END)",
}

func queryAdminSLSByDesa(page int, q, sort, dir, metode string) ([]DesaRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	extra += "&level=desa"
	if metode != MetodeTotalVsTotal {
		extra += "&metode=" + metode
	}
	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT CONCAT(s.nama_desa,'|',s.nama_kec)) FROM sls s
		WHERE s.nama_desa LIKE ? OR s.nama_kec LIKE ?`, like, like).Scan(&total)
	sortCols := make(map[string]string, len(adminDesaSortCols))
	for k, v := range adminDesaSortCols {
		sortCols[k] = v
	}
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(SUM(p.jumlah_submit),0)", "COALESCE(SUM(p.fasih_total),0)", "COALESCE(SUM(s.target),0)")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "s.kode_kec, s.kode_desa")
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.nama_desa, s.nama_kec,
		       COUNT(DISTINCT s.id),
		       COALESCE(SUM(s.target),0),
		       COALESCE(SUM(p.fasih_submitted),0),
		       COALESCE(SUM(p.jumlah_submit),0),
		       COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0),
		       COALESCE(SUM(p.fasih_rejected_pengawas),0),
		       COALESCE(SUM(p.fasih_total),0)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.nama_desa LIKE ? OR s.nama_kec LIKE ?
		GROUP BY s.nama_desa, s.nama_kec, s.kode_desa, s.kode_kec
		`+orderBy+`
		LIMIT ? OFFSET ?`, like, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/sls", "admin-sls-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()
	var list []DesaRow
	for rows.Next() {
		var r DesaRow
		rows.Scan(&r.NamaDesa, &r.NamaKec, &r.JmlSLS, &r.Target,
			&r.FasihSubmit, &r.JumlahSubmit, &r.JumlahDraft, &r.JumlahDiperiksa, &r.JumlahError, &r.FasihTotal)
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.Target)
		list = append(list, r)
	}
	return list, pageInfo
}

var adminKecSortCols = map[string]string{
	"nama_kec": "s.nama_kec",
	"jml_sls":  "COUNT(DISTINCT s.id)",
	"total":    "COALESCE(SUM(p.fasih_total),0)",
	"submit":   "COALESCE(SUM(p.fasih_submitted),0)",
	"draft":    "COALESCE(SUM(p.jumlah_draft),0)",
	"approved": "COALESCE(SUM(p.fasih_approved_pengawas),0)",
	"rejected": "COALESCE(SUM(p.fasih_rejected_pengawas),0)",
	"progres":  "(CASE WHEN COALESCE(SUM(p.fasih_total),0)=0 THEN 0 ELSE COALESCE(SUM(p.jumlah_submit),0)/SUM(p.fasih_total) END)",
}

func queryAdminSLSByKec(page int, q, sort, dir, metode string) ([]KecRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := "&level=kec"
	if q != "" {
		extra = "&q=" + q + "&level=kec"
	}
	if metode != MetodeTotalVsTotal {
		extra += "&metode=" + metode
	}
	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT s.nama_kec) FROM sls s WHERE s.nama_kec LIKE ?`, like).Scan(&total)
	sortCols := make(map[string]string, len(adminKecSortCols))
	for k, v := range adminKecSortCols {
		sortCols[k] = v
	}
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(SUM(p.jumlah_submit),0)", "COALESCE(SUM(p.fasih_total),0)", "COALESCE(SUM(s.target),0)")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "s.kode_kec")
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.nama_kec,
		       COUNT(DISTINCT s.id),
		       COALESCE(SUM(s.target),0),
		       COALESCE(SUM(p.fasih_submitted),0),
		       COALESCE(SUM(p.jumlah_submit),0),
		       COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0),
		       COALESCE(SUM(p.fasih_rejected_pengawas),0),
		       COALESCE(SUM(p.fasih_total),0)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.nama_kec LIKE ?
		GROUP BY s.nama_kec, s.kode_kec
		`+orderBy+`
		LIMIT ? OFFSET ?`, like, models.PerPage, offset)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/sls", "admin-sls-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()
	var list []KecRow
	for rows.Next() {
		var r KecRow
		rows.Scan(&r.NamaKec, &r.JmlSLS, &r.Target,
			&r.FasihSubmit, &r.JumlahSubmit, &r.JumlahDraft, &r.JumlahDiperiksa, &r.JumlahError, &r.FasihTotal)
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.Target)
		list = append(list, r)
	}
	return list, pageInfo
}
