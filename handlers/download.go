package handlers

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
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

	// Header style
	bold, _ := f.NewStyle(&excelize.Style{
		Font: &excelize.Font{Bold: true, Color: "FFFFFF"},
		Fill: excelize.Fill{Type: "pattern", Color: []string{"F37021"}, Pattern: 1},
		Alignment: &excelize.Alignment{Horizontal: "center"},
	})

	for i, h := range headers {
		cell, _ := excelize.CoordinatesToCellName(i+1, 1)
		f.SetCellValue(sheet, cell, h)
		f.SetCellStyle(sheet, cell, cell, bold)
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
		       COALESCE(SUM(lh.js),0), COALESCE(SUM(lh.jd),0),
		       COALESCE(SUM(vh.jp),0), COALESCE(SUM(vh.je),0)
		FROM users u
		JOIN sls s ON s.pml_id = u.id
		LEFT JOIN (SELECT sls_id, SUM(jumlah_submit) as js, SUM(jumlah_draft) as jd FROM laporan_harian GROUP BY sls_id) lh ON lh.sls_id = s.id
		LEFT JOIN (SELECT sls_id, SUM(jumlah_diperiksa) as jp, SUM(jumlah_error) as je FROM verifikasi_harian GROUP BY sls_id) vh ON vh.sls_id = s.id
		WHERE u.role = 'pml' AND u.name LIKE ?
		GROUP BY u.id, u.name ORDER BY u.name`, like)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()
	type row struct{ name string; jmlPPL, jmlSLS, submit, draft, approved, rejected int }
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.name, &r.jmlPPL, &r.jmlSLS, &r.submit, &r.draft, &r.approved, &r.rejected)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_pml_%s.xlsx", time.Now().Format("20060102"))
	headers := []string{"Nama PML", "Jml PPL", "Jml SLS", "Submit", "Draft", "Approved", "Rejected"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range data {
			rowNum := i + 2
			f.SetCellValue(sheet, cell(1, rowNum), r.name)
			f.SetCellValue(sheet, cell(2, rowNum), r.jmlPPL)
			f.SetCellValue(sheet, cell(3, rowNum), r.jmlSLS)
			f.SetCellValue(sheet, cell(4, rowNum), r.submit)
			f.SetCellValue(sheet, cell(5, rowNum), r.draft)
			f.SetCellValue(sheet, cell(6, rowNum), r.approved)
			f.SetCellValue(sheet, cell(7, rowNum), r.rejected)
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
		args = []interface{}{pmlID, like, like}
	} else {
		args = []interface{}{like, like}
	}

	rows, err := db.DB.Query(`
		SELECT u.name, pml.name, COUNT(s.id),
		       COALESCE(SUM(lh.jumlah_submit),0), COALESCE(SUM(lh.jumlah_draft),0),
		       COALESCE(SUM(s.target),0)
		FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN laporan_harian lh ON lh.sls_id = s.id
		WHERE u.role = 'ppl'`+pmlFilter+` AND (u.name LIKE ? OR pml.name LIKE ?)
		GROUP BY u.id, u.name, pml.name ORDER BY pml.name, u.name`, args...)
	if err != nil {
		return c.String(http.StatusInternalServerError, err.Error())
	}
	defer rows.Close()
	type row struct{ ppl, pml string; jmlSLS, submit, draft, target int }
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.ppl, &r.pml, &r.jmlSLS, &r.submit, &r.draft, &r.target)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_ppl_%s.xlsx", time.Now().Format("20060102"))
	headers := []string{"Nama PPL", "Nama PML", "Jml SLS", "Submit", "Draft", "Target"}
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
	fname := fmt.Sprintf("monitoring_%s_%s.xlsx", suffix, time.Now().Format("20060102"))

	switch level {
	case "kec":
		rows, err := db.DB.Query(`
			SELECT s.nama_kec, COUNT(DISTINCT s.id),
			       COALESCE(SUM(s.target),0),
			       COALESCE(SUM(lh.js),0), COALESCE(SUM(lh.jd),0),
			       COALESCE(SUM(vh.jp),0), COALESCE(SUM(vh.je),0)
			FROM sls s
			LEFT JOIN (SELECT sls_id, SUM(jumlah_submit) as js, SUM(jumlah_draft) as jd FROM laporan_harian GROUP BY sls_id) lh ON lh.sls_id = s.id
			LEFT JOIN (SELECT sls_id, SUM(jumlah_diperiksa) as jp, SUM(jumlah_error) as je FROM verifikasi_harian GROUP BY sls_id) vh ON vh.sls_id = s.id
			WHERE s.nama_kec LIKE ?
			GROUP BY s.nama_kec, s.kode_kec ORDER BY s.kode_kec`, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct{ kec string; jml, target, submit, draft, approved, rejected int }
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.kec, &r.jml, &r.target, &r.submit, &r.draft, &r.approved, &r.rejected)
			data = append(data, r)
		}
		headers := []string{"Kecamatan", "Jml SLS", "Target", "Submit", "Draft", "Approved", "Rejected"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				f.SetCellValue(sheet, cell(1, n), r.kec)
				f.SetCellValue(sheet, cell(2, n), r.jml)
				f.SetCellValue(sheet, cell(3, n), r.target)
				f.SetCellValue(sheet, cell(4, n), r.submit)
				f.SetCellValue(sheet, cell(5, n), r.draft)
				f.SetCellValue(sheet, cell(6, n), r.approved)
				f.SetCellValue(sheet, cell(7, n), r.rejected)
			}
		})
	case "desa":
		rows, err := db.DB.Query(`
			SELECT s.nama_desa, s.nama_kec, COUNT(DISTINCT s.id),
			       COALESCE(SUM(s.target),0),
			       COALESCE(SUM(lh.js),0), COALESCE(SUM(lh.jd),0),
			       COALESCE(SUM(vh.jp),0), COALESCE(SUM(vh.je),0)
			FROM sls s
			LEFT JOIN (SELECT sls_id, SUM(jumlah_submit) as js, SUM(jumlah_draft) as jd FROM laporan_harian GROUP BY sls_id) lh ON lh.sls_id = s.id
			LEFT JOIN (SELECT sls_id, SUM(jumlah_diperiksa) as jp, SUM(jumlah_error) as je FROM verifikasi_harian GROUP BY sls_id) vh ON vh.sls_id = s.id
			WHERE s.nama_desa LIKE ? OR s.nama_kec LIKE ?
			GROUP BY s.nama_desa, s.nama_kec, s.kode_desa, s.kode_kec
			ORDER BY s.kode_kec, s.kode_desa`, like, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct{ desa, kec string; jml, target, submit, draft, approved, rejected int }
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.desa, &r.kec, &r.jml, &r.target, &r.submit, &r.draft, &r.approved, &r.rejected)
			data = append(data, r)
		}
		headers := []string{"Desa", "Kecamatan", "Jml SLS", "Target", "Submit", "Draft", "Approved", "Rejected"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				f.SetCellValue(sheet, cell(1, n), r.desa)
				f.SetCellValue(sheet, cell(2, n), r.kec)
				f.SetCellValue(sheet, cell(3, n), r.jml)
				f.SetCellValue(sheet, cell(4, n), r.target)
				f.SetCellValue(sheet, cell(5, n), r.submit)
				f.SetCellValue(sheet, cell(6, n), r.draft)
				f.SetCellValue(sheet, cell(7, n), r.approved)
				f.SetCellValue(sheet, cell(8, n), r.rejected)
			}
		})
	default:
		rows, err := db.DB.Query(`
			SELECT s.kode_sls, s.nama_sls, ppl.name, pml.name,
			       COALESCE(s.nama_desa,''), COALESCE(s.nama_kec,''), s.target,
			       COALESCE(lh.js,0), COALESCE(lh.jd,0),
			       COALESCE(vh.jp,0), COALESCE(vh.je,0)
			FROM sls s
			JOIN users ppl ON ppl.id = s.ppl_id
			JOIN users pml ON pml.id = s.pml_id
			LEFT JOIN (SELECT sls_id, SUM(jumlah_submit) as js, SUM(jumlah_draft) as jd FROM laporan_harian GROUP BY sls_id) lh ON lh.sls_id = s.id
			LEFT JOIN (SELECT sls_id, SUM(jumlah_diperiksa) as jp, SUM(jumlah_error) as je FROM verifikasi_harian GROUP BY sls_id) vh ON vh.sls_id = s.id
			WHERE s.nama_sls LIKE ? OR ppl.name LIKE ? OR pml.name LIKE ?
			  OR s.nama_kec LIKE ? OR s.nama_desa LIKE ?
			ORDER BY s.kode_kec, s.kode_desa, s.kode_sls`,
			like, like, like, like, like)
		if err != nil {
			return c.String(http.StatusInternalServerError, err.Error())
		}
		defer rows.Close()
		type row struct {
			kode, nama, ppl, pml, desa, kec string
			target, submit, draft, approved, rejected int
		}
		var data []row
		for rows.Next() {
			var r row
			rows.Scan(&r.kode, &r.nama, &r.ppl, &r.pml, &r.desa, &r.kec,
				&r.target, &r.submit, &r.draft, &r.approved, &r.rejected)
			data = append(data, r)
		}
		headers := []string{"Kode SLS", "Nama SLS", "PPL", "PML", "Desa", "Kecamatan", "Target", "Submit", "Draft", "Approved", "Rejected"}
		return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
			for i, r := range data {
				n := i + 2
				f.SetCellValue(sheet, cell(1, n), r.kode)
				f.SetCellValue(sheet, cell(2, n), r.nama)
				f.SetCellValue(sheet, cell(3, n), r.ppl)
				f.SetCellValue(sheet, cell(4, n), r.pml)
				f.SetCellValue(sheet, cell(5, n), r.desa)
				f.SetCellValue(sheet, cell(6, n), r.kec)
				f.SetCellValue(sheet, cell(7, n), r.target)
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
		diawasi int
	}
	var data []row
	for rows.Next() {
		var r row
		rows.Scan(&r.org, &r.sls, &r.desa, &r.kec, &r.ppl, &r.pml, &r.tgl, &r.diawasi, &r.kendala, &r.solusi)
		data = append(data, r)
	}

	fname := fmt.Sprintf("monitoring_organik_%s.xlsx", time.Now().Format("20060102"))
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

	// Sort by tanggal DESC, sumber ASC
	sort.SliceStable(data, func(i, j int) bool {
		if data[i].tgl != data[j].tgl {
			return data[i].tgl > data[j].tgl
		}
		return data[i].sumber < data[j].sumber
	})

	fname := fmt.Sprintf("daftar_kendala_%s.xlsx", time.Now().Format("20060102"))
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

func cell(col, row int) string {
	name, _ := excelize.CoordinatesToCellName(col, row)
	return name
}
