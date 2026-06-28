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

type PMLModalData struct {
	SP         models.SLSProgress
	Today      models.VerifikasiHarian
	HasToday   bool
	History    []models.VerifikasiHarian
	StatusOpts []models.StatusLabel
}

// verifCols: submit/draft/diperiksa/error dari progress (FASIH), observasi dari verifikasi_harian
const verifCols = `
	s.id, s.kode_sls, s.nama_sls, s.target,
	COALESCE(s.kode_kec,''), COALESCE(s.nama_kec,''),
	COALESCE(s.kode_desa,''), COALESCE(s.nama_desa,''),
	u.name,
	COALESCE(p.jumlah_submit,0),
	COALESCE(p.jumlah_draft,0),
	COALESCE(p.fasih_approved_pengawas,0),
	COALESCE(p.fasih_rejected_pengawas,0),
	COALESCE((SELECT SUM(vh2.jumlah_observasi) FROM verifikasi_harian vh2 WHERE vh2.sls_id=s.id),0),
	COALESCE((SELECT vh2.kendala FROM verifikasi_harian vh2 WHERE vh2.sls_id=s.id ORDER BY vh2.tanggal DESC LIMIT 1),''),
	COALESCE((SELECT vh2.solusi_sementara FROM verifikasi_harian vh2 WHERE vh2.sls_id=s.id ORDER BY vh2.tanggal DESC LIMIT 1),''),
	COALESCE((SELECT vh2.status_kendala FROM verifikasi_harian vh2 WHERE vh2.sls_id=s.id ORDER BY vh2.tanggal DESC LIMIT 1),'open'),
	COALESCE((SELECT vh2.tindak_lanjut_pml FROM verifikasi_harian vh2 WHERE vh2.sls_id=s.id ORDER BY vh2.tanggal DESC LIMIT 1),'')`

