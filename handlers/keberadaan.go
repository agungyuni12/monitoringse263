package handlers

import (
	"fmt"
	"net/http"
	"strconv"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"
)

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
