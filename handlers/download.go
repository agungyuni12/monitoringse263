package handlers

import (
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"monitoringse/db"

	"github.com/labstack/echo/v4"
	"github.com/xuri/excelize/v2"
)

func itoa(n int) string { return strconv.Itoa(n) }

func writeXlsx(c echo.Context, filename string, headers []string, fillRows func(f *excelize.File, sheet string)) error {
	f := excelize.NewFile()
	defer f.Close()
	sheet := "Data"
	f.SetSheetName("Sheet1", sheet)

	bold, _ := f.NewStyle(&excelize.Style{
		Font: &excelize.Font{Bold: true, Color: "FFFFFF"},
		Fill: excelize.Fill{Type: "pattern", Color: []string{"F37021"}, Pattern: 1},
		Alignment: &excelize.Alignment{Horizontal: "center"},
	})

	for i, h := range headers {
		c2, _ := excelize.CoordinatesToCellName(i+1, 1)
		f.SetCellValue(sheet, c2, h)
		f.SetCellStyle(sheet, c2, c2, bold)
	}

	fillRows(f, sheet)

	c.Response().Header().Set("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	c.Response().Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	c.Response().WriteHeader(http.StatusOK)
	return f.Write(c.Response())
}

func DownloadPML(c echo.Context) error {
	q := c.QueryParam("q")
	like := "%" + q + "%"

	rows, err := db.DB.Query(`
		SELECT u.name,
		       COUNT(DISTINCT s.ppl_id), COUNT(s.id),
		       COALESCE(SUM(p.jumlah_submit),0),
		       COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0),
		       COALESCE(SUM(p.fasih_rejected_pengawas),0)
		FROM users u
		JOIN sls s ON s.pml_id = u.id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE u.role = 'pml' AND u.name LIKE ?
		GROUP BY u.id, u.name ORDER BY u.name`, like)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()

	type row struct {
		name                              string
		jmlPPL, jmlSLS, submit, draft, approved, rejected int
	}
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.name, &r.jmlPPL, &r.jmlSLS, &r.submit, &r.draft, &r.approved, &r.rejected)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_pml_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Nama PML", "Jml PPL", "Jml SLS", "Submit", "Draft", "Approved", "Rejected"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range data {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.name)
			f.SetCellValue(sheet, cell(2, n), r.jmlPPL)
			f.SetCellValue(sheet, cell(3, n), r.jmlSLS)
			f.SetCellValue(sheet, cell(4, n), r.submit)
			f.SetCellValue(sheet, cell(5, n), r.draft)
			f.SetCellValue(sheet, cell(6, n), r.approved)
			f.SetCellValue(sheet, cell(7, n), r.rejected)
		}
	})
}

func DownloadPPL(c echo.Context) error {
	q := c.QueryParam("q")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	like := "%" + q + "%"

	pmlFilter := ""
	var args []interface{}
	if pmlID > 0 {
		pmlFilter = " AND s.pml_id = ?"
		args = []interface{}{like, like, pmlID}
	} else {
		args = []interface{}{like, like}
	}

	rows, err := db.DB.Query(`
		SELECT u.name, pml.name, COUNT(s.id),
		       COALESCE(SUM(p.jumlah_submit),0),
		       COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(s.target),0)
		FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE u.role = 'ppl'
		  AND (u.name LIKE ? OR pml.name LIKE ?)`+pmlFilter+`
		GROUP BY u.id, u.name, pml.name ORDER BY pml.name, u.name`, args...)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()

	type row struct {
		ppl, pml         string
		jmlSLS, submit, draft, target int
	}
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.ppl, &r.pml, &r.jmlSLS, &r.submit, &r.draft, &r.target)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_ppl_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Nama PPL", "Nama PML", "Jml SLS", "Submit", "Draft", "Total (FASIH)"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range data {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.ppl)
			f.SetCellValue(sheet, cell(2, n), r.pml)
			f.SetCellValue(sheet, cell(3, n), r.jmlSLS)
			f.SetCellValue(sheet, cell(4, n), r.submit)
			f.SetCellValue(sheet, cell(5, n), r.draft)
			f.SetCellValue(sheet, cell(6, n), r.target)
		}
	})
}

