package handlers

import (
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
	"github.com/xuri/excelize/v2"
)

// PembayaranRow menampilkan status "Bisa Bayar" per PPL: layak dibayar hanya
// kalau SEMUA datanya sudah Approved (tidak ada sisa Non Approved) DAN SEMUA
// SLS-nya progresnya benar-benar 100% (bukan ambang >=95% seperti "Persentase
// SLS" di tab Per PPL — pembayaran butuh kepastian penuh, bukan "hampir
// selesai").
type PembayaranRow struct {
	ID            int
	Name          string
	PMLName       string
	JmlSLS        int
	FasihTotal    int
	Approved      int
	NonApproved   int
	PctApproved   float64
	PctSLSSelesai float64
	BisaBayar     bool
}

var adminPembayaranSortCols = map[string]string{
	"nama":     "u.name",
	"pml":      "pml.name",
	"jml_sls":  "COUNT(s.id)",
	"total":    "COALESCE(SUM(p.fasih_total),0)",
	"approved": approvedColSQLAgg,
}

func queryAdminPembayaran(page int, q string, pmlID int, sort, dir string) ([]PembayaranRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := ""
	if q != "" {
		extra = "&q=" + q
	}
	if pmlID > 0 {
		extra += fmt.Sprintf("&pml_id=%d", pmlID)
	}

	pmlFilter := ""
	var countArgs, queryArgs []interface{}
	offset := (page - 1) * models.PerPage
	if pmlID > 0 {
		pmlFilter = " AND s.pml_id = ?"
		countArgs = []interface{}{pmlID, like, like}
		queryArgs = []interface{}{pmlID, like, like, models.PerPage, offset}
	} else {
		countArgs = []interface{}{like, like}
		queryArgs = []interface{}{like, like, models.PerPage, offset}
	}

	var total int
	db.DB.QueryRow(`SELECT COUNT(DISTINCT u.id) FROM users u JOIN sls s ON s.ppl_id=u.id JOIN users pml ON pml.id=s.pml_id WHERE u.role='ppl'`+pmlFilter+` AND (u.name LIKE ? OR pml.name LIKE ?)`, countArgs...).Scan(&total)

	sortCols := make(map[string]string, len(adminPembayaranSortCols))
	for k, v := range adminPembayaranSortCols {
		sortCols[k] = v
	}
	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, sortCols, "pml.name, u.name")

	rows, err := db.DB.Query(`
		SELECT u.id, u.name, pml.name,
		       COUNT(s.id),
		       COALESCE(SUM(p.fasih_total),0),
		       `+approvedColSQLAgg+`
		FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE u.role = 'ppl'`+pmlFilter+` AND (u.name LIKE ? OR pml.name LIKE ?)
		GROUP BY u.id, u.name, pml.name
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	pageInfo := models.NewPageInfo(page, total, "/admin/table/pembayaran", "admin-pembayaran-wrap", extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []PembayaranRow
	for rows.Next() {
		var r PembayaranRow
		rows.Scan(&r.ID, &r.Name, &r.PMLName, &r.JmlSLS, &r.FasihTotal, &r.Approved)
		r.NonApproved = maxInt(r.FasihTotal-r.Approved, 0)
		if r.FasihTotal > 0 {
			r.PctApproved = math.Min(float64(r.Approved)*100/float64(r.FasihTotal), 100)
		}
		list = append(list, r)
	}

	fillPctSLSSelesaiPenuh(list)
	for i := range list {
		list[i].BisaBayar = list[i].FasihTotal > 0 && list[i].NonApproved == 0 && list[i].PctSLSSelesai == 100
	}
	return list, pageInfo
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// slsSelesaiPenuhThreshold: ambang MURNI 100% (bukan >=95% seperti
// "Persentase SLS" di tab Per PPL, lihat slsSelesaiThreshold) — dipakai
// KHUSUS untuk menentukan syarat "Bisa Bayar", karena pembayaran mensyaratkan
// SLS-nya benar-benar tuntas, bukan "hampir tuntas".
const slsSelesaiPenuhThreshold = 1.0

// fillPctSLSSelesaiPenuh mengisi PctSLSSelesai tiap PembayaranRow: dari semua
// SLS milik PPL itu, berapa persen yang progres per-SLS-nya (jumlah_submit /
// fasih_total) sudah PERSIS >=100%. Tidak metode-aware seperti fillPctSLSSelesai
// karena tab Pembayaran tidak punya dropdown metode — selalu Total/Total.
func fillPctSLSSelesaiPenuh(list []PembayaranRow) {
	if len(list) == 0 {
		return
	}
	byID := make(map[int]*PembayaranRow, len(list))
	placeholders := make([]string, len(list))
	args := make([]interface{}, len(list))
	for i := range list {
		byID[list[i].ID] = &list[i]
		placeholders[i] = "?"
		args[i] = list[i].ID
	}

	pctExpr := progresSortExprGeneric(MetodeTotalVsTotal, "COALESCE(p.jumlah_submit,0)", "COALESCE(p.fasih_total,0)", "s.target_prelist_resmi")
	rows, err := db.DB.Query(`
		SELECT s.ppl_id, COUNT(*),
		       SUM(CASE WHEN `+pctExpr+` >= `+strconv.FormatFloat(slsSelesaiPenuhThreshold, 'f', -1, 64)+` THEN 1 ELSE 0 END)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE s.ppl_id IN (`+strings.Join(placeholders, ",")+`)
		GROUP BY s.ppl_id`, args...)
	if err != nil {
		return
	}
	defer rows.Close()
	for rows.Next() {
		var pplID, totalSLS, doneSLS int
		if err := rows.Scan(&pplID, &totalSLS, &doneSLS); err != nil {
			continue
		}
		if r, ok := byID[pplID]; ok && totalSLS > 0 {
			r.PctSLSSelesai = math.Min(float64(doneSLS)*100/float64(totalSLS), 100)
		}
	}
}

func AdminTablePembayaran(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	sort := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	list, pageInfo := queryAdminPembayaran(page, q, pmlID, sort, dir)
	return c.Render(http.StatusOK, "admin_pembayaran_table.html", map[string]interface{}{
		"Pembayarans": list, "PembayaranPage": pageInfo,
	})
}

func DownloadPembayaran(c echo.Context) error {
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
		SELECT u.id, u.name, pml.name,
		       COUNT(s.id),
		       COALESCE(SUM(p.fasih_total),0),
		       `+approvedColSQLAgg+`
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

	var list []PembayaranRow
	for rows.Next() {
		var r PembayaranRow
		rows.Scan(&r.ID, &r.Name, &r.PMLName, &r.JmlSLS, &r.FasihTotal, &r.Approved)
		r.NonApproved = maxInt(r.FasihTotal-r.Approved, 0)
		if r.FasihTotal > 0 {
			r.PctApproved = math.Min(float64(r.Approved)*100/float64(r.FasihTotal), 100)
		}
		list = append(list, r)
	}
	fillPctSLSSelesaiPenuh(list)
	for i := range list {
		list[i].BisaBayar = list[i].FasihTotal > 0 && list[i].NonApproved == 0 && list[i].PctSLSSelesai == 100
	}

	fname := fmt.Sprintf("monitoring_pembayaran_%s.xlsx", time.Now().In(wita).Format("20060102"))
	headers := []string{"Nama PPL", "Supervisor (PML)", "Jml SLS", "Approved", "Non Approved", "% Approved", "% SLS Selesai", "Bisa Bayar"}
	return writeXlsx(c, fname, headers, func(f *excelize.File, sheet string) {
		for i, r := range list {
			n := i + 2
			f.SetCellValue(sheet, cell(1, n), r.Name)
			f.SetCellValue(sheet, cell(2, n), r.PMLName)
			f.SetCellValue(sheet, cell(3, n), r.JmlSLS)
			f.SetCellValue(sheet, cell(4, n), r.Approved)
			f.SetCellValue(sheet, cell(5, n), r.NonApproved)
			f.SetCellValue(sheet, cell(6, n), roundPct(r.PctApproved))
			f.SetCellValue(sheet, cell(7, n), roundPct(r.PctSLSSelesai))
			bayar := "Belum Bisa Dibayar"
			if r.BisaBayar {
				bayar = "Bisa Dibayar"
			}
			f.SetCellValue(sheet, cell(8, n), bayar)
		}
	})
}
