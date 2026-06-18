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
	list, pageInfo := queryAdminOrganik(page, q)
	return c.Render(http.StatusOK, "admin_organik_table.html", map[string]interface{}{
		"OrganikRows": list,
		"OrganikPage": pageInfo,
		"Q":           q,
	})
}

func queryAdminOrganik(page int, q string) ([]AdminOrganikRow, models.PageInfo) {
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
		ORDER BY lo.tanggal DESC, lo.id DESC
		LIMIT ? OFFSET ?`,
		like, like, like, like, like, models.PerPage, offset)
	if err != nil {
		return nil, models.NewPageInfo(page, total, "/admin/table/organik", "admin-organik-wrap", extra)
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
	return list, models.NewPageInfo(page, total, "/admin/table/organik", "admin-organik-wrap", extra)
}