func DownloadSLS(c echo.Context) error {
	q := c.QueryParam("q")
	level := c.QueryParam("level")
	like := "%" + q + "%"

	suffix := level
	if suffix == "" {
		suffix = "sls"
	}
	fname := fmt.Sprintf("monitoring_%s_%s.xlsx", suffix, time.Now().In(wita).Format("20060102"))

	switch level {
	case "kec":
		rows, err := db.DB.Query(`
			SELECT s.nama_kec, COUNT(DISTINCT s.id),
			       COALESCE(SUM(p.fasih_total),0),
			       COALESCE(SUM(p.jumlah_submit),0),
			       COALESCE(SUM(p.jumlah_draft),0),
			       COALESCE(SUM(p.fasih_approved_pengawas),0),
			       COALESCE(SUM(p.fasih_rejected_pengawas),0)
			FROM sls s
			LEFT JOIN progress p ON p.sls_id = s.id
			WHERE s.nama_kec LIKE ?
			GROUP BY s.nama_kec, s.kode_kec ORDER BY s.kode_kec`, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct {
			kec                                     string
			jml, total, submit, draft, approved, rejected int
		}
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.kec, &r.jml, &r.total, &r.submit, &r.draft, &r.approved, &r.rejected)
			data = append(data, r)
		}
		headers := []string{"Kecamatan", "Jml SLS", "Total (FASIH)", "Submit", "Draft", "Approved", "Rejected"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				f.SetCellValue(sheet, cell(1, n), r.kec)
				f.SetCellValue(sheet, cell(2, n), r.jml)
				f.SetCellValue(sheet, cell(3, n), r.total)
				f.SetCellValue(sheet, cell(4, n), r.submit)
				f.SetCellValue(sheet, cell(5, n), r.draft)
				f.SetCellValue(sheet, cell(6, n), r.approved)
				f.SetCellValue(sheet, cell(7, n), r.rejected)
			}
		})

	case "desa":
		rows, err := db.DB.Query(`
			SELECT s.nama_desa, s.nama_kec, COUNT(DISTINCT s.id),
			       COALESCE(SUM(p.fasih_total),0),
			       COALESCE(SUM(p.jumlah_submit),0),
			       COALESCE(SUM(p.jumlah_draft),0),
			       COALESCE(SUM(p.fasih_approved_pengawas),0),
			       COALESCE(SUM(p.fasih_rejected_pengawas),0)
			FROM sls s
			LEFT JOIN progress p ON p.sls_id = s.id
			WHERE s.nama_desa LIKE ? OR s.nama_kec LIKE ?
			GROUP BY s.nama_desa, s.nama_kec, s.kode_desa, s.kode_kec
			ORDER BY s.kode_kec, s.kode_desa`, like, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct {
			desa, kec                               string
			jml, total, submit, draft, approved, rejected int
		}
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.desa, &r.kec, &r.jml, &r.total, &r.submit, &r.draft, &r.approved, &r.rejected)
			data = append(data, r)
		}
		headers := []string{"Desa", "Kecamatan", "Jml SLS", "Total (FASIH)", "Submit", "Draft", "Approved", "Rejected"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				f.SetCellValue(sheet, cell(1, n), r.desa)
				f.SetCellValue(sheet, cell(2, n), r.kec)
				f.SetCellValue(sheet, cell(3, n), r.jml)
				f.SetCellValue(sheet, cell(4, n), r.total)
				f.SetCellValue(sheet, cell(5, n), r.submit)
				f.SetCellValue(sheet, cell(6, n), r.draft)
				f.SetCellValue(sheet, cell(7, n), r.approved)
				f.SetCellValue(sheet, cell(8, n), r.rejected)
			}
		})

	default:
		rows, err := db.DB.Query(`
			SELECT s.kode_sls, s.nama_sls, ppl.name, pml.name,
			       COALESCE(s.nama_desa,''), COALESCE(s.nama_kec,''),
			       COALESCE(p.fasih_total,0),
			       COALESCE(p.jumlah_submit,0),
			       COALESCE(p.jumlah_draft,0),
			       COALESCE(p.fasih_approved_pengawas,0),
			       COALESCE(p.fasih_rejected_pengawas,0)
			FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id
			LEFT JOIN progress p ON p.sls_id = s.id
			WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
			  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
			ORDER BY s.kode_kec, s.kode_desa, s.kode_sls`,
			like, like, like, like, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct {
			kode, nama, ppl, pml, desa, kec    string
			total, submit, draft, approved, rejected int
		}
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.kode, &r.nama, &r.ppl, &r.pml, &r.desa, &r.kec,
				&r.total, &r.submit, &r.draft, &r.approved, &r.rejected)
			data = append(data, r)
		}
		headers := []string{"Kode SLS", "Nama SLS", "PPL", "PML", "Desa", "Kecamatan", "Total (FASIH)", "Submit", "Draft", "Approved", "Rejected"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				f.SetCellValue(sheet, cell(1, n), r.kode)
				f.SetCellValue(sheet, cell(2, n), r.nama)
				f.SetCellValue(sheet, cell(3, n), r.ppl)
				f.SetCellValue(sheet, cell(4, n), r.pml)
				f.SetCellValue(sheet, cell(5, n), r.desa)
				f.SetCellValue(sheet, cell(6, n), r.kec)
				f.SetCellValue(sheet, cell(7, n), r.total)
				f.SetCellValue(sheet, cell(8, n), r.submit)
				f.SetCellValue(sheet, cell(9, n), r.draft)
				f.SetCellValue(sheet, cell(10, n), r.approved)
				f.SetCellValue(sheet, cell(11, n), r.rejected)
			}
		})
	}
}

