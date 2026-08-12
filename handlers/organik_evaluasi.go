package handlers

import (
	"net/http"
	"strconv"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

// AssignmentSearchRow — hasil pencarian assignment (dari usaha_ekonomi, yang
// menyimpan satu baris per usaha/assignment hasil sync FASIH) buat dipilih
// organik saat mau mencatat perbaikan.
type AssignmentSearchRow struct {
	AssignmentID string
	NamaUsaha    string
	SLSID        int
	NamaSLS      string
	NamaKec      string
	NamaDesa     string
	NamaPPL      string
	JmlPerbaikan int
}

// OrganikAssignmentSearch — GET /organik/assignment/search
func OrganikAssignmentSearch(c echo.Context) error {
	q := c.QueryParam("q")
	if len(q) < 2 {
		return c.Render(http.StatusOK, "organik_evaluasi_results.html", map[string]interface{}{})
	}
	like := "%" + q + "%"
	rows, err := db.DB.Query(`
		SELECT ue.assignment_id, COALESCE(ue.nama_usaha, ue.nama_kk, '(tanpa nama)'),
		       s.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''), ppl.name,
		       (SELECT COUNT(*) FROM evaluasi_assignment ea WHERE ea.assignment_id = ue.assignment_id)
		FROM usaha_ekonomi ue
		JOIN sls s ON s.id = ue.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		WHERE ue.nama_usaha LIKE ? OR ue.nama_kk LIKE ? OR s.nama_sls LIKE ? OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
		ORDER BY s.nama_kec, s.nama_desa, s.nama_sls
		LIMIT 20`, like, like, like, like, like)
	if err != nil {
		return c.Render(http.StatusOK, "organik_evaluasi_results.html", map[string]interface{}{})
	}
	defer rows.Close()

	var results []AssignmentSearchRow
	for rows.Next() {
		var r AssignmentSearchRow
		rows.Scan(&r.AssignmentID, &r.NamaUsaha, &r.SLSID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.JmlPerbaikan)
		results = append(results, r)
	}
	return c.Render(http.StatusOK, "organik_evaluasi_results.html", map[string]interface{}{
		"Results": results,
	})
}

// evaluasiAssignmentInfo mengambil info usaha/SLS untuk satu assignment_id
// dari usaha_ekonomi (sumber data assignment). Dipakai modal & save handler.
func evaluasiAssignmentInfo(assignmentID string) (namaUsaha string, slsID int, sp struct {
	NamaSLS, NamaKec, NamaDesa, NamaPPL, NamaPML string
}, ok bool) {
	err := db.DB.QueryRow(`
		SELECT COALESCE(ue.nama_usaha, ue.nama_kk, '(tanpa nama)'), s.id,
		       s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''), ppl.name, pml.name
		FROM usaha_ekonomi ue
		JOIN sls s ON s.id = ue.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE ue.assignment_id = ?`, assignmentID).
		Scan(&namaUsaha, &slsID, &sp.NamaSLS, &sp.NamaKec, &sp.NamaDesa, &sp.NamaPPL, &sp.NamaPML)
	ok = err == nil
	return
}

func loadPerbaikanItems(assignmentID string) []models.EvaluasiAssignment {
	rows, err := db.DB.Query(`
		SELECT ea.id, ea.rincian_kuesioner, ea.jenis_kesalahan, ea.rekomendasi, ea.status,
		       COALESCE(ea.catatan_tindak_lanjut,''), COALESCE(tl.name,''),
		       COALESCE(DATE_FORMAT(ea.tindak_lanjut_at,'%d/%m/%Y %H:%i'),'')
		FROM evaluasi_assignment ea
		LEFT JOIN users tl ON tl.id = ea.tindak_lanjut_by
		WHERE ea.assignment_id = ?
		ORDER BY ea.created_at DESC`, assignmentID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var items []models.EvaluasiAssignment
	for rows.Next() {
		var it models.EvaluasiAssignment
		rows.Scan(&it.ID, &it.RincianKuesioner, &it.JenisKesalahan, &it.Rekomendasi, &it.Status,
			&it.CatatanTindakLanjut, &it.TindakLanjutByName, &it.TindakLanjutAt)
		items = append(items, it)
	}
	return items
}

// organikEvaluasiModalData bangun map render "organik_evaluasi_modal.html" —
// dipakai baik saat modal pertama dibuka (GET) maupun setelah simpan (POST),
// supaya kedua handler tidak duplikasi daftar field.
func organikEvaluasiModalData(assignmentID, namaUsaha string, slsID int, sp struct {
	NamaSLS, NamaKec, NamaDesa, NamaPPL, NamaPML string
}) map[string]interface{} {
	return map[string]interface{}{
		"AssignmentID": assignmentID,
		"NamaUsaha":    namaUsaha,
		"SLSID":        slsID,
		"NamaSLS":      sp.NamaSLS,
		"NamaKec":      sp.NamaKec,
		"NamaDesa":     sp.NamaDesa,
		"NamaPPL":      sp.NamaPPL,
		"NamaPML":      sp.NamaPML,
		"FasihLink":    fasihSMLink(assignmentID),
		"Items":        loadPerbaikanItems(assignmentID),
		"JenisOpts":    models.JenisKesalahanOptions,
		"RincianOpts":  models.RincianKuesionerList,
	}
}

// OrganikPerbaikanModal — GET /organik/assignment/:assignment_id/perbaikan/modal
func OrganikPerbaikanModal(c echo.Context) error {
	assignmentID := c.Param("assignment_id")
	namaUsaha, slsID, sp, ok := evaluasiAssignmentInfo(assignmentID)
	if !ok {
		return c.String(http.StatusNotFound, "Assignment tidak ditemukan")
	}

	return c.Render(http.StatusOK, "organik_evaluasi_modal.html", organikEvaluasiModalData(assignmentID, namaUsaha, slsID, sp))
}

// OrganikSavePerbaikan — POST /organik/assignment/:assignment_id/perbaikan
// Body form: rincian_kuesioner[], jenis_kesalahan[], rekomendasi[] (array,
// satu perbaikan bisa punya banyak rincian sekaligus).
func OrganikSavePerbaikan(c echo.Context) error {
	assignmentID := c.Param("assignment_id")
	userID := mw.SessionUserID(c)

	namaUsaha, slsID, sp, ok := evaluasiAssignmentInfo(assignmentID)
	if !ok {
		return c.String(http.StatusNotFound, "Assignment tidak ditemukan")
	}

	if err := c.Request().ParseForm(); err != nil {
		return c.String(http.StatusBadRequest, "Form tidak valid")
	}
	rincianList := c.Request().Form["rincian_kuesioner[]"]
	jenisList := c.Request().Form["jenis_kesalahan[]"]
	rekomendasiList := c.Request().Form["rekomendasi[]"]

	validJenis := map[string]bool{}
	for _, o := range models.JenisKesalahanOptions {
		validJenis[o.Value] = true
	}

	n := len(rincianList)
	if len(jenisList) < n {
		n = len(jenisList)
	}
	if len(rekomendasiList) < n {
		n = len(rekomendasiList)
	}

	saved := 0
	for i := 0; i < n; i++ {
		rincian := rincianList[i]
		jenis := jenisList[i]
		rekomendasi := rekomendasiList[i]
		if rincian == "" || rekomendasi == "" || !validJenis[jenis] {
			continue
		}
		_, err := db.DB.Exec(`
			INSERT INTO evaluasi_assignment
			  (organik_id, sls_id, assignment_id, rincian_kuesioner, jenis_kesalahan, rekomendasi, status)
			VALUES (?, ?, ?, ?, ?, ?, 'open')`,
			userID, slsID, assignmentID, rincian, jenis, rekomendasi)
		if err == nil {
			saved++
		}
	}

	if saved > 0 {
		c.Response().Header().Set("HX-Trigger", `{"showToast":{"msg":"`+strconv.Itoa(saved)+` catatan perbaikan berhasil disimpan!","kind":"success"},"refreshEvaluasi":"true"}`)
	} else {
		c.Response().Header().Set("HX-Trigger", `{"showToast":{"msg":"Gagal menyimpan: pastikan semua field wajib terisi.","kind":"error"}}`)
	}

	return c.Render(http.StatusOK, "organik_evaluasi_modal.html", organikEvaluasiModalData(assignmentID, namaUsaha, slsID, sp))
}

// OrganikEvaluasiTable — GET /organik/evaluasi
// Rekap assignment yang sudah dievaluasi oleh organik yang login, dikelompokkan
// per assignment_id (satu assignment bisa punya banyak catatan perbaikan).
func OrganikEvaluasiTable(c echo.Context) error {
	userID := mw.SessionUserID(c)
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}

	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT ea.assignment_id) FROM evaluasi_assignment ea WHERE ea.organik_id=?`, userID).Scan(&total)

	offset := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/organik/evaluasi", "evaluasi-table-wrap", "")

	rows, err := db.DB.Query(`
		SELECT ea.assignment_id,
		       COALESCE(MAX(ue.nama_usaha), MAX(ue.nama_kk), '(tanpa nama)'),
		       s.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       COUNT(*), SUM(CASE WHEN ea.status='open' THEN 1 ELSE 0 END),
		       DATE_FORMAT(MAX(ea.created_at),'%d/%m/%Y %H:%i')
		FROM evaluasi_assignment ea
		JOIN sls s ON s.id = ea.sls_id
		LEFT JOIN usaha_ekonomi ue ON ue.assignment_id = ea.assignment_id
		WHERE ea.organik_id = ?
		GROUP BY ea.assignment_id, s.id, s.nama_sls, s.nama_kec, s.nama_desa
		ORDER BY MAX(ea.created_at) DESC
		LIMIT ? OFFSET ?`, userID, models.PerPage, offset)

	type Row struct {
		AssignmentID string
		NamaUsaha    string
		SLSID        int
		NamaSLS      string
		NamaKec      string
		NamaDesa     string
		JmlPerbaikan int
		JmlOpen      int
		CreatedAt    string
	}
	var list []Row
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r Row
			rows.Scan(&r.AssignmentID, &r.NamaUsaha, &r.SLSID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
				&r.JmlPerbaikan, &r.JmlOpen, &r.CreatedAt)
			list = append(list, r)
		}
	}

	return c.Render(http.StatusOK, "organik_evaluasi_table.html", map[string]interface{}{
		"Rows":     list,
		"PageInfo": pageInfo,
	})
}
