package handlers

import (
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"

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
	TotalDiperiksa     int // = TotalApprovedPengawas
	TotalError         int // = TotalRejectedPengawas
	TotalObservasi     int
	TotalFasihTotal    int
	TotalTargetPrelist int
	PctSubmit          float64
	PctDiperiksa       float64
	PctProgres         float64
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
	TargetPrelist  int
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
	Diperiksa     int // = ApprovedPengawas
	Error         int // = RejectedPengawas
	FasihTotal    int
	TargetPrelist int
	PctSubmit     float64 // "Persentase Muatan": submit/total (metode-aware), data-level
	// "Persentase SLS": dari semua SLS milik PPL ini, berapa persen yang % Progres
	// per-SLS-nya sudah >=95% (metode-aware juga) — metrik selesai di level SLS,
	// bukan level data/muatan.
	PctSLSSelesai float64
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
	TargetPrelist   int // target_prelist_resmi: kuota resmi Rekap Prelist BPS, STABIL (beda dari Target yang ikut ter-overwrite sync FASIH)
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
	TargetPrelist   int // SUM(target_prelist_resmi) — stabil, tidak ter-overwrite sync FASIH
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
	TargetPrelist   int // SUM(target_prelist_resmi) — stabil, tidak ter-overwrite sync FASIH
	FasihSubmit     int // fasih_submitted: pending review PML (kolom Submit)
	JumlahSubmit    int // jumlah_submit: semua status (untuk % progress)
	JumlahDraft     int
	JumlahDiperiksa int
	JumlahError     int
	FasihTotal      int
	PctSubmit       float64
}

// computeAdminSummary menghitung ringkasan kabupaten. metode menentukan cara
// hitung PctProgres (lihat computePctProgres) — dipakai baik untuk render
// halaman penuh maupun untuk refresh partial via AdminSummaryPartial.
func computeAdminSummary(metode string) AdminSummary {
	var s AdminSummary
	db.DB.QueryRow(`
		SELECT
		  (SELECT COUNT(*) FROM sls),
		  (SELECT COALESCE(SUM(target),0) FROM sls),
		  (SELECT COALESCE(SUM(target_prelist_resmi),0) FROM sls),
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
		Scan(&s.TotalSLS, &s.TotalTarget, &s.TotalTargetPrelist, &s.TotalSubmit, &s.TotalFasihSubmit, &s.TotalDraft,
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
	s.PctProgres = computePctProgres(metode, s.TotalSubmit, s.TotalFasihTotal, s.TotalTargetPrelist)
	return s
}

// AdminSummaryPartial merender ulang section Ringkasan sesuai metode % Progres
// yang dipilih di dropdown global (dipanggil via HTMX saat dropdown berubah).
func AdminSummaryPartial(c echo.Context) error {
	metode := normalizeMetode(c.QueryParam("metode"))
	s := computeAdminSummary(metode)
	return c.Render(http.StatusOK, "admin_summary_stats.html", map[string]interface{}{
		"Summary": s,
	})
}

func AdminDashboard(c echo.Context) error {
	s := computeAdminSummary(MetodeTotalVsTotal)

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

	pmls, pmlPage2 := queryAdminPML(pmlPage, "", "", "", MetodeTotalVsTotal)
	ppls, pplPage2 := queryAdminPPL(pplPage, "", 0, "", "", MetodeTotalVsTotal)
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
	metode := normalizeMetode(c.QueryParam("metode"))
	pmls, pageInfo := queryAdminPML(page, q, sort, dir, metode)
	return c.Render(http.StatusOK, "admin_pml_table.html", map[string]interface{}{
		"PMLs": pmls, "PMLPage": pageInfo, "Metode": metode,
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
	metode := normalizeMetode(c.QueryParam("metode"))
	ppls, pageInfo := queryAdminPPL(page, q, pmlID, sort, dir, metode)
	return c.Render(http.StatusOK, "admin_ppl_table.html", map[string]interface{}{
		"PPLs": ppls, "PPLPage": pageInfo, "Metode": metode,
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

func queryAdminPML(page int, q, sort, dir, metode string) ([]PMLRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	if metode != MetodeTotalVsTotal {
		extra += "&metode=" + metode
	}
	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT u.id) FROM users u JOIN sls s ON s.pml_id=u.id WHERE u.role='pml' AND u.name LIKE ?`, like).Scan(&total)
	sortCols := make(map[string]string, len(adminPMLSortCols))
	for k, v := range adminPMLSortCols {
		sortCols[k] = v
	}
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(SUM(p.jumlah_submit),0)", "COALESCE(SUM(p.fasih_total),0)", "COALESCE(SUM(s.target_prelist_resmi),0)")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "u.name")
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
		       COALESCE(SUM(p.fasih_total),0), COALESCE(SUM(s.target_prelist_resmi),0),
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
			&r.FasihTotal, &r.TargetPrelist, &r.Observasi, &r.KendalaTerbuka)
		// Isi alias lama
		r.Diperiksa = r.ApprovedPengawas
		r.Error = r.RejectedPengawas
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.TargetPrelist)
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