func DownloadOrganik(c echo.Context) error {
	q := c.QueryParam("q")
	like := "%" + q + "%"

	rows, err := db.DB.Query(`
		SELECT org.name, s.nama_sls, COALESCE(s.nama_desa,''), COALESCE(s.nama_kec,''),
		       ppl.name, pml.name,
		       lo.tanggal, lo.jumlah_diawasi,
		       COALESCE(lo.kendala,''), COALESCE(lo.solusi,'')
		FROM laporan_organik lo
		JOIN users org ON org.id = lo.organik_id
		JOIN sls s ON s.id = lo.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE org.name LIKE ? OR s.nama_sls LIKE ? OR s.nama_desa LIKE ?
		  OR s.nama_kec LIKE ? OR ppl.name LIKE ?
		ORDER BY lo.tanggal DESC, org.name`, like, like, like, like, like)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()

	type row struct {
		org, sls, desa, kec, ppl, pml, tgl, kendala, solusi string
		diawasi                                              int
	}
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.org, &r.sls, &r.desa, &r.kec, &r.ppl, &r.pml, &r.tgl, &r.diawasi, &r.kendala, &r.solusi)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_organik_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Nama Organik", "Nama SLS", "Desa", "Kecamatan", "PPL", "PML", "Tanggal", "Jml Diawasi", "Kendala", "Solusi"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range data {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.org)
			f.SetCellValue(sheet, cell(2, n), r.sls)
			f.SetCellValue(sheet, cell(3, n), r.desa)
			f.SetCellValue(sheet, cell(4, n), r.kec)
			f.SetCellValue(sheet, cell(5, n), r.ppl)
			f.SetCellValue(sheet, cell(6, n), r.pml)
			f.SetCellValue(sheet, cell(7, n), r.tgl)
			f.SetCellValue(sheet, cell(8, n), r.diawasi)
			f.SetCellValue(sheet, cell(9, n), r.kendala)
			f.SetCellValue(sheet, cell(10, n), r.solusi)
		}
	})
}

