/**
 * auth.js — клієнтський auth guard
 *
 * Використання:
 *   <script src="/auth.js"></script>          — просто захист (будь-яка роль)
 *   <script>const REQUIRED_ROLE = "admin";</script>
 *   <script src="/auth.js"></script>          — тільки для admin
 *
 * Експортує:
 *   authToken()       → string | null
 *   authHeaders()     → { Authorization: "Bearer ..." }
 *   authUser()        → { username, role, display_name }
 *   authLogout()      → очищує токен і редиректить на /login.html
 */

(function () {
    const TOKEN_KEY = "token";
    const ROLE_KEY  = "role";
    const NAME_KEY  = "display_name";

    function token()   { return localStorage.getItem(TOKEN_KEY); }
    function role()    { return localStorage.getItem(ROLE_KEY) || ""; }
    function name()    { return localStorage.getItem(NAME_KEY) || ""; }

    function logout() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(ROLE_KEY);
        localStorage.removeItem(NAME_KEY);
        window.location.replace("/login.html");
    }

    // ── перевірка токена ──────────────────────────────────────────────────────
    const t = token();
    if (!t) { logout(); return; }

    // Базова перевірка exp + читаємо роль прямо з JWT payload
    try {
        const payload = JSON.parse(atob(t.split(".")[1]));
        if (payload.exp && payload.exp * 1000 < Date.now()) { logout(); return; }
        if (payload.role)         localStorage.setItem(ROLE_KEY, payload.role);
        if (payload.display_name) localStorage.setItem(NAME_KEY, payload.display_name);
    } catch { logout(); return; }

    // ── перевірка ролі ────────────────────────────────────────────────────────
    const _required = window.REQUIRED_ROLE;
    if (_required && role() !== _required) {
        window.location.replace("/");
        return;
    }

    // ── публічне API ──────────────────────────────────────────────────────────
    window.authToken   = token;
    window.authHeaders = () => ({ "Authorization": "Bearer " + token() });
    window.authUser    = () => ({ username: "", role: role(), display_name: name() });
    window.authLogout  = logout;
})();
