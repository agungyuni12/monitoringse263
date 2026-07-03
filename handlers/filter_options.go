package handlers

import "monitoringse/db"

// FilterOption is a generic {id, label} pair used for cascading dropdown filters.
type FilterOption struct {
	ID   int
	Name string
}

// OOBSelect describes a <select> re-rendered out-of-band (via hx-swap-oob) whenever
// a parent filter (kecamatan/PML/PPL) changes, so its options stay narrowed to what's
// actually valid under the current filter combination.
type OOBSelect struct {
	TargetID    string
	Name        string
	Placeholder string
	Options     []FilterOption
	Selected    int
	HxGet       string
	HxTarget    string
	HxInclude   string
}

func placeholders(n int) string {
	s := "?"
	for i := 1; i < n; i++ {
		s += ",?"
	}
	return s
}

// queryPMLOptionsByKec returns PML users, narrowed to those with at least one SLS
// in one of the given kecamatan (all PML if kecs is empty).
func queryPMLOptionsByKec(kecs []string) []FilterOption {
	q := `SELECT DISTINCT u.id, u.name FROM users u JOIN sls s ON s.pml_id = u.id WHERE u.role = 'pml'`
	var args []interface{}
	if len(kecs) > 0 {
		q += ` AND s.nama_kec IN (` + placeholders(len(kecs)) + `)`
		for _, k := range kecs {
			args = append(args, k)
		}
	}
	q += ` ORDER BY u.name`
	return queryFilterOptions(q, args...)
}

// queryPPLOptionsByFilter returns PPL users, narrowed by kecamatan and/or PML.
func queryPPLOptionsByFilter(kecs []string, pmlID int) []FilterOption {
	q := `SELECT DISTINCT u.id, u.name FROM users u JOIN sls s ON s.ppl_id = u.id WHERE u.role = 'ppl'`
	var args []interface{}
	if len(kecs) > 0 {
		q += ` AND s.nama_kec IN (` + placeholders(len(kecs)) + `)`
		for _, k := range kecs {
			args = append(args, k)
		}
	}
	if pmlID > 0 {
		q += ` AND s.pml_id = ?`
		args = append(args, pmlID)
	}
	q += ` ORDER BY u.name`
	return queryFilterOptions(q, args...)
}

// querySLSOptionsByFilter returns SLS, narrowed by kecamatan, PML, and/or PPL.
func querySLSOptionsByFilter(kecs []string, pmlID, pplID int) []FilterOption {
	q := `SELECT id, nama_sls FROM sls WHERE 1=1`
	var args []interface{}
	if len(kecs) > 0 {
		q += ` AND nama_kec IN (` + placeholders(len(kecs)) + `)`
		for _, k := range kecs {
			args = append(args, k)
		}
	}
	if pmlID > 0 {
		q += ` AND pml_id = ?`
		args = append(args, pmlID)
	}
	if pplID > 0 {
		q += ` AND ppl_id = ?`
		args = append(args, pplID)
	}
	q += ` ORDER BY nama_sls`
	return queryFilterOptions(q, args...)
}

func queryFilterOptions(query string, args ...interface{}) []FilterOption {
	rows, err := db.DB.Query(query, args...)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var list []FilterOption
	for rows.Next() {
		var o FilterOption
		rows.Scan(&o.ID, &o.Name)
		list = append(list, o)
	}
	return list
}
