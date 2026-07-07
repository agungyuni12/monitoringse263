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
//
// Kalau snapshot untuk tanggal hari ini SUDAH ada, fungsi ini tidak melakukan
// apa-apa — supaya redeploy berkali-kali dalam satu hari tidak menimpa ulang
// titik data yang sudah terekam (satu hari = satu titik data, siapa pun/apa
// pun yang memicunya duluan, entah redeploy atau jadwal 07:00).
func captureDailyProgressTrendSnapshot() error {
	tanggal := time.Now().In(witaLocation()).Format("2006-01-02")

	var exists int
	if err := db.DB.QueryRow(`SELECT COUNT(*) FROM progress_trend WHERE tanggal = ?`, tanggal).Scan(&exists); err != nil {
		return fmt.Errorf("cek snapshot: %w", err)
	}
	if exists > 0 {
		log.Printf("[TREND] Snapshot %s sudah ada, dilewati.", tanggal)
		return nil
	}

	const insertTpl = `
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

	if _, err := db.DB.Exec(fmt.Sprintf(insertTpl, "ppl", "ppl_id", "ppl_id"), tanggal); err != nil {
		return fmt.Errorf("snapshot ppl: %w", err)
	}
	if _, err := db.DB.Exec(fmt.Sprintf(insertTpl, "pml", "pml_id", "pml_id"), tanggal); err != nil {
		return fmt.Errorf("snapshot pml: %w", err)
	}
	log.Printf("[TREND] Snapshot %s tersimpan.", tanggal)
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
			}
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

	// Submit = fasih_submitted (pending review PML), sama seperti kolom "Submit" di
	// tab "Per PPL" — bukan jumlah_submit. Kecepatan/tren di bawah tetap pakai
	// jumlah_submit (lihat DeltaTotal/VelocityPts) supaya tidak naik-turun mengikuti
	// antrian pending.
	Submit, Draft, Approved, Rejected int

	ChartW, ChartH                            float64
	HasHistory                                bool
	LastDate                                  string
	DeltaSubmit, DeltaApproved, DeltaRejected int

	// Kecepatan progres (total = jumlah_submit, sudah mencakup submit/approve/reject/revoke/level admin).
	// Membandingkan kenaikan hari ini vs kenaikan hari sebelumnya, butuh minimal 3 titik data.
	HasAccelData                             bool
	DeltaTotal, PrevDeltaTotal, Acceleration int
	// Tren kecepatan (selisih harian) sepanjang periode, bukan cuma 2 hari terakhir.
	VelocityPts   string
	VelocityZeroY float64
}

// PMLTrendRow satu baris tren untuk seorang PML.
type PMLTrendRow struct {
	ID   int
	Name string

	Approved, Rejected, Revoked int

	ChartW, ChartH                             float64
	HasHistory                                 bool
	LastDate                                   string
	DeltaApproved, DeltaRejected, DeltaRevoked int

	// Kecepatan progres (total = approved+rejected+revoked, aktivitas verifikasi PML).
	// Membandingkan kenaikan hari ini vs kenaikan hari sebelumnya, butuh minimal 3 titik data.
	HasAccelData                             bool
	DeltaTotal, PrevDeltaTotal, Acceleration int
	// Tren kecepatan (selisih harian) sepanjang periode, bukan cuma 2 hari terakhir.
	VelocityPts   string
	VelocityZeroY float64
}

const trendChartW = 110.0
const trendChartH = 30.0

// sparklinePointsSigned menghasilkan garis untuk seri yang bisa negatif (mis. selisih
// harian/kecepatan), dengan skalanya sendiri (bukan shared) dan selalu menyertakan
// angka 0 dalam rentang supaya garis referensi nol (zeroY) tetap relevan digambar.
func sparklinePointsSigned(series []int, w, h float64) (points string, zeroY float64) {
	n := len(series)
	if n == 0 {
		return "", h / 2
	}
	minV, maxV := series[0], series[0]
	for _, v := range series {
		if v < minV {
			minV = v
		}
		if v > maxV {
			maxV = v
		}
	}
	if minV > 0 {
		minV = 0
	}
	if maxV < 0 {
		maxV = 0
	}
	rangeV := maxV - minV
	if rangeV == 0 {
		rangeV = 1
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
		y := h - (float64(v-minV)/float64(rangeV))*h
		parts[i] = fmt.Sprintf("%.1f,%.1f", x, y)
	}
	zeroY = h - (float64(0-minV)/float64(rangeV))*h
	return strings.Join(parts, " "), zeroY
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

func queryTrendPPL(q string, days, page, pmlID int) ([]PPLTrendRow, models.PageInfo) {
	like := "%" + q + "%"
	extra := fmt.Sprintf("&days=%d", days)
	if q != "" {
		extra += "&q=" + q
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
	db.DB.QueryRow(`
		SELECT COUNT(DISTINCT u.id) FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		WHERE u.role = 'ppl'`+pmlFilter+` AND (u.name LIKE ? OR pml.name LIKE ?)`, countArgs...).Scan(&total)
	pageInfo := models.NewPageInfo(page, total, "/admin/table/trend/ppl", "admin-trend-ppl-wrap", extra)

	rows, err := db.DB.Query(`
		SELECT u.id, u.name, pml.name,
		       COALESCE(SUM(p.fasih_submitted),0), COALESCE(SUM(p.jumlah_draft),0),
		       COALESCE(SUM(p.fasih_approved_pengawas),0), COALESCE(SUM(p.fasih_rejected_pengawas),0)
		FROM users u
		JOIN sls s ON s.ppl_id = u.id
		JOIN users pml ON pml.id = s.pml_id
		LEFT JOIN progress p ON p.sls_id = s.id
		WHERE u.role = 'ppl'`+pmlFilter+` AND (u.name LIKE ? OR pml.name LIKE ?)
		GROUP BY u.id, u.name, pml.name
		ORDER BY pml.name, u.name
		LIMIT ? OFFSET ?`, queryArgs...)
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
		list[i].ChartW, list[i].ChartH = trendChartW, trendChartH
		list[i].HasHistory = len(pts) >= 2
		last := pts[len(pts)-1]
		list[i].LastDate = last.Tanggal
		if len(pts) >= 2 {
			prev := pts[len(pts)-2]
			list[i].DeltaSubmit = last.Submit - prev.Submit
			list[i].DeltaApproved = last.Approved - prev.Approved
			list[i].DeltaRejected = last.Rejected - prev.Rejected
		}
		if len(pts) >= 3 {
			prev := pts[len(pts)-2]
			prev2 := pts[len(pts)-3]
			list[i].DeltaTotal = last.Submit - prev.Submit
			list[i].PrevDeltaTotal = prev.Submit - prev2.Submit
			list[i].Acceleration = list[i].DeltaTotal - list[i].PrevDeltaTotal
			list[i].HasAccelData = true

			var velocityS []int
			for j := 1; j < len(pts); j++ {
				velocityS = append(velocityS, pts[j].Submit-pts[j-1].Submit)
			}
			list[i].VelocityPts, list[i].VelocityZeroY = sparklinePointsSigned(velocityS, trendChartW, trendChartH)
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
		list[i].ChartW, list[i].ChartH = trendChartW, trendChartH
		list[i].HasHistory = len(pts) >= 2
		last := pts[len(pts)-1]
		list[i].LastDate = last.Tanggal
		if len(pts) >= 2 {
			prev := pts[len(pts)-2]
			list[i].DeltaApproved = last.Approved - prev.Approved
			list[i].DeltaRejected = last.Rejected - prev.Rejected
			list[i].DeltaRevoked = last.Revoked - prev.Revoked
		}
		if len(pts) >= 3 {
			prev := pts[len(pts)-2]
			prev2 := pts[len(pts)-3]
			totalLast := last.Approved + last.Rejected + last.Revoked
			totalPrev := prev.Approved + prev.Rejected + prev.Revoked
			totalPrev2 := prev2.Approved + prev2.Rejected + prev2.Revoked
			list[i].DeltaTotal = totalLast - totalPrev
			list[i].PrevDeltaTotal = totalPrev - totalPrev2
			list[i].Acceleration = list[i].DeltaTotal - list[i].PrevDeltaTotal
			list[i].HasAccelData = true

			var velocityS []int
			for j := 1; j < len(pts); j++ {
				tCur := pts[j].Approved + pts[j].Rejected + pts[j].Revoked
				tPrev := pts[j-1].Approved + pts[j-1].Rejected + pts[j-1].Revoked
				velocityS = append(velocityS, tCur-tPrev)
			}
			list[i].VelocityPts, list[i].VelocityZeroY = sparklinePointsSigned(velocityS, trendChartW, trendChartH)
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
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	list, pageInfo := queryTrendPPL(q, days, page, pmlID)
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
