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

// ── Pagination ──────────────────────────────────────────────────────────────

const PerPage = 20

type PageInfo struct {
	Current   int
	TotalPage int
	TotalRow  int
	From      int
	To        int
	BaseURL   string
	TargetID  string
	Extra     string
	Pages     []int
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
