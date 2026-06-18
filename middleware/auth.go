package middleware

import (
	"encoding/gob"
	"net/http"

	"github.com/gorilla/sessions"
	"github.com/labstack/echo/v4"
)

func init() {
	gob.Register(int(0))
	gob.Register("")
}

var Store = sessions.NewCookieStore([]byte("se2026-secret-key-ganti-di-produksi"))

func GetSession(c echo.Context) *sessions.Session {
	sess, _ := Store.Get(c.Request(), "se2026-session")
	return sess
}

func RequireAuth(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		sess := GetSession(c)
		if sess.Values["user_id"] == nil {
			return c.Redirect(http.StatusFound, "/login")
		}
		return next(c)
	}
}

func RequireRole(role string) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			sess := GetSession(c)
			if sess.Values["role"] != role {
				return c.Redirect(http.StatusFound, "/login")
			}
			return next(c)
		}
	}
}

func SetUser(c echo.Context, userID int, role, name string) error {
	sess := GetSession(c)
	sess.Values["user_id"] = userID
	sess.Values["role"] = role
	sess.Values["name"] = name
	return sess.Save(c.Request(), c.Response())
}

func ClearSession(c echo.Context) error {
	sess := GetSession(c)
	sess.Options.MaxAge = -1
	return sess.Save(c.Request(), c.Response())
}

func SessionUserID(c echo.Context) int {
	sess := GetSession(c)
	v, _ := sess.Values["user_id"].(int)
	return v
}

func SessionRole(c echo.Context) string {
	sess := GetSession(c)
	v, _ := sess.Values["role"].(string)
	return v
}

func SessionName(c echo.Context) string {
	sess := GetSession(c)
	v, _ := sess.Values["name"].(string)
	return v
}

func RedirectByRole(c echo.Context) error {
	switch SessionRole(c) {
	case "pml":
		return c.Redirect(http.StatusFound, "/pml")
	case "admin":
		return c.Redirect(http.StatusFound, "/admin")
	case "organik":
		return c.Redirect(http.StatusFound, "/organik")
	default:
		return c.Redirect(http.StatusFound, "/ppl")
	}
}

func IsHTMX(c echo.Context) bool {
	return c.Request().Header.Get("HX-Request") == "true"
}

func AddToast(w http.ResponseWriter, msg, kind string) {
	// kind: success | error | warning
	w.Header().Set("HX-Trigger", `{"showToast":{"msg":"`+msg+`","kind":"`+kind+`"}}`)
}
