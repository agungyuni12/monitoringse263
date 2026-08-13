package models

type User struct {
	ID           int
	Username     string
	PasswordHash string
	Role         string
	Name         string
}

type SLS struct {
	ID       int
	KodeSLS  string
	NamaSLS  string
	PMLID    int
	PPLID    int
	Target   int
	KodeKec  string
	NamaKec  string
	KodeDesa string
	NamaDesa string
	NamaPPL  string
	NamaPML  string
}

// SLSProgress adalah gabungan data SLS + agregat harian dari PPL dan PML
type SLSProgress struct {
	SLS
	// dari laporan_harian (PPL) — SUM
	JumlahSubmit    int
	JumlahDraft     int
	JmlLaporan      int
	TanggalTerakhir string
	// kendala terbaru dari laporan_harian
	Kendala         string
	SolusiSementara string
	// dari verifikasi_harian (PML) — SUM
	JumlahDiperiksa int
	JumlahError     int
	JumlahRevoked   int
	JumlahObservasi int
	// status dari verifikasi terakhir
	StatusKendala   string
	TindakLanjutPML string
	// nama join
	NamaPPL string
}

type LaporanHarian struct {
	ID            int
	SLSID         int
	Tanggal       string
	JumlahSubmit  int
	JumlahDraft   int
	AlasanLebih20 string
}

type VerifikasiHarian struct {
	ID              int
	SLSID           int
	Tanggal         string
	JumlahDiperiksa int
	JumlahError     int
	JumlahObservasi int
	StatusKendala   string
	TindakLanjutPML string
	Kendala         string
	SolusiSementara string
}

type StatusLabel struct {
	Value string
	Label string
}

var StatusOptions = []StatusLabel{
	{"open", "Terbuka"},
	{"in_progress", "Sedang Ditangani"},
	{"resolved", "Selesai"},
	{"escalated", "Eskalasi"},
}

func StatusLabelOf(val string) string {
	for _, s := range StatusOptions {
		if s.Value == val {
			return s.Label
		}
	}
	return val
}

// EvaluasiAssignment — catatan perbaikan kuesioner per assignment (fitur
// Evaluasi Organik). Satu assignment bisa punya banyak baris (satu per
// rincian kuesioner yang salah).
type EvaluasiAssignment struct {
	ID                  int
	OrganikID           int
	NamaOrganik         string
	SLSID               int
	NamaSLS             string
	NamaKec             string
	NamaDesa            string
	NamaPPL             string
	NamaPML             string
	AssignmentID        string
	NamaUsaha           string
	FasihLink           string
	Tipe                string
	Nama                string
	RincianKuesioner    string
	JenisKesalahan      string
	Rekomendasi         string
	Status              string
	CatatanTindakLanjut string
	TindakLanjutByName  string
	TindakLanjutAt      string
	CreatedAt           string
}

var JenisKesalahanOptions = []StatusLabel{
	{"konflik_isian", "Konflik Isian"},
	{"nilai_ekstrem", "Nilai Ekstrem"},
	{"tidak_konsisten", "Tidak Konsisten"},
	{"lainnya", "Lainnya"},
}

func JenisKesalahanLabelOf(val string) string {
	for _, s := range JenisKesalahanOptions {
		if s.Value == val {
			return s.Label
		}
	}
	return val
}

// EvaluasiTipeOptions — tipe sumber assignment yg bisa dievaluasi organik.
// 'usaha' = usaha_ekonomi (form lengkap); 'td_*' = tidak ditemukan (catatan
// bebas saja). Value-nya harus sinkron dgn konstanta di handlers/organik_evaluasi.go.
var EvaluasiTipeOptions = []StatusLabel{
	{"usaha", "Usaha (Data Ekonomi)"},
	{"td_usaha", "Tidak Ditemukan · Usaha"},
	{"td_keluarga", "Tidak Ditemukan · Keluarga"},
	{"td_usaha_keluarga", "Tidak Ditemukan · Usaha dalam Keluarga"},
}

func EvaluasiTipeLabelOf(val string) string {
	for _, s := range EvaluasiTipeOptions {
		if s.Value == val {
			return s.Label
		}
	}
	return val
}

var PerbaikanStatusOptions = []StatusLabel{
	{"open", "Terbuka"},
	{"resolved", "Selesai"},
}

func PerbaikanStatusLabelOf(val string) string {
	for _, s := range PerbaikanStatusOptions {
		if s.Value == val {
			return s.Label
		}
	}
	return val
}

// ── Pagination ──────────────────────────────────────────────────────────────

const PerPage = 20

type PageInfo struct {
	Current     int
	TotalPage   int
	TotalRow    int
	From        int
	To          int
	BaseURL     string
	TargetID    string
	Extra       string
	Pages       []int
	Sort        string // kolom yang lagi aktif di-sort, "" = default
	Dir         string // "asc" atau "desc"
	FilterExtra string // query string filter TANPA sort/dir, dipakai header kolom sortable
}

func NewPageInfo(current, totalRow int, baseURL, targetID, extra string) PageInfo {
	totalPage := (totalRow + PerPage - 1) / PerPage
	if totalPage == 0 {
		totalPage = 1
	}
	if current < 1 {
		current = 1
	}
	if current > totalPage {
		current = totalPage
	}
	from := (current-1)*PerPage + 1
	to := current * PerPage
	if to > totalRow {
		to = totalRow
	}
	if totalRow == 0 {
		from, to = 0, 0
	}
	return PageInfo{
		Current:   current,
		TotalPage: totalPage,
		TotalRow:  totalRow,
		From:      from,
		To:        to,
		BaseURL:   baseURL,
		TargetID:  targetID,
		Extra:     extra,
		Pages:     pageNumbers(current, totalPage),
	}
}

func pageNumbers(current, total int) []int {
	if total <= 7 {
		nums := make([]int, total)
		for i := range nums {
			nums[i] = i + 1
		}
		return nums
	}
	var nums []int
	nums = append(nums, 1)
	if current > 4 {
		nums = append(nums, -1)
	}
	start := current - 2
	if start < 2 {
		start = 2
	}
	end := current + 2
	if end > total-1 {
		end = total - 1
	}
	for i := start; i <= end; i++ {
		nums = append(nums, i)
	}
	if current < total-3 {
		nums = append(nums, -1)
	}
	if total > 1 {
		nums = append(nums, total)
	}
	return nums
}

// ── Sortable table headers ───────────────────────────────────────────────────

// BuildOrderBy memvalidasi sortKey terhadap whitelist `allowed` (kunci UI ->
// ekspresi kolom SQL) supaya query param sort/dir dari user tidak pernah
// diinterpolasi langsung ke SQL. Kalau sortKey tidak dikenal, fallback ke
// defaultOrderBy dan col dikembalikan "" (artinya tidak ada header yang aktif).
func BuildOrderBy(sortKey, dirKey string, allowed map[string]string, defaultOrderBy string) (orderBy, col, dir string) {
	dir = "asc"
	if dirKey == "desc" {
		dir = "desc"
	}
	expr, ok := allowed[sortKey]
	if !ok {
		return "ORDER BY " + defaultOrderBy, "", "asc"
	}
	sqlDir := "ASC"
	if dir == "desc" {
		sqlDir = "DESC"
	}
	return "ORDER BY " + expr + " " + sqlDir, sortKey, dir
}

// SortQueryString membuat fragmen "&sort=..&dir=.." untuk disambung ke query
// string pagination. Kosong kalau tidak ada sort aktif.
func SortQueryString(col, dir string) string {
	if col == "" {
		return ""
	}
	return "&sort=" + col + "&dir=" + dir
}