func queryAdminPPL(page int, q string, pmlID int, sort, dir, metode string) ([]PPLRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}
	if metode != MetodeTotalVsTotal {
		extra += "&metode=" + metode
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
	sortCols := make(map[string]string, len(adminPPLSortCols))
	for k, v := range adminPPLSortCols {
		sortCols[k] = v
	}
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(SUM(p.jumlah_submit),0)", "COALESCE(SUM(p.fasih_total),0)", "COALESCE(SUM(s.target_prelist_resmi),0)")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "pml.name, u.name")

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
		       COALESCE(SUM(p.fasih_total),0), COALESCE(SUM(s.target_prelist_resmi),0)
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
			&r.FasihTotal, &r.TargetPrelist)
		// Isi alias lama
		r.Diperiksa = r.ApprovedPengawas
		r.Error = r.RejectedPengawas
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.TargetPrelist)
		ppls = append(ppls, r)
	}

	fillPctSLSSelesai(ppls, metode)
	return ppls, pageInfo
}

// slsSelesaiThreshold: ambang batas % Progres per-SLS (metode-aware) supaya SLS
// dianggap "selesai" utk perhitungan "Persentase SLS".
const slsSelesaiThreshold = 0.95

// fillPctSLSSelesai mengisi PctSLSSelesai tiap PPLRow: dari semua SLS milik PPL
// itu, berapa persen yang % Progres per-SLS-nya (metode yang sama dgn dropdown
// global) sudah >= 95%. Query terpisah (bukan bagian agregat utama) karena
// butuh evaluasi per-baris SLS, bukan SUM lintas SLS.
func fillPctSLSSelesai(ppls []PPLRow, metode string) {
	if len(ppls) == 0 {
		return
	}
	byID := make(map[int]*PPLRow, len(ppls))
	placeholders := make([]string, len(ppls))
	args := make([]interface{}, len(ppls))
	for i := range ppls {
		byID[ppls[i].ID] = &ppls[i]
		placeholders[i] = "?"
		args[i] = ppls[i].ID
	}

	pctExpr := progresSortExprGeneric(metode, "COALESCE(p.jumlah_submit,0)", "COALESCE(p.fasih_total,0)", "s.target_prelist_resmi")
	rows, err := db.DB.Query(`
		SELECT s.ppl_id, COUNT(*),
		       SUM(CASE WHEN `+pctExpr+` >= `+strconv.FormatFloat(slsSelesaiThreshold, 'f', -1, 64)+` THEN 1 ELSE 0 END)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.ppl_id IN (`+strings.Join(placeholders, ",")+`)
		GROUP BY s.ppl_id`, args...)
	if err != nil {
		return
	}
	defer rows.Close()
	for rows.Next() {
		var pplID, totalSLS, doneSLS int
		if err := rows.Scan(&pplID, &totalSLS, &doneSLS); err != nil {
			continue
		}
		if r, ok := byID[pplID]; ok && totalSLS > 0 {
			r.PctSLSSelesai = math.Min(float64(doneSLS)*100/float64(totalSLS), 100)
		}
	}
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
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(p.jumlah_submit,0)", "COALESCE(p.fasih_total,0)", "s.target_prelist_resmi")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "s.kode_kec, s.kode_desa, s.kode_sls")

	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.id, s.kode_sls, s.nama_sls,
		       ppl.name, pml.name,
		       COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''), s.target, s.target_prelist_resmi,
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
			&r.NamaKec, &r.NamaDesa, &r.Target, &r.TargetPrelist,
			&r.FasihSubmit, &r.JumlahSubmit, &r.JumlahDraft,
			&r.JumlahDiperiksa, &r.JumlahError, &r.JumlahObservasi,
			&r.FasihTotal, &r.StatusKendala, &r.Kendala)
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.TargetPrelist)
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
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(SUM(p.jumlah_submit),0)", "COALESCE(SUM(p.fasih_total),0)", "COALESCE(SUM(s.target_prelist_resmi),0)")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "s.kode_kec, s.kode_desa")
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.nama_desa, s.nama_kec,
		       COUNT(DISTINCT s.id),
		       COALESCE(SUM(s.target),0),
		       COALESCE(SUM(s.target_prelist_resmi),0),
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
		rows.Scan(&r.NamaDesa, &r.NamaKec, &r.JmlSLS, &r.Target, &r.TargetPrelist,
			&r.FasihSubmit, &r.JumlahSubmit, &r.JumlahDraft, &r.JumlahDiperiksa, &r.JumlahError, &r.FasihTotal)
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.TargetPrelist)
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
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(SUM(p.jumlah_submit),0)", "COALESCE(SUM(p.fasih_total),0)", "COALESCE(SUM(s.target_prelist_resmi),0)")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "s.kode_kec")
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT s.nama_kec,
		       COUNT(DISTINCT s.id),
		       COALESCE(SUM(s.target),0),
		       COALESCE(SUM(s.target_prelist_resmi),0),
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
		rows.Scan(&r.NamaKec, &r.JmlSLS, &r.Target, &r.TargetPrelist,
			&r.FasihSubmit, &r.JumlahSubmit, &r.JumlahDraft, &r.JumlahDiperiksa, &r.JumlahError, &r.FasihTotal)
		r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.TargetPrelist)
		list = append(list, r)
	}
	return list, pageInfo
}

