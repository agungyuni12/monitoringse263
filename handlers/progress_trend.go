package handlers

import (
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

// ===== Snapshot harian =====

// captureDailyProgressTrendSnapshot mengambil agregat progres FASIH saat ini
// (submit/draft/approved/rejected/revoked di level Pengawas) dan menyimpannya
// sebagai satu titik data historis per PPL dan per PML untuk tanggal hari ini
// (WITA). Tanggal dihitung di Go (bukan CURDATE() MySQL) supaya tidak bergeser
// kalau timezone server database berbeda dari WITA.
// Aman dipanggil berkali-kali dalam satu hari (upsert per tanggal).
func captureDailyProgressTrendSnapshot() error {
	tanggal := time.Now().In(witaLocation()).Format("2006-01-02")

	const upsertTpl = `
		INSERT INTO progress_trend (entity_type, entity_id, tanggal, jumlah_submit, jumlah_draft, approved, rejected, revoked)
		SELECT '%s', s.%s, ?,
		       COALESCE(SUM(p.jumlah_submit),0), COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0),
		       COALESCE(SUM(p.fasih_rejected_pengawas),0),
		       COALESCE(SUM(p.fasih_revoked_pengawas),0)
		FROM sls s
		LEFT JOIN progress p ON p.sls_id = s.id
		GROUP BY s.%s
		ON DUPLICATE KEY UPDATE
		  jumlah_submit = VALUES(jumlah_submit),
		  jumlah_draft  = VALUES(jumlah_draft),
		  approved      = VALUES(approved),
		  rejected      = VALUES(rejected),
		  revoked       = VALUES(revoked)`

	if _, err := db.DB.Exec(fmt.Sprintf(upsertTpl, "ppl", "ppl_id", "ppl_id"), tanggal); err != nil {
		return fmt.Errorf("snapshot ppl: %w", err)
	}
	if _, err := db.DB.Exec(fmt.Sprintf(upsertTpl, "pml", "pml_id", "pml_id"), tanggal); err != nil {
		return fmt.Errorf("snapshot pml: %w", err)
	}
	return nil
}

// witaLocation mengembalikan zona waktu Asia/Makassar (WITA), sama dengan yang
// dipakai scraper/sync_fasih.py untuk menjadwalkan sync FASIH pertama tiap hari.
func witaLocation() *time.Location {
	if loc, err := time.LoadLocation("Asia/Makassar"); err == nil {
		return loc
	}
	return time.FixedZone("WITA", 8*60*60)
}

// StartProgressTrendScheduler menjalankan snapshot harian secara otomatis.
// Dijalankan sekali saat startup (agar ada data walau server baru di-deploy),
// lalu setiap hari jam 07:00 WITA — 30 menit setelah sync FASIH pertama hari itu
// (dimulai 06:30 WITA, lihat scraper/sync_fasih.py) supaya snapshot merekam hasil
// sync tersebut, bukan data basi dari sebelum sync selesai.
func StartProgressTrendScheduler() {
	if err := captureDailyProgressTrendSnapshot(); err != nil {
		log.Printf("[TREND] Snapshot awal gagal: %v", err)
	} else {
		log.Println("[TREND] Snapshot awal tersimpan.")
	}

	go func() {
		const hour, minute = 7, 0
		loc := witaLocation()
		for {
			now := time.Now().In(loc)
			next := time.Date(now.Year(), now.Month(), now.Day(), hour, minute, 0, 0, loc)
			if !next.After(now) {
				next = next.Add(24 * time.Hour)
			}
			time.Sleep(next.Sub(now))
			if err := captureDailyProgressTrendSnapshot(); err != nil {
				log.Printf("[TREND] Snapshot harian gagal: %v", err)
				continue
			}
			log.Println("[TREND] Snapshot harian tersimpan.")
		}
	}()
}

// ===== Query & tampilan =====

type trendPoint struct {
	Tanggal  string
	Submit   int
	Draft    int
	Approved int
	Rejected int
	Revoked  int
}

// PPLTrendRow satu baris tren untuk seorang PPL.
type PPLTrendRow struct {
	ID      int
	Name    string
	PMLName string

	Submit, Draft, Approved, Rejected int

	ChartW, ChartH                                float64
	SubmitPts, ApprovedPts, RejectedPts, DraftPts string
	HasHistory                                    bool
	LastDate                                      string
	DeltaSubmit, DeltaApproved, DeltaRejected     int
}

// PMLTrendRow satu baris tren untuk seorang PML.
type PMLTrendRow struct {
	ID   int
	Name string

	Approved, Rejected, Revoked int

	ChartW, ChartH                             float64
	ApprovedPts, RejectedPts, RevokedPts       string
	HasHistory                                 bool
	LastDate                                   string
	DeltaApproved, DeltaRejected, DeltaRevoked int
}

const trendChartW = 110.0
const trendChartH = 30.0

// sparklinePoints menghasilkan string "x,y x,y ..." untuk <polyline points="...">,
// dengan skala Y dibagi bersama antar beberapa seri (agar sebanding satu sama lain).
func sparklinePoints(series []int, sharedMax int, w, h float64) string {
	n := len(series)
	if n == 0 {
		return ""
	}
	if sharedMax <= 0 {
		sharedMax = 1
	}
	stepX := w
	if n > 1 {
		stepX = w / float64(n-1)
	}
	parts := make([]string, n)
	for i, v := range series {
		x := stepX * float64(i)
		if n == 1 {
			x = w / 2
		}
		y := h - (float64(v)/float64(sharedMax))*h
		parts[i] = fmt.Sprintf("%.1f,%.1f", x, y)
	}
	return strings.Join(parts, " ")
}

func sharedMaxOf(seriesList ...[]int) int {
	m := 0
	for _, s := range seriesList {
		for _, v := range s {
			if v > m {
				m = v
			}
		}
	}
	return m
}

func fetchTrendHistory(entityType string, days int) map[int][]trendPoint {
	cutoff := time.Now().In(witaLocation()).AddDate(0, 0, -days).Format("2006-01-02")
	rows, err := db.DB.Query(`
		SELECT entity_id, DATE_FORMAT(tanggal,'%d/%m'), jumlah_submit, jumlah_draft, approved, rejected, revoked
		FROM progress_trend
		WHERE entity_type = ? AND tanggal >= ?
		ORDER BY entity_id, tanggal`, entityType, cutoff)
	result := map[int][]trendPoint{}
	if err != nil {
		return result
	}
	defer rows.Close()
	for rows.Next() {
		var id int
		var p trendPoint
		if err := rows.Scan(&id, &p.Tanggal, &p.Submit, &p.Draft, &p.Approved, &p.Rejected, &p.Revoked); err != nil {
			continue
		}
		result[id] = append(result[id], p)
	}
	return result
}

func queryTrendPPL(q string, days, page int) ([]PPLTrendRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := fmt.Sprintf("&days=%d", days)
	if q != "" {
		extra += "&q=" + q
	}

	var total int
	db.DB.QueryRow(`
		SELECT COUNT(DISTINCT u.id) FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		WHERE u.role = 'ppl' AND (u.name LIKE ? OR pml.name LIKE ?)`, like, like).Scan(&total)
	pageInfo := models.NewPageInfo(page, total, "/admin/table/trend/ppl", "admin-trend-ppl-wrap", extra)

	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT u.id, u.name, pml.name,
		       COALESCE(SUM(p.jumlah_submit),0), COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0), COALESCE(SUM(p.fasih_rejected_pengawas),0)
		FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE u.role = 'ppl' AND (u.name LIKE ? OR pml.name LIKE ?)
		GROUP BY u.id, u.name, pml.name
		ORDER BY pml.name, u.name
		LIMIT ? OFFSET ?`, like, like, models.PerPage, offset)
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []PPLTrendRow
	for rows.Next() {
		var r PPLTrendRow
		rows.Scan(&r.ID, &r.Name, &r.PMLName, &r.Submit, &r.Draft, &r.Approved, &r.Rejected)
		list = append(list, r)
	}

	history := fetchTrendHistory("ppl", days)
	for i := range list {
		pts := history[list[i].ID]
		if len(pts) == 0 {
			continue
		}
		var submitS, draftS, approvedS, rejectedS []int
		for _, p := range pts {
			submitS = append(submitS, p.Submit)
			draftS = append(draftS, p.Draft)
			approvedS = append(approvedS, p.Approved)
			rejectedS = append(rejectedS, p.Rejected)
		}
		maxV := sharedMaxOf(submitS, approvedS, rejectedS)
		list[i].ChartW, list[i].ChartH = trendChartW, trendChartH
		list[i].SubmitPts = sparklinePoints(submitS, maxV, trendChartW, trendChartH)
		list[i].ApprovedPts = sparklinePoints(approvedS, maxV, trendChartW, trendChartH)
		list[i].RejectedPts = sparklinePoints(rejectedS, maxV, trendChartW, trendChartH)
		list[i].HasHistory = len(pts) >= 2
		last := pts[len(pts)-1]
		list[i].LastDate = last.Tanggal
		if len(pts) >= 2 {
			prev := pts[len(pts)-2]
			list[i].DeltaSubmit = last.Submit - prev.Submit
			list[i].DeltaApproved = last.Approved - prev.Approved
			list[i].DeltaRejected = last.Rejected - prev.Rejected
		}
	}
	return list, pageInfo
}

func queryTrendPML(q string, days, page int) ([]PMLTrendRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := fmt.Sprintf("&days=%d", days)
	if q != "" {
		extra += "&q=" + q
	}

	var total int
	db.DB.QueryRow(`SELECT COUNT(*) FROM users u WHERE u.role = 'pml' AND u.name LIKE ?`, like).Scan(&total)
	pageInfo := models.NewPageInfo(page, total, "/admin/table/trend/pml", "admin-trend-pml-wrap", extra)

	offset := (page - 1) * models.PerPage
	rows, err := db.DB.Query(`
		SELECT u.id, u.name,
		       COALESCE(SUM(p.fasih_approved_pengawas),0), COALESCE(SUM(p.fasih_rejected_pengawas),0), COALESCE(SUM(p.fasih_revoked_pengawas),0)
		FROM users u
		JOIN sls s ON s.pml_id = u.id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE u.role = 'pml' AND u.name LIKE ?
		GROUP BY u.id, u.name
		ORDER BY u.name
		LIMIT ? OFFSET ?`, like, models.PerPage, offset)
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []PMLTrendRow
	for rows.Next() {
		var r PMLTrendRow
		rows.Scan(&r.ID, &r.Name, &r.Approved, &r.Rejected, &r.Revoked)
		list = append(list, r)
	}

	history := fetchTrendHistory("pml", days)
	for i := range list {
		pts := history[list[i].ID]
		if len(pts) == 0 {
			continue
		}
		var approvedS, rejectedS, revokedS []int
		for _, p := range pts {
			approvedS = append(approvedS, p.Approved)
			rejectedS = append(rejectedS, p.Rejected)
			revokedS = append(revokedS, p.Revoked)
		}
		maxV := sharedMaxOf(approvedS, rejectedS, revokedS)
		list[i].ChartW, list[i].ChartH = trendChartW, trendChartH
		list[i].ApprovedPts = sparklinePoints(approvedS, maxV, trendChartW, trendChartH)
		list[i].RejectedPts = sparklinePoints(rejectedS, maxV, trendChartW, trendChartH)
		list[i].RevokedPts = sparklinePoints(revokedS, maxV, trendChartW, trendChartH)
		list[i].HasHistory = len(pts) >= 2
		last := pts[len(pts)-1]
		list[i].LastDate = last.Tanggal
		if len(pts) >= 2 {
			prev := pts[len(pts)-2]
			list[i].DeltaApproved = last.Approved - prev.Approved
			list[i].DeltaRejected = last.Rejected - prev.Rejected
			list[i].DeltaRevoked = last.Revoked - prev.Revoked
		}
	}
	return list, pageInfo
}

func parseTrendDays(v string) int {
	days, _ := strconv.Atoi(v)
	if days != 7 && days != 30 {
		return 14
	}
	return days
}

// AdminTableTrendPPL melayani sub-tabel "Tren per PPL" (submit/approve/reject).
func AdminTableTrendPPL(c echo.Context) error {
	q := c.QueryParam("q")
	days := parseTrendDays(c.QueryParam("days"))
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	list, pageInfo := queryTrendPPL(q, days, page)
	return c.Render(http.StatusOK, "admin_trend_ppl_table.html", map[string]interface{}{
		"PPLTrend":     list,
		"PPLTrendPage": pageInfo,
		"Days":         days,
		"Q":            q,
	})
}

// AdminTableTrendPML melayani sub-tabel "Tren per PML" (approve/reject/revoke).
func AdminTableTrendPML(c echo.Context) error {
	q := c.QueryParam("q")
	days := parseTrendDays(c.QueryParam("days"))
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	list, pageInfo := queryTrendPML(q, days, page)
	return c.Render(http.StatusOK, "admin_trend_pml_table.html", map[string]interface{}{
		"PMLTrend":     list,
		"PMLTrendPage": pageInfo,
		"Days":         days,
		"Q":            q,
	})
}
