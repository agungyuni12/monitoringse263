package handlers

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"strings"
	"time"

	"monitoringse/db"

	"github.com/labstack/echo/v4"
	"golang.org/x/net/html"
)

const (
	fasihBase       = "https://fasih-sm.bps.go.id"
	fasihSurveyID   = "a0429e96-51a5-477b-a415-485f9c153004"
	fasihPeriodID   = "fd68e454-ba45-4b85-8205-f3bf777ded24"
	fasihRoleID     = "6d7d919a-45e5-4779-bb87-2905b49fd31a" // Pencacah
	fasihRegion2ID  = "546a26bf-e388-41ab-9083-e02cbbc093d4" // Dompu
	fasihPageSize   = 10
)

var (
	fasihUser = getenv("FASIH_USER", "agung.yuniarta")
	fasihPass = getenv("FASIH_PASS", "kelayu1998")
)

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

var wita = func() *time.Location {
	loc, err := time.LoadLocation("Asia/Makassar")
	if err != nil {
		return time.FixedZone("WITA", 8*3600)
	}
	return loc
}()

// LastSyncFromDB membaca waktu dan jumlah SLS sync terakhir dari database.
func LastSyncFromDB() struct {
	Time    string
	Updated int
	Error   string
} {
	var result struct {
		Time    string
		Updated int
		Error   string
	}
	var syncedAt *time.Time
	var count int
	err := db.DB.QueryRow(`
		SELECT MAX(fasih_synced_at), COUNT(*)
		FROM progress
		WHERE fasih_synced_at = (SELECT MAX(fasih_synced_at) FROM progress)
	`).Scan(&syncedAt, &count)
	if err != nil || syncedAt == nil {
		return result
	}
	wita := time.FixedZone("WITA", 8*3600)
	result.Time = syncedAt.In(wita).Format("02/01/2006 15:04:05 WITA")
	result.Updated = count
	return result
}

// --- HTTP client & login ---

func newFasihClient() (*http.Client, error) {
	jar, _ := cookiejar.New(nil)
	client := &http.Client{
		Jar: jar,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) > 15 {
				return fmt.Errorf("terlalu banyak redirect")
			}
			// propagate headers pada redirect
			if len(via) > 0 {
				req.Header.Set("User-Agent", via[0].Header.Get("User-Agent"))
			}
			return nil
		},
	}
	return client, nil
}

var browserUA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

