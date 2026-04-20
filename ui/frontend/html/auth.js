/**
 * auth.js — клієнтський auth guard
 *
 * Використання:
 *   <script src="/auth.js"></script>                    — будь-яка роль
 *   <script>window.REQUIRED_ROLE = "admin";</script>
 *   <script src="/auth.js"></script>                    — тільки admin
 *
 *   <script>window.REQUIRED_ROLE = "operator"; window.PAGE_SLUG = "dashboard";</script>
 *   <script src="/auth.js"></script>                    — роль + per-user дозвіл
 *
 * Експортує:
 *   authToken()          → string | null
 *   authHeaders()        → { Authorization: "Bearer ..." }
 *   authUser()           → { username, role, display_name }
 *   authPerms()          → { pages?: string[], objects?: string[] }
 *   authCanPage(slug)    → bool
 *   authLogout()         → очищує токен і редиректить на /login.html
 */

(function () {
    const TOKEN_KEY = "token";
    const ROLE_KEY  = "role";
    const NAME_KEY  = "display_name";
    const PERMS_KEY = "permissions";

    function token()   { return localStorage.getItem(TOKEN_KEY); }
    function role()    { return localStorage.getItem(ROLE_KEY) || ""; }
    function name()    { return localStorage.getItem(NAME_KEY) || ""; }
    function perms()   {
        try { return JSON.parse(localStorage.getItem(PERMS_KEY) || "{}"); }
        catch { return {}; }
    }

    function logout() {
        [TOKEN_KEY, ROLE_KEY, NAME_KEY, PERMS_KEY].forEach(k => localStorage.removeItem(k));
        window.location.replace("/login.html");
    }

    // ── перевірка токена ──────────────────────────────────────────────────────
    const t = token();
    if (!t) { logout(); return; }

    let payload;
    try {
        payload = JSON.parse(atob(t.split(".")[1]));
        if (payload.exp && payload.exp * 1000 < Date.now()) { logout(); return; }
        if (payload.role)         localStorage.setItem(ROLE_KEY, payload.role);
        if (payload.display_name) localStorage.setItem(NAME_KEY, payload.display_name);
        localStorage.setItem(PERMS_KEY, JSON.stringify(payload.permissions || {}));
    } catch { logout(); return; }

    // ── перевірка ролі (ієрархія: admin > operator) ──────────────────────────
    const ROLE_RANK = { admin: 99, operator: 10, constructor: 5 };
    const _required = window.REQUIRED_ROLE;
    if (_required) {
        const userRank = ROLE_RANK[role()] ?? 0;
        const needRank = ROLE_RANK[_required] ?? 0;
        if (userRank < needRank) { window.location.replace("/"); return; }
    }

    // ── per-user перевірка сторінки ───────────────────────────────────────────
    const _slug = window.PAGE_SLUG;
    if (_slug && role() !== "admin") {
        const p = perms();
        if (p.pages && !p.pages.includes(_slug)) {
            window.location.replace("/");
            return;
        }
    }

    // ── публічне API ──────────────────────────────────────────────────────────
    window.authToken   = token;
    window.authHeaders = () => ({ "Authorization": "Bearer " + token() });
    window.authUser    = () => ({ username: "", role: role(), display_name: name() });
    window.authPerms   = perms;
    window.authCanPage = (slug) => {
        if (role() === "admin") return true;
        const p = perms();
        if (!p.pages) return true;       // no restriction set
        return p.pages.includes(slug);
    };
    window.authLogout  = logout;
})();
