package main

import (
	"fmt"
	"html/template"
	"io"
	"log"
	"math"
	"net/http"
	"strings"

	"monitoringse/db"
	"monitoringse/handlers"
	mw "monitoringse/middleware"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
	echomw "github.com/labstack/echo/v4/middleware"
)

// TemplateRenderer wraps html/template for Echo
type TemplateRenderer struct {
	templates *template.Template
}

func (t *TemplateRenderer) Render(w io.Writer, name string, data interface{}, c echo.Context) error {
	return t.templates.ExecuteTemplate(w, name, data)
}

func main() {
	if err := db.Connect(); err != nil {
		log.Fatalf("Gagal konek database: %v", err)
	}
	log.Println("Database terhubung.")

	// Load all templates
	funcMap := template.FuncMap{
		"statusLabel": models.StatusLabelOf,
		"pct": func(a, b int) string {
			if b == 0 {
				return "0"
			}
			v := math.Min(float64(a)*100/float64(b), 100)
			s := fmt.Sprintf("%.2f", v)
			return strings.ReplaceAll(s, ".", ",")
		},
		"pctraw": func(a, b int) float64 {
			if b == 0 {
				return 0
			}
			return math.Min(float64(a)*100/float64(b), 100)
		},
		"pctf": func(v float64) string {
			s := fmt.Sprintf("%.2f", v)
			return strings.ReplaceAll(s, ".", ",")
		},
		"add": func(a, b int) int { return a + b },
		"inc": func(n int) int { return n + 1 },
		"dec": func(n int) int { return n - 1 },
	}

	tmpl := template.New("").Funcs(funcMap)
	tmpl = template.Must(tmpl.ParseGlob("templates/*.html"))
	tmpl = template.Must(tmpl.ParseGlob("templates/partials/*.html"))

	e := echo.New()
	e.HideBanner = true
	e.Renderer = &TemplateRenderer{templates: tmpl}

	e.Use(echomw.Logger())
	e.Use(echomw.Recover())
	e.Static("/static", "static")

	// Public routes
	e.GET("/login", handlers.LoginPage)
	e.POST("/login", handlers.LoginPost)
	e.GET("/logout", handlers.Logout)
	e.GET("/kendala", handlers.KendalaList)
	e.GET("/kendala/download", handlers.DownloadKendala)
	e.GET("/", func(c echo.Context) error {
		if mw.SessionUserID(c) != 0 {
			return mw.RedirectByRole(c)
		}
		return c.Redirect(http.StatusFound, "/login")
	})

	// PPL routes
	ppl := e.Group("/ppl", mw.RequireAuth, mw.RequireRole("ppl"))
	ppl.GET("", handlers.PPLDashboard)
	ppl.GET("/table", handlers.PPLTable)
	ppl.GET("/sls/:id/form", handlers.PPLFormModal)
	ppl.POST("/sls/:id/save", handlers.PPLSaveProgress)
	ppl.GET("/anomali", handlers.PPLAnomali)
	ppl.GET("/keberadaan", handlers.PPLKeberadaan)

	// PML routes
	pmlGrp := e.Group("/pml", mw.RequireAuth, mw.RequireRole("pml"))
	pmlGrp.GET("", handlers.PMLDashboard)
	pmlGrp.GET("/table", handlers.PMLTable)
	pmlGrp.GET("/sls/:id/verif", handlers.PMLVerifModal)
	pmlGrp.POST("/sls/:id/save", handlers.PMLSaveVerif)
	pmlGrp.GET("/anomali", handlers.PMLAnomali)
	pmlGrp.GET("/keberadaan", handlers.PMLKeberadaan)

	// Admin routes
	adminGrp := e.Group("/admin", mw.RequireAuth, mw.RequireRole("admin"))
	adminGrp.GET("", handlers.AdminDashboard)
	adminGrp.GET("/table/pml", handlers.AdminTablePML)
	adminGrp.GET("/table/ppl", handlers.AdminTablePPL)
	adminGrp.GET("/table/sls", handlers.AdminTableSLS)
	adminGrp.GET("/table/organik", handlers.AdminTableOrganik)
	adminGrp.GET("/table/anomali", handlers.AdminAnomaliTable)
	adminGrp.GET("/table/keberadaan", handlers.AdminKeberadaanTable)
	adminGrp.GET("/geo/stats", handlers.AdminGeoStats)
	adminGrp.GET("/geo/geojson", handlers.AdminGeoJSON)
	adminGrp.GET("/download/pml", handlers.DownloadPML)
	adminGrp.GET("/download/ppl", handlers.DownloadPPL)
	adminGrp.GET("/download/sls", handlers.DownloadSLS)
	adminGrp.GET("/download/organik", handlers.DownloadOrganik)
	adminGrp.POST("/sync/fasih", handlers.AdminSyncFasih)

	// Organik routes
	orgGrp := e.Group("/organik", mw.RequireAuth, mw.RequireRole("organik"))
	orgGrp.GET("", handlers.OrganikDashboard)
	orgGrp.GET("/sls/search", handlers.OrganikSearchSLS)
	orgGrp.POST("/laporan", handlers.OrganikSaveLaporan)

	port := "8080"
	fmt.Printf("SIGEMPAR SE2026 – Server berjalan di http://localhost:%s\n", port)
	fmt.Println("  Admin login : username=admin,  password=password123")
	fmt.Println("  PML login   : username=pml001, password=password123")
	fmt.Println("  PPL login   : username=ppl001, password=password123")
	log.Fatal(e.Start(":" + port))
}