func DownloadKendala(c echo.Context) error {
	q := c.QueryParam("q")
	like := "%" + q + "%"

	type row struct{ sumber, petugas, sls, desa, kec, tgl, kendala, solusi string }
	var data []row

	orgRows, err := db.DB.Query(`
		SELECT 'Organik', org.name, s.nama_sls,
		       COALESCE(s.nama_desa,''), COALESCE(s.nama_kec,''),
		       lo.tanggal, lo.kendala, COALESCE(lo.solusi,'')
		FROM laporan_organik lo
		JOIN users org ON org.id = lo.organik_id
		JOIN sls s ON s.id = lo.sls_id
		WHERE lo.kendala IS NOT NULL AND lo.kendala != ''
		  AND (org.name LIKE ? OR s.nama_sls LIKE ? OR s.nama_desa LIKE ? OR s.nama_kec LIKE ? OR lo.kendala LIKE ?)
		ORDER BY lo.tanggal DESC`,
		like, like, like, like, like)
	if err == nil {
		defer orgRows.Close()
		for orgRows.Next() {
			var r row
			orgRows.Scan(&r.sumber, &r.petugas, &r.sls, &r.desa, &r.kec, &r.tgl, &r.kendala, &r.solusi)
			data = append(data, r)
		}
	}

	pmlRows, err := db.DB.Query(`
		SELECT 'PML', pml.name, s.nama_sls,
		       COALESCE(s.nama_desa,''), COALESCE(s.nama_kec,''),
		       vh.tanggal, vh.kendala, COALESCE(vh.solusi_sementara,'')
		FROM verifikasi_harian vh
		JOIN sls s ON s.id = vh.sls_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE vh.kendala IS NOT NULL AND vh.kendala != ''
		  AND (pml.name LIKE ? OR s.nama_sls LIKE ? OR s.nama_desa LIKE ? OR s.nama_kec LIKE ? OR vh.kendala LIKE ?)
		ORDER BY vh.tanggal DESC`,
		like, like, like, like, like)
	if err == nil {
		defer pmlRows.Close()
		for pmlRows.Next() {
			var r row
			pmlRows.Scan(&r.sumber, &r.petugas, &r.sls, &r.desa, &r.kec, &r.tgl, &r.kendala, &r.solusi)
			data = append(data, r)
		}
	}

	sort.SliceStable(data, func(i, j int) bool {
		if data[i].tgl != data[j].tgl {
			return data[i].tgl > data[j].tgl
		}
		return data[i].sumber < data[j].sumber
	})

	fname := fmt.Sprintf("daftar_kendala_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Sumber", "Nama Petugas", "Nama SLS", "Desa", "Kecamatan", "Tanggal", "Kendala", "Solusi Sementara"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range data {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.sumber)
			f.SetCellValue(sheet, cell(2, n), r.petugas)
			f.SetCellValue(sheet, cell(3, n), r.sls)
			f.SetCellValue(sheet, cell(4, n), r.desa)
			f.SetCellValue(sheet, cell(5, n), r.kec)
			f.SetCellValue(sheet, cell(6, n), r.tgl)
			f.SetCellValue(sheet, cell(7, n), r.kendala)
			f.SetCellValue(sheet, cell(8, n), r.solusi)
		}
	})
}

func DownloadKeberadaan(c echo.Context) error {
	q := c.QueryParam("q")
	label := c.QueryParam("label")
	kecs := c.QueryParams()["kec"]
	skalas := c.QueryParams()["skala"]
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

	where := ` WHERE (k.nama LIKE ? OR k.skala_usaha LIKE ? OR k.keberadaan_label LIKE ? OR s.nama_sls LIKE ?)`
	args := []interface{}{like, like, like, like}

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

	rows, err := db.DB.Query(`
		SELECT s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       k.nama, k.skala_usaha,
		       COALESCE(k.keberadaan_label,''),
		       COALESCE(k.gate_label,''), COALESCE(k.assignment_status,''),
		       COALESCE(DATE_FORMAT(k.synced_at,'%d/%m/%Y %H:%i'),'')
		FROM keberadaan_usaha k
		JOIN sls s ON s.id = k.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		ORDER BY s.nama_kec, s.nama_desa, s.nama_sls, k.nama`, args...)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()

	type row struct {
		sls, kec, desa, ppl, pml, nama, skala, label, gateLabel, assignmentStatus, synced string
	}
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.sls, &r.kec, &r.desa, &r.ppl, &r.pml, &r.nama, &r.skala, &r.label,
			&r.gateLabel, &r.assignmentStatus, &r.synced)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_keberadaan_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Nama SLS", "Kecamatan", "Desa", "PPL", "PML", "Nama Usaha", "Skala", "Status Keberadaan", "Keterangan Gate", "Status Assignment", "Sync Terakhir"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range data {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.sls)
			f.SetCellValue(sheet, cell(2, n), r.kec)
			f.SetCellValue(sheet, cell(3, n), r.desa)
			f.SetCellValue(sheet, cell(4, n), r.ppl)
			f.SetCellValue(sheet, cell(5, n), r.pml)
			f.SetCellValue(sheet, cell(6, n), r.nama)
			f.SetCellValue(sheet, cell(7, n), r.skala)
			f.SetCellValue(sheet, cell(8, n), r.label)
			f.SetCellValue(sheet, cell(9, n), r.gateLabel)
			f.SetCellValue(sheet, cell(10, n), r.assignmentStatus)
			f.SetCellValue(sheet, cell(11, n), r.synced)
		}
	})
}

func cell(col, row int) string {
	name, _ := excelize.CoordinatesToCellName(col, row)
	return name
}

