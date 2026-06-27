package handlers

import (
	"net/http"
	"strconv"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

type PPLDetailData struct {
	SP           models.SLSProgress
	FasihOpen    int
	FasihSubmit  int
	// Breakdown per level
	FasihApprove          int // = ApprovedPengawas (alias lama)
	FasihReject           int // = RejectedPengawas (alias lama)
	FasihRevoke           int // = RevokedPengawas (alias lama)
	ApprovedPengawas      int
	RejectedPengawas      int
	RevokedPengawas       int
	ApprovedKabupaten     int
	RejectedKabupaten     int
	ApprovedProvinsi      int
	RejectedProvinsi      int
	ApprovedPusat         int
	RejectedPusat         int
	FasihTotal   int
	SyncedAt     string
}

// laporanCols membaca dari tabel progress (data FASIH)
const laporanCols = `
	s.id, s.kode_sls, s.nama_sls, s.target,
	COALESCE(s.kode_kec,''), COALESCE(s.nama_kec,''),
	COALESCE(s.kode_desa,''), COALESCE(s.nama_desa,''),
	COALESCE(p.jumlah_submit,0),
	COALESCE(p.jumlah_draft,0),
	CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END,
	COALESCE(DATE_FORMAT(p.fasih_synced_at,'%Y-%m-%d'),'')`

func pplQueryList(userID, page int) ([]models.SLSProgress, error) {
	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT `+laporanCols+`
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.ppl_id = ?
		ORDER BY s.kode_kec, s.kode_desa, s.kode_sls
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
			&sp.JumlahSubmit, &sp.JumlahDraft,
			&sp.JmlLaporan, &sp.TanggalTerakhir,
		)
		list = append(list, sp)
	}
	return list, nil
}

func querySPByID(slsID int) models.SLSProgress {
	var sp models.SLSProgress
	db.DB.QueryRow(`
		SELECT `+laporanCols+`
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.id = ?`, slsID).
		Scan(
			&sp.SLS.ID, &sp.KodeSLS, &sp.NamaSLS, &sp.Target,
			&sp.KodeKec, &sp.NamaKec, &sp.KodeDesa, &sp.NamaDesa,
			&sp.JumlahSubmit, &sp.JumlahDraft,
			&sp.JmlLaporan, &sp.TanggalTerakhir,
		)
	return sp
}

func PPLDashboard(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}

	var totalRow int
	db.DB.QueryRow("SELECT COUNT(*) FROM sls WHERE ppl_id=?", userID).Scan(&totalRow)

	list, err := pplQueryList(userID, page)
	if err != nil {
		return err
	}

	var totTarget, totSubmit, totDraft int
	db.DB.QueryRow(`
		SELECT COALESCE(SUM(s.target),0),
		       COALESCE(SUM(p.jumlah_submit),0),
		       COALESCE(SUM(p.jumlah_draft),0)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.ppl_id=?`, userID,
	).Scan(&totTarget, &totSubmit, &totDraft)

	pageInfo := models.NewPageInfo(page, totalRow, "/ppl/table", "ppl-table-wrap", "")

	return c.Render(http.StatusOK, "ppl.html", map[string]interface{}{
		"Name":        mw.SessionName(c),
		"List":        list,
		"Page":        pageInfo,
		"TotalTarget": totTarget,
		"TotalSubmit": totSubmit,
		"TotalDraft":  totDraft,
	})
}

func PPLTable(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	var totalRow int
	db.DB.QueryRow("SELECT COUNT(*) FROM sls WHERE ppl_id=?", userID).Scan(&totalRow)

	list, err := pplQueryList(userID, page)
	if err != nil {
		return err
	}
	pageInfo := models.NewPageInfo(page, totalRow, "/ppl/table", "ppl-table-wrap", "")

	return c.Render(http.StatusOK, "ppl_table.html", map[string]interface{}{
		"List": list,
		"Page": pageInfo,
	})
}

// PPLFormModal sekarang menampilkan detail FASIH (read-only), bukan form input.
func PPLFormModal(c echo.Context) error {
	slsID, _ := strconv.Atoi(c.Param("id"))
	userID := mw.SessionUserID(c)

	var count int
	db.DB.QueryRow("SELECT COUNT(*) FROM sls WHERE id=? AND ppl_id=?", slsID, userID).Scan(&count)
	if count == 0 {
		return c.String(http.StatusNotFound, "SLS tidak ditemukan")
	}

	sp := querySPByID(slsID)
	data := PPLDetailData{
		SP:       sp,
		SyncedAt: "Belum tersinkron",
	}

	db.DB.QueryRow(`
		SELECT COALESCE(fasih_open,0), COALESCE(fasih_submitted,0),
		       COALESCE(fasih_approved_pengawas,0), COALESCE(fasih_rejected_pengawas,0),
		       COALESCE(fasih_revoked_pengawas,0),
		       COALESCE(fasih_approved_kabupaten,0), COALESCE(fasih_rejected_kabupaten,0),
		       COALESCE(fasih_approved_provinsi,0),  COALESCE(fasih_rejected_provinsi,0),
		       COALESCE(fasih_approved_pusat,0),     COALESCE(fasih_rejected_pusat,0),
		       COALESCE(fasih_total,0),
		       COALESCE(DATE_FORMAT(fasih_synced_at,'%d/%m/%Y %H:%i'),'Belum tersinkron')
		FROM progress WHERE sls_id=?`, slsID).
		Scan(&data.FasihOpen, &data.FasihSubmit,
			&data.ApprovedPengawas, &data.RejectedPengawas, &data.RevokedPengawas,
			&data.ApprovedKabupaten, &data.RejectedKabupaten,
			&data.ApprovedProvinsi, &data.RejectedProvinsi,
			&data.ApprovedPusat, &data.RejectedPusat,
			&data.FasihTotal, &data.SyncedAt)
	// Isi alias lama
	data.FasihApprove = data.ApprovedPengawas
	data.FasihReject = data.RejectedPengawas
	data.FasihRevoke = data.RevokedPengawas

	return c.Render(http.StatusOK, "ppl_modal.html", data)
}

// PPLSaveProgress dinonaktifkan — data diambil dari FASIH, bukan input manual.
func PPLSaveProgress(c echo.Context) error {
	return c.String(http.StatusMethodNotAllowed, "Input manual dinonaktifkan. Data diambil otomatis dari FASIH.")
}
