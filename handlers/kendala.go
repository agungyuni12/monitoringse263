package handlers

import (
	"log"
	"net/http"
	"sort"
	"strconv"

	"monitoringse/db"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
)

type KendalaRow struct {
	Sumber      string
	NamaPetugas string
	NamaSLS     string
	NamaDesa    string
	NamaKec     string
	Tanggal     string
	Kendala     string
	Solusi      string
}

func KendalaList(c echo.Context) error {
	page, _ := strconv.Atoi(c.QueryParam("page"))
	if page < 1 {
		page = 1
	}
	q := c.QueryParam("q")
	like := "%" + q + "%"

	var list []KendalaRow

	// Organik kendala
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
	if err != nil {
		log.Printf("kendala organik query error: %v", err)
	} else {
		defer orgRows.Close()
		for orgRows.Next() {
			var r KendalaRow
			if scanErr := orgRows.Scan(&r.Sumber, &r.NamaPetugas, &r.NamaSLS, &r.NamaDesa,
				&r.NamaKec, &r.Tanggal, &r.Kendala, &r.Solusi); scanErr != nil {
				log.Printf("kendala organik scan error: %v", scanErr)
				continue
			}
			list = append(list, r)
		}
	}

	// PML kendala
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
	if err != nil {
		log.Printf("kendala pml query error: %v", err)
	} else {
		defer pmlRows.Close()
		for pmlRows.Next() {
			var r KendalaRow
			if scanErr := pmlRows.Scan(&r.Sumber, &r.NamaPetugas, &r.NamaSLS, &r.NamaDesa,
				&r.NamaKec, &r.Tanggal, &r.Kendala, &r.Solusi); scanErr != nil {
				log.Printf("kendala pml scan error: %v", scanErr)
				continue
			}
			list = append(list, r)
		}
	}

	// Merge sort: tanggal DESC, sumber ASC
	sort.SliceStable(list, func(i, j int) bool {
		if list[i].Tanggal != list[j].Tanggal {
			return list[i].Tanggal > list[j].Tanggal
		}
		return list[i].Sumber < list[j].Sumber
	})

	total := len(list)
	offset := (page - 1) * models.PerPage
	if offset > total {
		offset = total
	}
	end := offset + models.PerPage
	if end > total {
		end = total
	}

	extra := ""
	if q != "" {
		extra = "&q=" + q
	}

	data := map[string]interface{}{
		"Q":        q,
		"PageInfo": models.NewPageInfo(page, total, "/kendala", "kendala-wrap", extra),
		"Rows":     list[offset:end],
	}

	if c.Request().Header.Get("HX-Request") == "true" {
		return c.Render(http.StatusOK, "kendala_table", data)
	}
	return c.Render(http.StatusOK, "kendala.html", data)
}