// DownloadAnomali — GET /admin/download/anomali (filter sama seperti tab Anomali)
func DownloadAnomali(c echo.Context) error {
	q := c.QueryParam("q")
	kec := c.QueryParam("kec")
	status := c.QueryParam("status")
	fasih := c.QueryParam("fasih")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	like := "%" + q + "%"

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
	if status == "belum" {
		where += " AND a.sudah_ditindaklanjuti_sigempar IS NULL"
	} else if status == "sudah" {
		where += " AND a.sudah_ditindaklanjuti_sigempar IS NOT NULL"
	}
	if fasih == "belum" {
		where += " AND a.is_resolved_fasih = 0"
	} else if fasih == "sudah" {
		where += " AND a.is_resolved_fasih = 1"
	}

	rows, err := db.DB.Query(`
		SELECT s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name,
		       a.nama, a.jenis, COALESCE(a.rule_msg,''),
		       COALESCE(DATE_FORMAT(a.synced_at,'%d/%m/%Y %H:%i'),''),
		       COALESCE(DATE_FORMAT(a.sudah_ditindaklanjuti_sigempar,'%d/%m/%Y %H:%i'),''),
		       a.is_resolved_fasih
		FROM anomali a
		JOIN sls s ON s.id = a.sls_id
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id`+where+`
		ORDER BY s.nama_kec, s.nama_desa, s.nama_sls, a.rule_key`, args...)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()

	type row struct {
		sls, kec, desa, ppl, pml, nama, jenis, msg, syncedAt, sigemparAt string
		resolvedFasih                                                   bool
	}
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.sls, &r.kec, &r.desa, &r.ppl, &r.pml, &r.nama, &r.jenis, &r.msg, &r.syncedAt, &r.sigemparAt, &r.resolvedFasih)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_anomali_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Nama SLS", "Kecamatan", "Desa", "PPL", "PML", "Nama Responden", "Jenis Anomali", "Keterangan", "Sync Terakhir", "Sudah Ditindaklanjuti SIGEMPAR", "Status FASIH"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range data {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.sls)
			f.SetCellValue(sheet, cell(2, n), r.kec)
			f.SetCellValue(sheet, cell(3, n), r.desa)
			f.SetCellValue(sheet, cell(4, n), r.ppl)
			f.SetCellValue(sheet, cell(5, n), r.pml)
			f.SetCellValue(sheet, cell(6, n), r.nama)
			f.SetCellValue(sheet, cell(7, n), r.jenis)
			f.SetCellValue(sheet, cell(8, n), r.msg)
			f.SetCellValue(sheet, cell(9, n), r.syncedAt)
			f.SetCellValue(sheet, cell(10, n), r.sigemparAt)
			f.SetCellValue(sheet, cell(11, n), boolLabel(r.resolvedFasih, "Sudah", "Belum"))
		}
	})
}

func boolLabel(b bool, yes, no string) string {
	if b {
		return yes
	}
	return no
}

// DownloadProgresRekap — GET /admin/download/progres-rekap (filter sama seperti tab Rekap Progres)
func DownloadProgresRekap(c echo.Context) error {
	q := c.QueryParam("q")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))
	slsID, _ := strconv.Atoi(c.QueryParam("sls_id"))
	prioritasOnly := c.QueryParam("prioritas") == "1"
	metode := normalizeMetode(c.QueryParam("metode"))

	list := queryProgresRekapRows(q, pmlID, pplID, slsID, prioritasOnly, metode)

	fname := fmt.Sprintf("monitoring_rekap_progres_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Nama SLS", "Kecamatan", "Desa", "PPL", "PML", "Prioritas",
		"Total", "Submit", "Draft", "Approved", "Rejected", "% Progres", "% Terverifikasi",
		"% Coverage Usaha BKU", "% Coverage Usaha Keluarga", "% Coverage Keluarga"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range list {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.NamaSLS)
			f.SetCellValue(sheet, cell(2, n), r.NamaKec)
			f.SetCellValue(sheet, cell(3, n), r.NamaDesa)
			f.SetCellValue(sheet, cell(4, n), r.NamaPPL)
			f.SetCellValue(sheet, cell(5, n), r.NamaPML)
			f.SetCellValue(sheet, cell(6, n), boolLabel(r.Prioritas, "Ya", ""))
			f.SetCellValue(sheet, cell(7, n), r.FasihTotal)
			f.SetCellValue(sheet, cell(8, n), r.FasihSubmit)
			f.SetCellValue(sheet, cell(9, n), r.JumlahDraft)
			f.SetCellValue(sheet, cell(10, n), r.Diperiksa)
			f.SetCellValue(sheet, cell(11, n), r.Error)
			f.SetCellValue(sheet, cell(12, n), roundPct(r.PctSubmit))
			f.SetCellValue(sheet, cell(13, n), roundPct(r.PctTerverifikasi))
			f.SetCellValue(sheet, cell(14, n), roundPct(r.PctCoverageUsahaBKU))
			f.SetCellValue(sheet, cell(15, n), roundPct(r.PctCoverageUsahaKeluarga))
			f.SetCellValue(sheet, cell(16, n), roundPct(r.PctCoverageKeluarga))
		}
	})
}