func pmlQueryList(userID, page int) ([]models.SLSProgress, error) {
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT `+verifCols+`
		FROM sls s
		JOIN users u ON u.id = s.ppl_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.pml_id = ?
		ORDER BY s.kode_kec, s.kode_desa, u.name, s.kode_sls
		LIMIT ? OFFSET ?`, userID, models.PerPage, offset)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var list []models.SLSProgress
	for rows.Next() {
		var sp models.SLSProgress
		rows.Scan(
			&sp.SLS.ID, &sp.KodeSLS, &sp.NamaSLS, &sp.Target,
			&sp.KodeKec, &sp.NamaKec, &sp.KodeDesa, &sp.NamaDesa,
			&sp.NamaPPL,
			&sp.JumlahSubmit, &sp.JumlahDraft,
			&sp.JumlahDiperiksa, &sp.JumlahError, &sp.JumlahObservasi,
			&sp.Kendala, &sp.SolusiSementara,
			&sp.StatusKendala, &sp.TindakLanjutPML,
		)
		list = append(list, sp)
	}
	return list, nil
}

func PMLDashboard(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}

	var totalRow int
	db.DB.QueryRow("SELECT COUNT(*) FROM sls WHERE pml_id=?", userID).Scan(&totalRow)

	list, err := pmlQueryList(userID, page)
	if err != nil {
		return err
	}

	var totSubmit, totDiperiksa, totError, totObs int
	db.DB.QueryRow(`
		SELECT
		  COALESCE(SUM(p.jumlah_submit),0),
		  COALESCE(SUM(p.fasih_approved_pengawas),0),
		  COALESCE(SUM(p.fasih_rejected_pengawas),0),
		  COALESCE((SELECT SUM(jumlah_observasi) FROM verifikasi_harian vh WHERE vh.sls_id IN (SELECT id FROM sls WHERE pml_id=?)),0)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.pml_id=?`, userID, userID,
	).Scan(&totSubmit, &totDiperiksa, &totError, &totObs)

	pageInfo := models.NewPageInfo(page, totalRow, "/pml/table", "pml-table-wrap", "")

	// Jenis anomali unik untuk dropdown filter
	jenisRows, _ := db.DB.Query(
		`SELECT DISTINCT a.jenis FROM anomali a JOIN sls s ON s.id=a.sls_id WHERE s.pml_id=? AND a.jenis!='' ORDER BY a.jenis`, userID)
	var jenisList []string
	if jenisRows != nil {
		for jenisRows.Next() {
			var j string
			jenisRows.Scan(&j)
			jenisList = append(jenisList, j)
		}
		jenisRows.Close()
	}
	// PPL di bawah PML ini untuk dropdown filter
	pplRows, _ := db.DB.Query(
		`SELECT u.id, u.name FROM users u JOIN sls s ON s.ppl_id=u.id WHERE s.pml_id=? GROUP BY u.id, u.name ORDER BY u.name`, userID)
	type PplOpt struct{ ID int; Name string }
	var pplList []PplOpt
	if pplRows != nil {
		for pplRows.Next() {
			var p PplOpt
			pplRows.Scan(&p.ID, &p.Name)
			pplList = append(pplList, p)
		}
		pplRows.Close()
	}

	return c.Render(http.StatusOK, "pml.html", map[string]interface{}{
		"Name":         mw.SessionName(c),
		"List":         list,
		"Page":         pageInfo,
		"TotSubmit":    totSubmit,
		"TotDiperiksa": totDiperiksa,
		"TotError":     totError,
		"TotObs":       totObs,
		"StatusOpts":   models.StatusOptions,
		"JenisList":    jenisList,
		"PPLList":      pplList,
	})
}

func PMLTable(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	var totalRow int
	db.DB.QueryRow("SELECT COUNT(*) FROM sls WHERE pml_id=?", userID).Scan(&totalRow)

	list, err := pmlQueryList(userID, page)
	if err != nil {
		return err
	}
	pageInfo := models.NewPageInfo(page, totalRow, "/pml/table", "pml-table-wrap", "")

	return c.Render(http.StatusOK, "pml_table.html", map[string]interface{}{
		"List":       list,
		"Page":       pageInfo,
		"StatusOpts": models.StatusOptions,
	})
}

func PMLVerifModal(c echo.Context) error {
	slsID, _ := strconv.Atoi(c.Param("id"))
	userID := mw.SessionUserID(c)
	todayStr := time.Now().Format("2006-01-02")

	var count int
	db.DB.QueryRow("SELECT COUNT(*) FROM sls WHERE id=? AND pml_id=?", slsID, userID).Scan(&count)
	if count == 0 {
		return c.String(http.StatusNotFound, "SLS tidak ditemukan")
	}

	var sp models.SLSProgress
	db.DB.QueryRow(`
		SELECT `+verifCols+`
		FROM sls s
		JOIN users u ON u.id = s.ppl_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.id = ?`, slsID).
		Scan(
			&sp.SLS.ID, &sp.KodeSLS, &sp.NamaSLS, &sp.Target,
			&sp.KodeKec, &sp.NamaKec, &sp.KodeDesa, &sp.NamaDesa,
			&sp.NamaPPL,
			&sp.JumlahSubmit, &sp.JumlahDraft,
			&sp.JumlahDiperiksa, &sp.JumlahError, &sp.JumlahObservasi,
			&sp.Kendala, &sp.SolusiSementara,
			&sp.StatusKendala, &sp.TindakLanjutPML,
		)

	data := PMLModalData{
		SP:         sp,
		StatusOpts: models.StatusOptions,
		Today:      models.VerifikasiHarian{Tanggal: todayStr, StatusKendala: "open"},
	}

	var todayVH models.VerifikasiHarian
	err := db.DB.QueryRow(`
		SELECT id, COALESCE(status_kendala,'open'), COALESCE(tindak_lanjut_pml,''),
		       COALESCE(kendala,''), COALESCE(solusi_sementara,'')
		FROM verifikasi_harian WHERE sls_id=? AND tanggal=?`, slsID, todayStr).
		Scan(&todayVH.ID, &todayVH.StatusKendala, &todayVH.TindakLanjutPML,
			&todayVH.Kendala, &todayVH.SolusiSementara)
	if err == nil {
		todayVH.Tanggal = todayStr
		data.Today = todayVH
		data.HasToday = true
	}

	rows, _ := db.DB.Query(`
		SELECT tanggal, status_kendala, COALESCE(tindak_lanjut_pml,''),
		       COALESCE(kendala,''), COALESCE(solusi_sementara,'')
		FROM verifikasi_harian WHERE sls_id=? AND tanggal != ?
		ORDER BY tanggal DESC LIMIT 10`, slsID, todayStr)
	defer rows.Close()
	for rows.Next() {
		var vh models.VerifikasiHarian
		rows.Scan(&vh.Tanggal, &vh.StatusKendala, &vh.TindakLanjutPML,
			&vh.Kendala, &vh.SolusiSementara)
		data.History = append(data.History, vh)
	}

	return c.Render(http.StatusOK, "pml_modal.html", data)
}

func PMLSaveVerif(c echo.Context) error {
	slsID, _ := strconv.Atoi(c.Param("id"))
	userID := mw.SessionUserID(c)

	var count int
	db.DB.QueryRow("SELECT COUNT(*) FROM sls WHERE id=? AND pml_id=?", slsID, userID).Scan(&count)
	if count == 0 {
		return c.String(http.StatusForbidden, "Akses ditolak")
	}

	kendala := c.FormValue("kendala")
	solusi   := c.FormValue("solusi_sementara")
	tanggal  := c.FormValue("tanggal")
	if tanggal == "" {
		tanggal = time.Now().Format("2006-01-02")
	}
	statusKendala := c.FormValue("status_kendala")
	if statusKendala == "" {
		statusKendala = "open"
	}
	tindakLanjut := c.FormValue("tindak_lanjut_pml")

	// Ambil diperiksa & error otomatis dari FASIH (progress)
	var diperiksa, errDoc int
	db.DB.QueryRow(`SELECT COALESCE(fasih_approved_pengawas,0), COALESCE(fasih_rejected_pengawas,0)
		FROM progress WHERE sls_id=?`, slsID).Scan(&diperiksa, &errDoc)

	_, err := db.DB.Exec(`
		INSERT INTO verifikasi_harian
		  (sls_id, tanggal, jumlah_diperiksa, jumlah_error, jumlah_observasi,
		   status_kendala, tindak_lanjut_pml, kendala, solusi_sementara)
		VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
		ON DUPLICATE KEY UPDATE
		  jumlah_diperiksa  = VALUES(jumlah_diperiksa),
		  jumlah_error      = VALUES(jumlah_error),
		  kendala           = VALUES(kendala),
		  solusi_sementara  = VALUES(solusi_sementara),
		  status_kendala    = VALUES(status_kendala),
		  tindak_lanjut_pml = VALUES(tindak_lanjut_pml),
		  updated_at        = NOW()`,
		slsID, tanggal, diperiksa, errDoc, statusKendala, tindakLanjut, kendala, solusi)
	if err != nil {
		return err
	}

	var sp models.SLSProgress
	db.DB.QueryRow(`
		SELECT `+verifCols+`
		FROM sls s
		JOIN users u ON u.id = s.ppl_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.id = ?`, slsID).
		Scan(
			&sp.SLS.ID, &sp.KodeSLS, &sp.NamaSLS, &sp.Target,
			&sp.KodeKec, &sp.NamaKec, &sp.KodeDesa, &sp.NamaDesa,
			&sp.NamaPPL,
			&sp.JumlahSubmit, &sp.JumlahDraft,
			&sp.JumlahDiperiksa, &sp.JumlahError, &sp.JumlahObservasi,
			&sp.Kendala, &sp.SolusiSementara,
			&sp.StatusKendala, &sp.TindakLanjutPML,
		)

	c.Response().Header().Set("HX-Trigger", `{"showToast":{"msg":"Kendala berhasil disimpan!","kind":"success"},"closeModal":"true"}`)
	return c.Render(http.StatusOK, "pml_row.html", sp)
}
