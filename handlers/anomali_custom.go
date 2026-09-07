package handlers

import (
	"encoding/json"
	"net/http"
	"sort"
	"strconv"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

// AnomaliCustomKV adalah satu pasang key:value hasil parse data_json — dipakai
// buat nampilin kolom-kolom mentah hasil query (beda-beda tiap rule, tergantung
// SELECT-nya) tanpa perlu tahu skema per rule di kode Go.
type AnomaliCustomKV struct {
	Key string
	Val string
}

type AnomaliCustomRow struct {
	ID              int
	RuleNo          int
	Jenis           string
	Deskripsi       string
	Nama            string
	NamaKec         string
	NamaDesa        string
	NamaSLS         string
	NamaPPL         string
	NamaPML         string
	KodeWilayah     string
	FasihLink       string
	FirstDetectedAt string
	SyncedAt        string
	Detail          []AnomaliCustomKV
}

var anomaliCustomSortCols = map[string]string{
	"lokasi":  "s.nama_kec, s.nama_desa, s.nama_sls",
	"petugas": "ppl.name",
	"nama":    "ac.nama",
	"rule":    "ac.rule_no",
	"jenis":   "r.jenis",
	"muncul":  "ac.first_detected_at",
	"sync":    "ac.synced_at",
}

func parseAnomaliCustomDetail(raw []byte) []AnomaliCustomKV {
	if len(raw) == 0 {
		return nil
	}
	var m map[string]interface{}
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]AnomaliCustomKV, 0, len(keys))
	for _, k := range keys {
		b, _ := json.Marshal(m[k])
		v := string(b)
		if s, ok := m[k].(string); ok {
			v = s
		}
		out = append(out, AnomaliCustomKV{Key: k, Val: v})
	}
	return out
}

func queryAnomaliCustom(page int, q, kec, jenis string, ruleNo, pmlID, pplID int, sort, dir, targetID, baseURL string) ([]AnomaliCustomRow, models.PageInfo) {
	like := "%" + q + "%"

	where := " WHERE (ac.nama LIKE ? OR r.deskripsi LIKE ? OR COALESCE(s.nama_sls,'') LIKE ?)"
	args := []interface{}{like, like, like}

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
	if jenis != "" {
		where += " AND r.jenis = ?"
		args = append(args, jenis)
	}
	if ruleNo > 0 {
		where += " AND ac.rule_no = ?"
		args = append(args, ruleNo)
	}

	fromJoin := ` FROM anomali_custom ac
		JOIN anomali_custom_rule r ON r.rule_no = ac.rule_no
		LEFT JOIN sls s ON s.id = ac.sls_id
		LEFT JOIN users ppl ON ppl.id = s.ppl_id
		LEFT JOIN users pml ON pml.id = s.pml_id`

	var total int
	countArgs := make([]interface{}, len(args))
	copy(countArgs, args)
	db.DB.QueryRow(`SELECT COUNT(*)`+fromJoin+where, countArgs...).Scan(&total)

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
	if jenis != "" {
		extra += "&jenis=" + jenis
	}
	if ruleNo > 0 {
		extra += "&rule_no=" + strconv.Itoa(ruleNo)
	}

	orderBy, sortCol, sortDir := models.BuildOrderBy(sort, dir, anomaliCustomSortCols, "ac.rule_no, s.nama_kec, s.nama_desa, s.nama_sls")

	offset := (page - 1) * models.PerPage
	queryArgs := make([]interface{}, len(args))
	copy(queryArgs, args)
	queryArgs = append(queryArgs, models.PerPage, offset)

	rows, err := db.DB.Query(`
		SELECT ac.id, ac.rule_no, r.jenis, r.deskripsi, ac.nama, ac.kode_wilayah, ac.assignment_id, ac.data_json,
		       COALESCE(s.nama_kec,''), COALESCE(s.nama_desa,''), COALESCE(s.nama_sls,''),
		       COALESCE(ppl.name,''), COALESCE(pml.name,''),
		       COALESCE(DATE_FORMAT(ac.first_detected_at,'%d/%m/%Y %H:%i'),''),
		       COALESCE(DATE_FORMAT(ac.synced_at,'%d/%m/%Y %H:%i'),'')`+
		fromJoin+where+`
		`+orderBy+`
		LIMIT ? OFFSET ?`, queryArgs...)

	pageInfo := models.NewPageInfo(page, total, baseURL, targetID, extra+models.SortQueryString(sortCol, sortDir))
	pageInfo.Sort = sortCol
	pageInfo.Dir = sortDir
	pageInfo.FilterExtra = extra
	if err != nil {
		return nil, pageInfo
	}
	defer rows.Close()

	var list []AnomaliCustomRow
	for rows.Next() {
		var r AnomaliCustomRow
		var assignmentID string
		var dataJSON []byte
		rows.Scan(&r.ID, &r.RuleNo, &r.Jenis, &r.Deskripsi, &r.Nama, &r.KodeWilayah, &assignmentID, &dataJSON,
			&r.NamaKec, &r.NamaDesa, &r.NamaSLS, &r.NamaPPL, &r.NamaPML, &r.FirstDetectedAt, &r.SyncedAt)
		r.FasihLink = fasihSMLink(assignmentID)
		r.Detail = parseAnomaliCustomDetail(dataJSON)
		list = append(list, r)
	}
	return list, pageInfo
}