type ProgresRekapRow struct {
	ID           int
	NamaSLS      string
	NamaKec      string
	NamaDesa     string
	NamaPPL      string
	NamaPML      string
	Prioritas    bool
	FasihTotal   int
	FasihSubmit  int // fasih_submitted: pending review PML (kolom Submit)
	JumlahSubmit int // jumlah_submit: semua status (untuk % progress)
	JumlahDraft  int
	Diperiksa    int // = fasih_approved_pengawas
	Error        int // = fasih_rejected_pengawas
	// Breakdown verifikasi per level lain (dipakai utk PctTerverifikasi, sama seperti Per PML)
	RevokedPengawas   int
	ApprovedKabupaten int
	RejectedKabupaten int
	ApprovedProvinsi  int
	RejectedProvinsi  int
	ApprovedPusat     int
	RejectedPusat     int
	TargetPrelist     int
	PctSubmit         float64
	// Terverifikasi = semua status kecuali open, submit, draft (approved+rejected+revoked di semua level)
	Terverifikasi    int
	PctTerverifikasi float64
	// Coverage usaha & keluarga (ditemukan/baru/prelist) dari Dashboard SE2026,
	// kode_indikator -> jumlah. Lihat queryCoverageIndikatorList utk daftar kolomnya.
	Coverage map[string]int
	// Persentase coverage: (Ditemukan+Baru)/Prelist*100 per kategori.
	PctCoverageUsahaBKU      float64
	PctCoverageUsahaKeluarga float64
	PctCoverageKeluarga      float64
}

// Kode indikator coverage_usaha_keluarga yang dipakai utk hitung % coverage per SLS.
const (
	kodeCovUsahaPrelist      = "2"
	kodeCovUsahaDitemukan    = "10264"
	kodeCovUsahaBaru         = "10268"
	kodeCovUsahaKelPrelist   = "90001"
	kodeCovUsahaKelDitemukan = "10691"
	kodeCovUsahaKelBaru      = "10696"
	kodeCovKeluargaPrelist   = "14"
	kodeCovKeluargaDitemukan = "15"
	kodeCovKeluargaBaru      = "20"
)

