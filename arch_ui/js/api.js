const API_URL = `http://${window.location.hostname}:8101`;

async function _fetchJson(url, options = {}) {
    const r = await fetch(url, options);
    if (!r.ok) throw new Error(`HTTP ${r.status} ${r.statusText} — ${url}`);
    return r.json();
}

async function fetchStatus() {
    return _fetchJson(`${API_URL}/status`);
}

async function fetchPoints() {
    return _fetchJson(`${API_URL}/points`);
}

async function fetchVolumes() {
    return _fetchJson(`${API_URL}/volumes`);
}

async function fetchCurrent(pointId, signal) {
    return _fetchJson(`${API_URL}/points/${pointId}/current`, { signal });
}

async function fetchValues(pointId, volume = null, fromTs = null, toTs = null) {
    const params = new URLSearchParams({ point_id: pointId });
    if (fromTs) params.set("from_ts", fromTs);
    if (toTs) params.set("to_ts", toTs);

    const url = volume
        ? `${API_URL}/volumes/${volume}/values?${params}`
        : `${API_URL}/points/${pointId}/values?${params}`;

    return _fetchJson(url);
}

async function fetchEvents(pointId, volume = null) {
    const url = volume
        ? `${API_URL}/volumes/${volume}/events?point_id=${pointId}`
        : `${API_URL}/points/${pointId}/events`;

    return _fetchJson(url);
}

async function fetchSessions() {
    return _fetchJson(`${API_URL}/sessions`);
}

async function controlArchivator(action) {
    return _fetchJson(`${API_URL}/control/${action}`, { method: "POST" });
}

async function fetchRange(pointId, fromTs, toTs, signal) {
    return _fetchJson(
        `${API_URL}/points/${pointId}/range?from_ts=${fromTs}&to_ts=${toTs}`,
        { signal }
    );
}
