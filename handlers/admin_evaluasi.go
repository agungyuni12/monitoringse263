package handlers

import (
	"net/http"
	"strconv"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

type AdminEvaluasiRow struct {
	ID                  int
	CreatedAt           string
	NamaOrganik         string
	NamaSLS             string
	NamaKec             string
	NamaDesa            string
	NamaPPL             string
	NamaPML             string
	Tipe                string
	Nama                string
	RincianKuesioner    string
	JenisKesalahan      string
	Rekomendasi         string
	Status              string
	CatatanTindakLanjut string
	TindakLanjutByName  string
	TindakLanjutAt      string
}

var adminEvaluasiSortCols = map[string]string{
	"tanggal": "ea.created_at",
	"organik": "org.name",
	"sls":     "s.nama_sls",
	"ppl":     "ppl.name",
	"pml":     "pml.name",
	"status":  "ea.status",
}

// AdminEvaluasiOrganikTable — GET /admin/table/evaluasi-organik
func AdminEvaluasiOrganikTable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	kec := c.QueryParam("kec")
	status := c.QueryParam("status")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where := `WHERE (org.name LIKE ? OR s.nama_sls LIKE ? OR ea.rincian_kuesioner LIKE ? OR ea.rekomendasi LIKE ?)`
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
	if status == "open" || status == "resolved" {
		where += ` AND ea.status = ?`
		args = append(args, status)
	}

	fromJoin := `
		FROM evaluasi_assignment ea
		JOIN users org ON org.id = ea.organik_id
		JOIN sls s ON s.id = ea.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN users tl ON tl.id = ea.tindak_lanjut_by`

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) `+fromJoin+` `+where, args...).Scan(&total)

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
	if status != "" {
		extra += "&status=" + status
	}

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, adminEvaluasiSortCols, "ea.created_at DESC")
	offset := (page - 1) * models.PerPage

	queryArgs := make([]interface{}, len(args))
	copy(queryArgs, args)
	queryArgs = append(queryArgs, models.PerPage, offset)

	rows, err := db.DB.Query(`
		SELECT ea.id, DATE_FORMAT(ea.created_at,'%d/%m/%Y %H:%i'), org.name,
		       s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''), ppl.name, pml.name,
		       ea.tipe, COALESCE(ea.nama,''),
		       COALESCE(ea.rincian_kuesioner,''), COALESCE(ea.jenis_kesalahan,''), ea.rekomendasi, ea.status,
		       COALESCE(ea.catatan_tindak_lanjut,''), COALESCE(tl.name,''),
		       COALESCE(DATE_FORMAT(ea.tindak_lanjut_at,'%d/%m/%Y %H:%i'),'')
		`+fromJoin+` `+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/evaluasi-organik", "admin-evaluasi-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra

	var list []AdminEvaluasiRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r AdminEvaluasiRow
			rows.Scan(&r.ID, &r.CreatedAt, &r.NamaOrganik, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
				&r.NamaPPL, &r.NamaPML, &r.Tipe, &r.Nama,
				&r.RincianKuesioner, &r.JenisKesalahan, &r.Rekomendasi,
				&r.Status, &r.CatatanTindakLanjut, &r.TindakLanjutByName, &r.TindakLanjutAt)
			list = append(list, r)
		}
	}

	return c.Render(http.StatusOK, "admin_evaluasi_table.html", map[string]interface{}{
		"Rows":     list,
		"PageInfo": pageInfo,
	})
}