// pctCoverage menghitung (ditemukan+baru)/prelist*100, dibatasi maks 100.
func pctCoverage(cov map[string]int, ditemukanKode, baruKode, prelistKode string) float64 {
	prelist := cov[prelistKode]
	if prelist <= 0 {
		return 0
	}
	return math.Min(float64(cov[ditemukanKode]+cov[baruKode])*100/float64(prelist), 100)
}

// queryCoverageIndikatorList mengambil daftar indikator coverage usaha & keluarga
// yang sudah ada datanya (pola sama seperti queryKBLIIndikatorList di kbli.go).
func queryCoverageIndikatorList() []KBLIIndikator {
	rows, err := db.DB.Query(`SELECT DISTINCT kode_indikator, nama_indikator FROM coverage_usaha_keluarga`)
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

var progresRekapSortCols = map[string]string{
	"lokasi":   "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas":  "ppl.name",
	"total":    "COALESCE(p.fasih_total,0)",
	"submit":   "COALESCE(p.fasih_submitted,0)",
	"draft":    "COALESCE(p.jumlah_draft,0)",
	"approved": "COALESCE(p.fasih_approved_pengawas,0)",
	"rejected": "COALESCE(p.fasih_rejected_pengawas,0)",
	"progres":  "(CASE WHEN COALESCE(p.fasih_total,0)=0 THEN 0 ELSE COALESCE(p.jumlah_submit,0)/p.fasih_total END)",
	"verifikasi": "(CASE WHEN COALESCE(p.jumlah_submit,0)=0 THEN 0 ELSE " +
		"(COALESCE(p.fasih_approved_pengawas,0)+COALESCE(p.fasih_rejected_pengawas,0)+COALESCE(p.fasih_revoked_pengawas,0)+" +
		"COALESCE(p.fasih_approved_kabupaten,0)+COALESCE(p.fasih_rejected_kabupaten,0)+" +
		"COALESCE(p.fasih_approved_provinsi,0)+COALESCE(p.fasih_rejected_provinsi,0)+" +
		"COALESCE(p.fasih_approved_pusat,0)+COALESCE(p.fasih_rejected_pusat,0)) / p.jumlah_submit END)",
}

// AdminProgresRekapTable — GET /admin/table/progres-rekap
// Rekap progres (data tabel progress) per SLS, dengan filter PML/PPL/SLS/prioritas
// yang sama seperti rekap keberadaan sebelumnya. % Progres mengikuti metode global.
func AdminProgresRekapTable(c echo.Context) error {
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
	metode := normalizeMetode(c.QueryParam("metode"))
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
	if metode != MetodeTotalVsTotal {
		extra += "&metode=" + metode
	}

	sortCols := make(map[string]string, len(progresRekapSortCols))
	for k, v := range progresRekapSortCols {
		sortCols[k] = v
	}
	sortCols["progres"] = progresSortExprGeneric(metode, "COALESCE(p.jumlah_submit,0)", "COALESCE(p.fasih_total,0)", "s.target_prelist_resmi")
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "s.nama_kec, s.nama_desa, s.nama_sls")

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/progres-rekap", "progres-rekap-result", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT s.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name, s.prioritas,
		       COALESCE(p.fasih_total,0), COALESCE(p.fasih_submitted,0), COALESCE(p.jumlah_submit,0),
		       COALESCE(p.jumlah_draft,0), COALESCE(p.fasih_approved_pengawas,0), COALESCE(p.fasih_rejected_pengawas,0),
		       COALESCE(p.fasih_revoked_pengawas,0),
		       COALESCE(p.fasih_approved_kabupaten,0), COALESCE(p.fasih_rejected_kabupaten,0),
		       COALESCE(p.fasih_approved_provinsi,0), COALESCE(p.fasih_rejected_provinsi,0),
		       COALESCE(p.fasih_approved_pusat,0), COALESCE(p.fasih_rejected_pusat,0),
		       s.target_prelist_resmi
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN progress p ON p.sls_id = s.id`+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []ProgresRekapRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r ProgresRekapRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML, &r.Prioritas,
				&r.FasihTotal, &r.FasihSubmit, &r.JumlahSubmit, &r.JumlahDraft, &r.Diperiksa, &r.Error,
				&r.RevokedPengawas, &r.ApprovedKabupaten, &r.RejectedKabupaten,
				&r.ApprovedProvinsi, &r.RejectedProvinsi, &r.ApprovedPusat, &r.RejectedPusat,
				&r.TargetPrelist)
			r.PctSubmit = computePctProgres(metode, r.JumlahSubmit, r.FasihTotal, r.TargetPrelist)
			r.Terverifikasi = r.Diperiksa + r.Error + r.RevokedPengawas +
				r.ApprovedKabupaten + r.RejectedKabupaten +
				r.ApprovedProvinsi + r.RejectedProvinsi +
				r.ApprovedPusat + r.RejectedPusat
			if r.JumlahSubmit > 0 {
				r.PctTerverifikasi = math.Min(float64(r.Terverifikasi)*100/float64(r.JumlahSubmit), 100)
			}
			list = append(list, r)
		}
	}

	coverageIndikators := queryCoverageIndikatorList()
	if len(list) > 0 {
		placeholders := make([]string, len(list))
		covArgs := make([]interface{}, len(list))
		byID := make(map[int]*ProgresRekapRow, len(list))
		for i := range list {
			list[i].Coverage = map[string]int{}
			byID[list[i].ID] = &list[i]
			placeholders[i] = "?"
			covArgs[i] = list[i].ID
		}
		covRows, err := db.DB.Query(`
			SELECT sls_id, kode_indikator, COALESCE(total_value,0)
			FROM coverage_usaha_keluarga
			WHERE sls_id IN (`+strings.Join(placeholders, ",")+`)`, covArgs...)
		if err == nil {
			defer covRows.Close()
			for covRows.Next() {
				var slsID2 int
				var kode string
				var val int
				covRows.Scan(&slsID2, &kode, &val)
				if r, ok := byID[slsID2]; ok {
					r.Coverage[kode] = val
				}
			}
		}
		for i := range list {
			r := &list[i]
			r.PctCoverageUsahaBKU = pctCoverage(r.Coverage, kodeCovUsahaDitemukan, kodeCovUsahaBaru, kodeCovUsahaPrelist)
			r.PctCoverageUsahaKeluarga = pctCoverage(r.Coverage, kodeCovUsahaKelDitemukan, kodeCovUsahaKelBaru, kodeCovUsahaKelPrelist)
			r.PctCoverageKeluarga = pctCoverage(r.Coverage, kodeCovKeluargaDitemukan, kodeCovKeluargaBaru, kodeCovKeluargaPrelist)
		}
	}

	pplSelect := OOBSelect{
		TargetID: "progresrekap-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(nil, pmlID), Selected: pplID,
		HxGet: "/admin/table/progres-rekap", HxTarget: "#progres-rekap-result", HxInclude: "#progres-rekap-filter-bar, #global-metode-select",
	}
	slsSelect := OOBSelect{
		TargetID: "progresrekap-sls-select", Name: "sls_id", Placeholder: "Semua SLS",
		Options: querySLSOptionsByFilter(nil, pmlID, pplID), Selected: slsID,
		HxGet: "/admin/table/progres-rekap", HxTarget: "#progres-rekap-result", HxInclude: "#progres-rekap-filter-bar, #global-metode-select",
	}

	return c.Render(http.StatusOK, "admin_progres_rekap_table.html", map[string]interface{}{
		"Rows":               list,
		"PageInfo":           pageInfo,
		"Q":                  q,
		"PmlID":              pmlID,
		"PplID":              pplID,
		"SlsID":              slsID,
		"PrioritasOnly":      prioritasOnly,
		"Metode":             metode,
		"CoverageIndikators": coverageIndikators,
		"PPLSelect":          pplSelect,
		"SLSSelect":          slsSelect,
	})
}
