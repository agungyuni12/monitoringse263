package handlers

import (
	"database/sql"
	"net/http"

	"monitoringse/db"
	mw "monitoringse/middleware"
	"monitoringse/models"

	"github.com/labstack/echo/v4"
	"golang.org/x/crypto/bcrypt"
)

func LoginPage(c echo.Context) error {
	if mw.SessionUserID(c) != 0 {
		return mw.RedirectByRole(c)
	}
	return c.Render(http.StatusOK, "login.html", map[string]interface{}{
		"Error": "",
	})
}

func LoginPost(c echo.Context) error {
	username := c.FormValue("username")
	password := c.FormValue("password")

	var user models.User
	err := db.DB.QueryRow(
		"SELECT id, password_hash, role, name FROM users WHERE username = ? OR email = ?", username, username,
	).Scan(&user.ID, &user.PasswordHash, &user.Role, &user.Name)

	if err == sql.ErrNoRows || bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(password)) != nil {
		return c.Render(http.StatusOK, "login.html", map[string]interface{}{
			"Error": "Username atau password salah.",
		})
	}
	if err != nil {
		return c.Render(http.StatusOK, "login.html", map[string]interface{}{
			"Error": "Terjadi kesalahan. Coba lagi.",
		})
	}

	if err := mw.SetUser(c, user.ID, user.Role, user.Name); err != nil {
		return c.Render(http.StatusOK, "login.html", map[string]interface{}{
			"Error": "Gagal menyimpan sesi.",
		})
	}
	return mw.RedirectByRole(c)
}

func Logout(c echo.Context) error {
	mw.ClearSession(c)
	return c.Redirect(http.StatusFound, "/login")
}
