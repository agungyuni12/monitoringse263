package handlers

import (
	"fmt"
	"net/http"
	"strconv"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

type AnomaliRow struct {
	ID       int
	NamaSLS  string
	NamaKec  string
	NamaDesa string
	NamaPPL  string
	NamaPML  string
	Nama     string // nama usaha/KRT
	Jenis    string // short_label
	RuleKey  string
	RuleMsg  string
	SyncedAt string
}

type PPLUser struct {
	ID   int
	Name string
}

func queryPPLUsers() []PPLUser {
	rows, err := db.DB.Query(`SELECT u.id, u.name FROM users u JOIN sls s ON s.ppl_id=u.id WHERE u.role='ppl' GROUP BY u.id, u.name ORDER BY u.name`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []PPLUser
	for rows.Next() {
		var p PPLUser
		rows.Scan(&p.ID, &p.Name)
		list = append(list, p)
	}
	return list
}

func queryKecList() []string {
	rows, err := db.DB.Query(`SELECT DISTINCT nama_kec FROM sls WHERE nama_kec != '' ORDER BY nama_kec`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []string
	for rows.Next() {
		var kec string
		rows.Scan(&kec)
		list = append(list, kec)
	}
	return list
}

func querySkalaList() []string {
	rows, err := db.DB.Query(`SELECT DISTINCT skala_usaha FROM keberadaan_usaha WHERE skala_usaha != '' ORDER BY skala_usaha`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []string
	for rows.Next() {
		var s string
		rows.Scan(&s)
		list = append(list, s)
	}
	return list
}

func queryAnomaili(page int, q, kec string, pmlID, pplID int, targetID, baseURL string) ([]AnomaliRow, models.PageInfo) {
	like := "%" + q + "%"

	// Build WHERE clause
	where := " WHERE (a.nama LIKE ? OR a.jenis LIKE ? OR a.rule_msg LIKE ? OR s.nama_sls LIKE ?)"
	args := []interface{}{like, like, like, like}

	if kec != "" {
		where += " AND s.nama_kec = ?"
		args = append(args, kec)
	}
	if pmlID > 0 {
		where += " AND s.pml_id = ?"
		args = append(args, pmlID)
	}
	if pplID > 0 {
		where += " AND s.ppl_id = ?"
		args = append(args, pplID)
	}

	var total int
	countArgs := make([]interface{}, len(args))
	copy(countArgs, args)
	db.DB.QueryRow(`SELECT COUNT(*) FROM anomali a JOIN sls s ON s.id=a.sls_id`+where, countArgs...).Scan(&total)

	// Build extra for pagination
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

	offset := (page - 1) * models.PerPage
	queryArgs := make([]interface{}, len(args))
	copy(queryArgs, args)
	queryArgs = append(queryArgs, models.PerPage, offset)

	rows, err := db.DB.Query(`
		SELECT a.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       a.nama, a.jenis, a.rule_key, COALESCE(a.rule_msg,''),
		       COALESCE(DATE_FORMAT(a.synced_at,'%d/%m/%Y %H:%i'),'')
		FROM anomali a
		JOIN sls s ON s.id = a.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+
		where+`
		ORDER BY s.nama_kec, s.nama_desa, s.nama_sls, a.rule_key
		LIMIT ? OFFSET ?`, queryArgs...)

	pageInfo := models.NewPageInfo(page, total, baseURL, targetID, extra)
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []AnomaliRow
	for rows.Next() {
		var r AnomaliRow
		rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
			&r.NamaPPL, &r.NamaPML,
			&r.Nama, &r.Jenis, &r.RuleKey, &r.RuleMsg, &r.SyncedAt)
		list = append(list, r)
	}
	return list, pageInfo
}

// AdminAnomaliTable — GET /admin/table/anomali
func AdminAnomaliTable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	kec := c.QueryParam("kec")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))

	list, pageInfo := queryAnomaili(page, q, kec, pmlID, pplID, "anomali-result", "/admin/table/anomali")

	var kecs []string
	if kec != "" {
		kecs = []string{kec}
	}
	pmlSelect := OOBSelect{
		TargetID: "anomali-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/anomali", HxTarget: "#anomali-result", HxInclude: "#anomali-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "anomali-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/anomali", HxTarget: "#anomali-result", HxInclude: "#anomali-filter-bar",
	}

	return c.Render(http.StatusOK, "anomali_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
	})
}

// PPLAnomali — GET /ppl/anomali
func PPLAnomali(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q     := c.QueryParam("q")
	jenis := c.QueryParam("jenis")
	slsID, _ := strconv.Atoi(c.QueryParam("sls_id"))
	like  := "%" + q + "%"

	where := `WHERE s.ppl_id=? AND (a.nama LIKE ? OR a.jenis LIKE ? OR a.rule_msg LIKE ? OR s.nama_sls LIKE ?)`
	args  := []interface{}{userID, like, like, like, like}
	if jenis != "" {
		where += ` AND a.jenis = ?`
		args = append(args, jenis)
	}
	if slsID > 0 {
		where += ` AND s.id = ?`
		args = append(args, slsID)
	}

	var total int
	countArgs := append([]interface{}{}, args...)
	db.DB.QueryRow(`SELECT COUNT(*) FROM anomali a JOIN sls s ON s.id=a.sls_id `+where, countArgs...).Scan(&total)

	extra := ""
	if q != "" { extra += "&q=" + q }
	if jenis != "" { extra += "&jenis=" + jenis }
	if slsID > 0 { extra += fmt.Sprintf("&sls_id=%d", slsID) }

	offset  := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/ppl/anomali", "ppl-anomali-result", extra)

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT a.id, s.nama_sls, a.nama, a.jenis, a.rule_key,
		       COALESCE(a.rule_msg,''),
		       COALESCE(DATE_FORMAT(a.synced_at,'%d/%m/%Y %H:%i'),'')
		FROM anomali a JOIN sls s ON s.id=a.sls_id `+where+`
		ORDER BY s.nama_sls, a.jenis, a.rule_key
		LIMIT ? OFFSET ?`, queryArgs...)

	type Row struct {
		ID       int
		NamaSLS  string
		Nama     string
		Jenis    string
		RuleKey  string
		RuleMsg  string
		SyncedAt string
	}
	var list []Row
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r Row
			rows.Scan(&r.ID, &r.NamaSLS, &r.Nama, &r.Jenis, &r.RuleKey, &r.RuleMsg, &r.SyncedAt)
			list = append(list, r)
		}
	}
	return c.Render(http.StatusOK, "ppl_anomali.html", map[string]interface{}{
		"Rows": list, "PageInfo": pageInfo, "Q": q, "Jenis": jenis, "SlsID": slsID, "Total": total,
	})
}

// PMLAnomali — GET /pml/anomali
func PMLAnomali(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q     := c.QueryParam("q")
	jenis := c.QueryParam("jenis")
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like  := "%" + q + "%"

	where := `WHERE s.pml_id=? AND (a.nama LIKE ? OR a.jenis LIKE ? OR a.rule_msg LIKE ? OR s.nama_sls LIKE ?)`
	args  := []interface{}{userID, like, like, like, like}
	if jenis != "" {
		where += ` AND a.jenis = ?`
		args = append(args, jenis)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}

	var total int
	countArgs := append([]interface{}{}, args...)
	db.DB.QueryRow(`SELECT COUNT(*) FROM anomali a JOIN sls s ON s.id=a.sls_id `+where, countArgs...).Scan(&total)

	extra := ""
	if q != "" { extra += "&q=" + q }
	if jenis != "" { extra += "&jenis=" + jenis }
	if pplID > 0 { extra += fmt.Sprintf("&ppl_id=%d", pplID) }

	offset  := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/pml/anomali", "pml-anomali-result", extra)

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT a.id, s.nama_sls, ppl.name,
		       a.nama, a.jenis, a.rule_key,
		       COALESCE(a.rule_msg,''),
		       COALESCE(DATE_FORMAT(a.synced_at,'%d/%m/%Y %H:%i'),'')
		FROM anomali a
		JOIN sls s ON s.id=a.sls_id
		JOIN users ppl ON ppl.id=s.ppl_id `+where+`
		ORDER BY ppl.name, s.nama_sls, a.jenis, a.rule_key
		LIMIT ? OFFSET ?`, queryArgs...)

	type PMLAnomaliRow struct {
		ID       int
		NamaSLS  string
		NamaPPL  string
		Nama     string
		Jenis    string
		RuleKey  string
		RuleMsg  string
		SyncedAt string
	}
	var list []PMLAnomaliRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r PMLAnomaliRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaPPL, &r.Nama, &r.Jenis, &r.RuleKey, &r.RuleMsg, &r.SyncedAt)
			list = append(list, r)
		}
	}
	return c.Render(http.StatusOK, "pml_anomali.html", map[string]interface{}{
		"Rows": list, "PageInfo": pageInfo, "Q": q, "Jenis": jenis, "PplID": pplID, "Total": total,
	})
}