func fasihLogin(client *http.Client) (string, error) {
	// GET oauth redirect → Keycloak login form
	req, _ := http.NewRequest("GET", fasihBase+"/oauth2/authorization/ics", nil)
	req.Header.Set("User-Agent", browserUA)
	req.Header.Set("Accept", "text/html,application/xhtml+xml,*/*;q=0.8")
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("oauth redirect: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	// Parse form action dan hidden fields
	action, fields, err := parseLoginForm(string(body))
	if err != nil {
		return "", fmt.Errorf("parse form: %w", err)
	}
	fields["username"] = fasihUser
	fields["password"] = fasihPass

	formData := url.Values{}
	for k, v := range fields {
		formData.Set(k, v)
	}

	// POST credentials
	postReq, _ := http.NewRequest("POST", action, strings.NewReader(formData.Encode()))
	postReq.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	postReq.Header.Set("User-Agent", browserUA)
	postReq.Header.Set("Origin", "https://sso.bps.go.id")
	postReq.Header.Set("Referer", resp.Request.URL.String())

	postResp, err := client.Do(postReq)
	if err != nil {
		return "", fmt.Errorf("login post: %w", err)
	}
	postResp.Body.Close()

	// Ambil XSRF-TOKEN dari cookie jar
	parsedBase, _ := url.Parse(fasihBase)
	for _, c := range client.Jar.Cookies(parsedBase) {
		if c.Name == "XSRF-TOKEN" {
			return c.Value, nil
		}
	}
	return "", fmt.Errorf("XSRF-TOKEN tidak ditemukan setelah login")
}

func parseLoginForm(htmlStr string) (action string, fields map[string]string, err error) {
	doc, err := html.Parse(strings.NewReader(htmlStr))
	if err != nil {
		return "", nil, err
	}
	fields = make(map[string]string)

	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.ElementNode && n.Data == "form" {
			for _, a := range n.Attr {
				if a.Key == "id" && a.Val == "kc-form-login" {
					for _, fa := range n.Attr {
						if fa.Key == "action" {
							action = fa.Val
						}
					}
				}
			}
		}
		if n.Type == html.ElementNode && n.Data == "input" {
			var name, value string
			for _, a := range n.Attr {
				if a.Key == "name" {
					name = a.Val
				}
				if a.Key == "value" {
					value = a.Val
				}
			}
			if name != "" && name != "username" && name != "password" {
				fields[name] = value
			}
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(doc)

	if action == "" {
		return "", nil, fmt.Errorf("form kc-form-login tidak ditemukan")
	}
	return action, fields, nil
}

// --- FASIH API ---

type fasihPageResp struct {
	Success bool `json:"success"`
	Data    struct {
		Content       []fasihPencacah `json:"content"`
		TotalElements *int            `json:"totalElements"`
	} `json:"data"`
}

type fasihPencacah struct {
	RegionSummary []struct {
		RegionCode      string `json:"regionCode"`
		StatusBreakdown []struct {
			Status string `json:"status"`
			Count  int    `json:"count"`
		} `json:"statusBreakdown"`
	} `json:"regionSummary"`
}

func fetchFasihPage(client *http.Client, xsrf string, page int) (*fasihPageResp, error) {
	payload := map[string]interface{}{
		"surveyPeriodId": fasihPeriodID,
		"surveyRoleId":   fasihRoleID,
		"size":           fasihPageSize,
		"page":           page,
		"search":         "",
		"target":         "TARGET_ONLY",
		"region": map[string]interface{}{
			"region1Id": nil, "region2Id": fasihRegion2ID,
			"region3Id": nil, "region4Id": nil, "region5Id": nil,
			"region6Id": nil, "region7Id": nil, "region8Id": nil,
			"region9Id": nil, "region10Id": nil,
		},
		"regionSummaryLevel": 6,
	}
	body, _ := json.Marshal(payload)

	req, _ := http.NewRequest("POST",
		fasihBase+"/analytic/api/v2/assignment/report-progress-by-responsibility",
		bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, */*")
	req.Header.Set("User-Agent", browserUA)
	req.Header.Set("X-XSRF-TOKEN", xsrf)
	req.Header.Set("Referer", fmt.Sprintf("%s/app/surveys/%s/%s", fasihBase, fasihSurveyID, fasihPeriodID))
	req.Header.Set("Origin", fasihBase)

	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		b, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(b)[:min(200, len(b))])
	}

	var result fasihPageResp
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return &result, nil
}

// --- Aggregate & Upload ---

type slsAgg struct {
	submit    int
	draft     int
	open      int
	submitted int
	// Breakdown per level
	approvedPengawas  int
	rejectedPengawas  int
	revokedPengawas   int
	approvedKabupaten int
	rejectedKabupaten int
	approvedProvinsi  int
	rejectedProvinsi  int
	approvedPusat     int
	rejectedPusat     int
	editedAdmin       int // "EDITED BY Admin ..." — digabung semua level (Kab/Prov/Pusat)
	completedAdmin    int // "COMPLETED BY Admin ..." — digabung semua level
	total             int
}

var submitStatuses = map[string]bool{
	"SUBMITTED BY Pencacah":        true,
	"SUBMITTED RESPONDENT":         true,
	"APPROVED BY Pengawas":         true,
	"REJECTED BY Pengawas":         true,
	"REVOKED BY Pengawas":          true,
	"EDITED BY Admin Kabupaten":    true,
	"COMPLETED BY Admin Kabupaten": true,
	"APPROVED BY Admin Kabupaten":  true,
	"REJECTED BY Admin Kabupaten":  true,
	"EDITED BY Admin Provinsi":     true,
	"COMPLETED BY Admin Provinsi":  true,
	"APPROVED BY Admin Provinsi":   true,
	"REJECTED BY Admin Provinsi":   true,
	"EDITED BY Admin Pusat":        true,
	"COMPLETED BY Admin Pusat":     true,
	"APPROVED BY Admin Pusat":      true,
	"REJECTED BY Admin Pusat":      true,
}

func doFasihSync() (int, error) {
	client, _ := newFasihClient()

	xsrf, err := fasihLogin(client)
	if err != nil {
		return 0, fmt.Errorf("login: %w", err)
	}

	// Ambil halaman pertama untuk tahu total
	first, err := fetchFasihPage(client, xsrf, 0)
	if err != nil {
		return 0, fmt.Errorf("halaman 0: %w", err)
	}

	totalPencacah := 0
	if first.Data.TotalElements != nil {
		totalPencacah = *first.Data.TotalElements
	}
	if totalPencacah == 0 {
		totalPencacah = len(first.Data.Content)
	}
	if totalPencacah == 0 {
		return 0, fmt.Errorf("tidak ada data pencacah dari FASIH")
	}

	totalPages := (totalPencacah + fasihPageSize - 1) / fasihPageSize
	log.Printf("[FASIH] %d pencacah, %d halaman", totalPencacah, totalPages)

	// Aggregate semua halaman
	agg := make(map[string]*slsAgg)
	unknownStatuses := make(map[string]int)
	processPencacah(first.Data.Content, agg, unknownStatuses)

	for pg := 1; pg < totalPages; pg++ {
		time.Sleep(300 * time.Millisecond)
		page, err := fetchFasihPage(client, xsrf, pg)
		if err != nil {
			log.Printf("[FASIH] halaman %d error: %v (lanjut)", pg, err)
			continue
		}
		processPencacah(page.Data.Content, agg, unknownStatuses)
	}

	log.Printf("[FASIH] %d SLS unik ditemukan", len(agg))
	if len(unknownStatuses) > 0 {
		// Status yang belum masuk submitStatuses/switch-case di atas — kalau FASIH
		// nambah status baru (kejadian: "EDITED BY Admin Kabupaten" & "COMPLETED BY
		// Admin Kabupaten" sempat tidak dihitung selama beberapa waktu), ini bakal
		// langsung ketahuan dari log tanpa perlu investigasi manual lagi.
		log.Printf("[FASIH] PERINGATAN: %d status belum dikenali (tidak masuk hitungan submit/approved): %v", len(unknownStatuses), unknownStatuses)
	}
	return upsertProgress(agg)
}

func processPencacah(content []fasihPencacah, agg map[string]*slsAgg, unknownStatuses map[string]int) {
	for _, p := range content {
		for _, rs := range p.RegionSummary {
			kode := rs.RegionCode
			if !strings.HasPrefix(kode, "5205") {
				continue
			}
			a := agg[kode]
			if a == nil {
				a = &slsAgg{}
				agg[kode] = a
			}
			for _, sb := range rs.StatusBreakdown {
				cnt := sb.Count
				a.total += cnt
				if submitStatuses[sb.Status] {
					a.submit += cnt
				}
				if sb.Status == "DRAFT" {
					a.draft += cnt
				}
				knownBucket := true
				switch sb.Status {
				case "OPEN":
					a.open += cnt
				case "DRAFT":
					// sudah dihitung di a.draft di atas
				case "SUBMITTED BY Pencacah", "SUBMITTED RESPONDENT":
					a.submitted += cnt
				case "APPROVED BY Pengawas":
					a.approvedPengawas += cnt
				case "REJECTED BY Pengawas":
					a.rejectedPengawas += cnt
				case "REVOKED BY Pengawas":
					a.revokedPengawas += cnt
				case "APPROVED BY Admin Kabupaten":
					a.approvedKabupaten += cnt
				case "REJECTED BY Admin Kabupaten":
					a.rejectedKabupaten += cnt
				case "APPROVED BY Admin Provinsi":
					a.approvedProvinsi += cnt
				case "REJECTED BY Admin Provinsi":
					a.rejectedProvinsi += cnt
				case "APPROVED BY Admin Pusat":
					a.approvedPusat += cnt
				case "REJECTED BY Admin Pusat":
					a.rejectedPusat += cnt
				case "EDITED BY Admin Kabupaten", "EDITED BY Admin Provinsi", "EDITED BY Admin Pusat":
					a.editedAdmin += cnt
				case "COMPLETED BY Admin Kabupaten", "COMPLETED BY Admin Provinsi", "COMPLETED BY Admin Pusat":
					a.completedAdmin += cnt
				default:
					knownBucket = false
				}
				// Status yang tidak masuk switch-case DI ATAS *maupun* tidak ada di
				// submitStatuses sama sekali berarti benar-benar belum dikenali sistem
				// ini (bukan cuma "EDITED"/"COMPLETED" yang sudah masuk submitStatuses
				// tapi memang tidak perlu bucket approved/rejected tersendiri).
				if !knownBucket && !submitStatuses[sb.Status] {
					unknownStatuses[sb.Status] += cnt
				}
			}
		}
	}
}

func upsertProgress(agg map[string]*slsAgg) (int, error) {
	// Build lookup: kode_sls → sls.id
	rows, err := db.DB.Query("SELECT id, kode_sls FROM sls")
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	slsMap := make(map[string]int)
	for rows.Next() {
		var id int
		var kode string
		rows.Scan(&id, &kode)
		slsMap[kode] = id
	}

	const sqlUpsert = `
		INSERT INTO progress
		  (sls_id, jumlah_submit, jumlah_draft,
		   fasih_open, fasih_submitted,
		   fasih_approved_pengawas, fasih_rejected_pengawas, fasih_revoked_pengawas,
		   fasih_approved_kabupaten, fasih_rejected_kabupaten,
		   fasih_approved_provinsi, fasih_rejected_provinsi,
		   fasih_approved_pusat, fasih_rejected_pusat,
		   fasih_edited_admin, fasih_completed_admin,
		   fasih_total, fasih_synced_at, updated_at)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NOW(),NOW())
		ON DUPLICATE KEY UPDATE
		  jumlah_submit             = VALUES(jumlah_submit),
		  jumlah_draft              = VALUES(jumlah_draft),
		  fasih_open                = VALUES(fasih_open),
		  fasih_submitted           = VALUES(fasih_submitted),
		  fasih_approved_pengawas   = VALUES(fasih_approved_pengawas),
		  fasih_rejected_pengawas   = VALUES(fasih_rejected_pengawas),
		  fasih_revoked_pengawas    = VALUES(fasih_revoked_pengawas),
		  fasih_approved_kabupaten  = VALUES(fasih_approved_kabupaten),
		  fasih_rejected_kabupaten  = VALUES(fasih_rejected_kabupaten),
		  fasih_approved_provinsi   = VALUES(fasih_approved_provinsi),
		  fasih_rejected_provinsi   = VALUES(fasih_rejected_provinsi),
		  fasih_approved_pusat      = VALUES(fasih_approved_pusat),
		  fasih_rejected_pusat      = VALUES(fasih_rejected_pusat),
		  fasih_edited_admin        = VALUES(fasih_edited_admin),
		  fasih_completed_admin     = VALUES(fasih_completed_admin),
		  fasih_total               = VALUES(fasih_total),
		  fasih_synced_at           = NOW(),
		  updated_at                = NOW()`

	syncedAt := time.Now()
	_ = syncedAt

	n := 0
	for kode, a := range agg {
		slsID, ok := slsMap[kode]
		if !ok {
			continue
		}
		_, err := db.DB.Exec(sqlUpsert,
			slsID, a.submit, a.draft,
			a.open, a.submitted,
			a.approvedPengawas, a.rejectedPengawas, a.revokedPengawas,
			a.approvedKabupaten, a.rejectedKabupaten,
			a.approvedProvinsi, a.rejectedProvinsi,
			a.approvedPusat, a.rejectedPusat,
			a.editedAdmin, a.completedAdmin,
			a.total)
		if err != nil {
			log.Printf("[FASIH] upsert %s: %v", kode, err)
			continue
		}
		// Sinkronkan target lokal dengan total assignment FASIH
		if a.total > 0 {
			db.DB.Exec(`UPDATE sls SET target = ? WHERE id = ?`, a.total, slsID)
		}
		n++
	}
	applyNonSLSApprovedOverride()
	return n, nil
}

// applyNonSLSApprovedOverride: SLS "Non SLS" (area kosong seperti gunung/sawah/
// kebun/ladang tanpa usaha/keluarga nyata) selalu dianggap punya minimal 1
// assignment approved oleh pengawas, terlepas dari status approval asli di
// FASIH — supaya tidak nyangkut "belum diperiksa" di rekap progres. Dijalankan
// tiap kali sync FASIH selesai, jadi override ini tidak hilang walau data
// di-sync ulang.
//
// Identifikasi Non SLS BUKAN dari nama_sls (variasinya banyak: "NON SLS...",
// "KEBUN...", "SAWAH...", "LADANG...", "GUNUNG...", "HUTAN...", dst — tidak
// konsisten), tapi dari KODE SLS: kode_sls 16 digit = prov(2)+kab(2)+kec(3)+
// desa(3)+sls(4)+subsls(2). SLS residensial normal (RT/dusun) diberi nomor
// segmen sls < 1000, sedangkan Non SLS (wilayah kerja statistik non-permukiman)
// selalu diberi nomor segmen sls >= 1000 (mis. "1001", "2003", dst) — ini
// konvensi baku BPS, jadi jauh lebih andal dipakai sebagai penanda.
func applyNonSLSApprovedOverride() {
	// jumlah_submit ikut dipaksa minimal sama dengan fasih_approved_pengawas
	// (tidak masuk akal ada assignment approved tapi belum submit) — soalnya
	// % Progres & % Terverifikasi dihitung dari jumlah_submit, bukan dari
	// fasih_approved_pengawas.
	_, err := db.DB.Exec(`
		UPDATE progress p
		JOIN sls s ON s.id = p.sls_id
		SET p.fasih_approved_pengawas = LEAST(GREATEST(p.fasih_approved_pengawas, 1), p.fasih_total),
		    p.jumlah_submit           = LEAST(GREATEST(p.jumlah_submit, LEAST(GREATEST(p.fasih_approved_pengawas, 1), p.fasih_total)), p.fasih_total)
		WHERE CAST(SUBSTRING(s.kode_sls, 11, 4) AS UNSIGNED) >= 1000
		  AND p.fasih_total > 0`)
	if err != nil {
		log.Printf("[FASIH] applyNonSLSApprovedOverride: %v", err)
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// AdminSyncFasih — trigger sync manual dari admin panel (POST /admin/sync/fasih).
func AdminSyncFasih(c echo.Context) error {
	go func() {
		n, err := doFasihSync()
		if err != nil {
			log.Printf("[FASIH] Sync manual gagal: %v", err)
			return
		}
		log.Printf("[FASIH] Sync manual selesai: %d SLS diupdate", n)
	}()
	return c.JSON(http.StatusOK, map[string]string{
		"status": "Sync FASIH dimulai di background. Refresh halaman dalam beberapa menit.",
	})
}