// AdminAnomaliCustomTable — GET /admin/table/anomali-custom
func AdminAnomaliCustomTable(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	kec := c.QueryParam("kec")
	jenis := c.QueryParam("jenis")
	ruleNo, _ := strconv.Atoi(c.QueryParam("rule_no"))
	sortQ := c.QueryParam("sort")
	dir := c.QueryParam("dir")
	pmlID, _ := strconv.Atoi(c.QueryParam("pml_id"))
	pplID, _ := strconv.Atoi(c.QueryParam("ppl_id"))

	list, pageInfo := queryAnomaliCustom(page, q, kec, jenis, ruleNo, pmlID, pplID, sortQ, dir, "anomali-custom-result", "/admin/table/anomali-custom")

	var kecs []string
	if kec != "" {
		kecs = []string{kec}
	}
	pmlSelect := OOBSelect{
		TargetID: "anomali-custom-pml-select", Name: "pml_id", Placeholder: "Semua PML",
		Options: queryPMLOptionsByKec(kecs), Selected: pmlID,
		HxGet: "/admin/table/anomali-custom", HxTarget: "#anomali-custom-result", HxInclude: "#anomali-custom-filter-bar",
	}
	pplSelect := OOBSelect{
		TargetID: "anomali-custom-ppl-select", Name: "ppl_id", Placeholder: "Semua PPL",
		Options: queryPPLOptionsByFilter(kecs, pmlID), Selected: pplID,
		HxGet: "/admin/table/anomali-custom", HxTarget: "#anomali-custom-result", HxInclude: "#anomali-custom-filter-bar",
	}

	return c.Render(http.StatusOK, "anomali_custom_table.html", map[string]interface{}{
		"Rows":      list,
		"PageInfo":  pageInfo,
		"PMLSelect": pmlSelect,
		"PPLSelect": pplSelect,
		"Rules":     queryAnomaliCustomRuleOptions(),
		"RuleNo":    ruleNo,
		"Jenis":     jenis,
	})
}

type AnomaliCustomRuleOption struct {
	RuleNo    int
	Deskripsi string
}

func queryAnomaliCustomRuleOptions() []AnomaliCustomRuleOption {
	rows, err := db.DB.Query(`SELECT rule_no, deskripsi FROM anomali_custom_rule ORDER BY rule_no`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []AnomaliCustomRuleOption
	for rows.Next() {
		var o AnomaliCustomRuleOption
		rows.Scan(&o.RuleNo, &o.Deskripsi)
		list = append(list, o)
	}
	return list
}
