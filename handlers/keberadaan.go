package handlers

import (
	"fmt"
	"net/http"
	"strconv"

	"github.com/labstack/echo/v4"

	"monitoringse/db"
	"monitoringse/models"
)

type KeberadaanRow struct {
	ID       int
	NamaSLS  string
	NamaKec  string
	NamaDesa string
	NamaPPL  string
	NamaPML  string
	Nama     string
	Skala    string
	Kode     string
	Label    string
	SyncedAt string
}

type KeberadaanStat struct {
	Label string
	Kode  string
	Total int
}

// AdminKeberadaanTable — GET /admin/table/keberadaan
func AdminKeberadaanTable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q      := c.QueryParam("q")
	label  := c.QueryParam("label")
	kecs   := c.QueryParams()["kec"]
	skalas := c.QueryParams()["skala"]
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like   := "%" + q + "%"

	where := ` WHERE (k.nama LIKE ? OR k.skala_usaha LIKE ? OR k.keberadaan_label LIKE ? OR s.nama_sls LIKE ?)`
	args  := []interface{}{like, like, like, like}

	if label != "" {
		where += ` AND k.keberadaan_label = ?`
		args = append(args, label)
	}
	inClause := func(col string, vals []string) {
		if len(vals) == 0 {
			return
		}
		ph := ""
		for i, v := range vals {
			if i > 0 {
				ph += ","
			}
			ph += "?"
			args = append(args, v)
		}
		where += ` AND ` + col + ` IN (` + ph + `)`
	}
	inClause("s.nama_kec", kecs)
	inClause("k.skala_usaha", skalas)
	if pmlID > 0 {
		where += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	if pplID > 0 {
		where += ` AND s.ppl_id = ?`
		args = append(args, pplID)
	}

	var total int
	countArgs := append([]interface{}{}, args...)
	db.DB.QueryRow(`SELECT COUNT(*) FROM keberadaan_usaha k JOIN sls s ON s.id=k.sls_id`+where, countArgs...).Scan(&total)

	extra := ""
	if q != "" {
		extra += "&q=" + q
	}
	for _, v := range kecs {
		extra += "&kec=" + v
	}
	for _, v := range skalas {
		extra += "&skala=" + v
	}
	if label != "" {
		extra += "&label=" + label
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}
	if pplID > 0 {
		extra += fmt.Sprintf("&ppl_id=%d", pplID)
	}

	offset   := (page - 1) * models.PerPage
	pageInfo := models.NewPageInfo(page, total, "/admin/table/keberadaan", "keberadaan-result", extra)

	queryArgs := append(args, models.PerPage, offset)
	rows, err := db.DB.Query(`
		SELECT k.id, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       k.nama, k.skala_usaha,
		       COALESCE(k.keberadaan_kode,''), COALESCE(k.keberadaan_label,''),
		       COALESCE(DATE_FORMAT(k.synced_at,'%d/%m/%Y %H:%i'),'')
		FROM keberadaan_usaha k
		JOIN sls s ON s.id = k.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		ORDER BY s.nama_kec, s.nama_desa, s.nama_sls, k.nama
		LIMIT ? OFFSET ?`, queryArgs...)

	var list []KeberadaanRow
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var r KeberadaanRow
			rows.Scan(&r.ID, &r.NamaSLS, &r.NamaKec, &r.NamaDesa,
				&r.NamaPPL, &r.NamaPML,
				&r.Nama, &r.Skala, &r.Kode, &r.Label, &r.SyncedAt)
			list = append(list, r)
		}
	}

	// Summary per label (untuk chart ringkasan di atas tabel)
	var stats []KeberadaanStat
	statRows, err := db.DB.Query(`
		SELECT COALESCE(keberadaan_label,'Belum diisi') as lbl,
		       COALESCE(keberadaan_kode,'') as kode,
		       COUNT(*) as tot
		FROM keberadaan_usaha
		GROUP BY keberadaan_kode, keberadaan_label
		ORDER BY tot DESC`)
	if err == nil {
		defer statRows.Close()
		for statRows.Next() {
			var st KeberadaanStat
			statRows.Scan(&st.Label, &st.Kode, &st.Total)
			stats = append(stats, st)
		}
	}

	// Distinct label list untuk filter dropdown
	var labelList []string
	lblRows, _ := db.DB.Query(`
		SELECT DISTINCT COALESCE(keberadaan_label,'') as lbl
		FROM keberadaan_usaha
		WHERE keberadaan_label IS NOT NULL AND keberadaan_label != ''
		ORDER BY lbl`)
	if lblRows != nil {
		defer lblRows.Close()
		for lblRows.Next() {
			var l string
			lblRows.Scan(&l)
			labelList = append(labelList, l)
		}
	}

	return c.Render(http.StatusOK, "keberadaan_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"Stats":     stats,
		"LabelList": labelList,
		"SkalaList": querySkalaList(),
		"KecList":   queryKecList(),
		"Q":      q,
		"Kecs":   kecs,
		"Skalas": skalas,
		"Label":  label,
		"PmlID":  pmlID,
		"PplID":  pplID,
	})
}
