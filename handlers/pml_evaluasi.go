package handlers

import (
	"net/http"
	"strconv"
	"time"

	"monitoringse/db"
	mw "monitoringse/middleware"

	"github.com/labstack/echo/v4"
)

type PerbaikanRow struct {
	ID                  int
	CreatedAt           string
	NamaSLS             string
	NamaUsaha           string
	RincianKuesioner    string
	JenisKesalahan      string
	Rekomendasi         string
	Status              string
	TindakLanjutByName  string
	CatatanTindakLanjut string
}

// PerbaikanTable — GET /pml/perbaikan/table dan GET /ppl/perbaikan/table
// (handler yang sama; scope data ditentukan dari role sesi login).
func PerbaikanTable(c echo.Context) error {
	userID := mw.SessionUserID(c)
	role := mw.SessionRole(c)
	status := c.QueryParam("status")

	basePath := "/pml"
	where := `WHERE s.pml_id = ?`
	if role == "ppl" {
		basePath = "/ppl"
		where = `WHERE s.ppl_id = ?`
	}
	args := []interface{}{userID}
	if status == "open" || status == "resolved" {
		where += ` AND ea.status = ?`
		args = append(args, status)
	}

	rows, err := db.DB.Query(`
		SELECT ea.id, DATE_FORMAT(ea.created_at,'%d/%m/%Y %H:%i'),
		       s.nama_sls, COALESCE(ue.nama_usaha, ue.nama_kk, '(tanpa nama)'),
		       ea.rincian_kuesioner, ea.jenis_kesalahan, ea.rekomendasi, ea.status,
		       COALESCE(tl.name,''), COALESCE(ea.catatan_tindak_lanjut,'')
		FROM evaluasi_assignment ea
		JOIN sls s ON s.id = ea.sls_id
		LEFT JOIN usaha_ekonomi ue ON ue.assignment_id = ea.assignment_id
		LEFT JOIN users tl ON tl.id = ea.tindak_lanjut_by
		`+where+`
		ORDER BY ea.status = 'open' DESC, ea.created_at DESC
		LIMIT 200`, args...)

	var list []PerbaikanRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r PerbaikanRow
			rows.Scan(&r.ID, &r.CreatedAt, &r.NamaSLS, &r.NamaUsaha,
				&r.RincianKuesioner, &r.JenisKesalahan, &r.Rekomendasi, &r.Status,
				&r.TindakLanjutByName, &r.CatatanTindakLanjut)
			list = append(list, r)
		}
	}

	return c.Render(http.StatusOK, "pml_perbaikan_table.html", map[string]interface{}{
		"Rows":     list,
		"BasePath": basePath,
	})
}

// PerbaikanTindakLanjut — POST /pml/perbaikan/:id/tindak-lanjut dan
// POST /ppl/perbaikan/:id/tindak-lanjut. Hanya PML/PPL penanggung jawab SLS
// dari assignment terkait yang boleh menandai selesai.
func PerbaikanTindakLanjut(c echo.Context) error {
	id, _ := strconv.Atoi(c.Param("id"))
	userID := mw.SessionUserID(c)
	role := mw.SessionRole(c)

	where := "s.pml_id = ?"
	if role == "ppl" {
		where = "s.ppl_id = ?"
	}
	var count int
	db.DB.QueryRow(`
		SELECT COUNT(*) FROM evaluasi_assignment ea
		JOIN sls s ON s.id = ea.sls_id
		WHERE ea.id = ? AND `+where, id, userID).Scan(&count)
	if count == 0 {
		return c.String(http.StatusForbidden, "Akses ditolak")
	}

	catatan := c.FormValue("catatan_tindak_lanjut")
	if catatan == "" {
		return c.String(http.StatusBadRequest, "Catatan penanganan wajib diisi")
	}

	_, err := db.DB.Exec(`
		UPDATE evaluasi_assignment
		SET status='resolved', catatan_tindak_lanjut=?, tindak_lanjut_by=?, tindak_lanjut_at=?
		WHERE id=?`, catatan, userID, time.Now(), id)
	if err != nil {
		return err
	}

	var r PerbaikanRow
	err = db.DB.QueryRow(`
		SELECT ea.id, DATE_FORMAT(ea.created_at,'%d/%m/%Y %H:%i'),
		       s.nama_sls, COALESCE(ue.nama_usaha, ue.nama_kk, '(tanpa nama)'),
		       ea.rincian_kuesioner, ea.jenis_kesalahan, ea.rekomendasi, ea.status,
		       COALESCE(tl.name,''), COALESCE(ea.catatan_tindak_lanjut,'')
		FROM evaluasi_assignment ea
		JOIN sls s ON s.id = ea.sls_id
		LEFT JOIN usaha_ekonomi ue ON ue.assignment_id = ea.assignment_id
		LEFT JOIN users tl ON tl.id = ea.tindak_lanjut_by
		WHERE ea.id = ?`, id).Scan(&r.ID, &r.CreatedAt, &r.NamaSLS, &r.NamaUsaha,
		&r.RincianKuesioner, &r.JenisKesalahan, &r.Rekomendasi, &r.Status,
		&r.TindakLanjutByName, &r.CatatanTindakLanjut)
	if err != nil {
		return err
	}

	basePath := "/pml"
	if role == "ppl" {
		basePath = "/ppl"
	}
	c.Response().Header().Set("HX-Trigger", `{"showToast":{"msg":"Perbaikan ditandai selesai!","kind":"success"},"refreshEvaluasi":"true"}`)
	return c.Render(http.StatusOK, "pml_perbaikan_row.html", map[string]interface{}{
		"Row":      r,
		"BasePath": basePath,
	})
}