func roundPct(v float64) float64 {
	return math.Round(v*100) / 100
}

// downloadWideAgregat — dipakai bareng oleh DownloadKBLI & DownloadKeberadaanRekap:
// tabel lebar per SLS, 1 kolom per indikator, dari sebuah tabel agregat generik
// (skema sama seperti adminWideAgregatTable di kbli.go).
func downloadWideAgregat(c echo.Context, table, filenamePrefix string) error {
	q := c.QueryParam("q")
	like := "%" + q + "%"

	indikatorList := queryAgregatIndikatorList(table)

	rows, err := db.DB.Query(`
		SELECT s.id, s.kode_sls, s.nama_sls, COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''),
		       ppl.name, pml.name
		FROM sls s
		JOIN users ppl ON ppl.id = s.ppl_id
		JOIN users pml ON pml.id = s.pml_id
		WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
		  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
		ORDER BY s.kode_kec, s.kode_desa, s.kode_sls`,
		like, like, like, like, like)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()

	var slsIDs []int
	bySLS := map[int]*WideAgregatRow{}
	var list []*WideAgregatRow
	for rows.Next() {
		var r WideAgregatRow
		rows.Scan(&r.ID, &r.KodeSLS, &r.NamaSLS, &r.NamaKec, &r.NamaDesa, &r.NamaPPL, &r.NamaPML)
		r.Values = map[string]int{}
		list = append(list, &r)
		bySLS[r.ID] = &r
		slsIDs = append(slsIDs, r.ID)
	}

	if len(slsIDs) > 0 {
		placeholders := make([]string, len(slsIDs))
		args := make([]interface{}, len(slsIDs))
		for i, id := range slsIDs {
			placeholders[i] = "?"
			args[i] = id
		}
		valRows, err := db.DB.Query(fmt.Sprintf(`
			SELECT sls_id, kode_indikator, COALESCE(total_value,0)
			FROM %s
			WHERE sls_id IN (%s)`, table, strings.Join(placeholders, ",")), args...)
		if err == nil {
			defer valRows.Close()
			for valRows.Next() {
				var slsID int
				var kode string
				var val int
				valRows.Scan(&slsID, &kode, &val)
				if r, ok := bySLS[slsID]; ok {
					r.Values[kode] = val
					r.Total += val
				}
			}
		}
	}

	fname := fmt.Sprintf("%s_%s.xlsx", filenamePrefix, time.Now().In(wita).Format("20060102"))
	headers := []string{"Kode SLS", "Nama SLS", "PPL", "PML", "Desa", "Kecamatan", "Total"}
	for _, k := range indikatorList {
		headers = append(headers, k.Nama)
	}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range list {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.KodeSLS)
			f.SetCellValue(sheet, cell(2, n), r.NamaSLS)
			f.SetCellValue(sheet, cell(3, n), r.NamaPPL)
			f.SetCellValue(sheet, cell(4, n), r.NamaPML)
			f.SetCellValue(sheet, cell(5, n), r.NamaDesa)
			f.SetCellValue(sheet, cell(6, n), r.NamaKec)
			f.SetCellValue(sheet, cell(7, n), r.Total)
			for j, k := range indikatorList {
				f.SetCellValue(sheet, cell(8+j, n), r.Values[k.Kode])
			}
		}
	})
}

// DownloadKBLI — GET /admin/download/kbli
func DownloadKBLI(c echo.Context) error {
	return downloadWideAgregat(c, "kbli_usaha", "monitoring_kbli")
}

// DownloadKeberadaanRekap — GET /admin/download/keberadaan-rekap
func DownloadKeberadaanRekap(c echo.Context) error {
	return downloadWideAgregat(c, "coverage_usaha_keluarga", "monitoring_rekap_keberadaan")
}
