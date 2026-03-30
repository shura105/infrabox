const API_URL = `http://${window.location.hostname}:8101`;

async function fetchStatus() {
    const r = await fetch(`${API_URL}/status`);
    return r.json();
}

async function fetchPoints() {
    const r = await fetch(`${API_URL}/points`);
    return r.json();
}

async function fetchVolumes() {
    const r = await fetch(`${API_URL}/volumes`);
    return r.json();
}

async function fetchValues(pointId, volume = null, fromTs = null, toTs = null) {
    let url = volume
        ? `${API_URL}/volumes/${volume}/values?point_id=${pointId}`
        : `${API_URL}/points/${pointId}/values?`;

    if (fromTs) url += `&from_ts=${fromTs}`;
    if (toTs) url += `&to_ts=${toTs}`;

    const r = await fetch(url);
    return r.json();
}

async function fetchEvents(pointId, volume = null) {
    let url = volume
        ? `${API_URL}/volumes/${volume}/events?point_id=${pointId}`
        : `${API_URL}/points/${pointId}/events`;

    const r = await fetch(url);
    return r.json();
}

async function fetchSessions() {
    const r = await fetch(`${API_URL}/sessions`);
    return r.json();
}

async function controlArchivator(action) {
    const r = await fetch(`${API_URL}/control/${action}`, { method: "POST" });
    return r.json();
}

async function fetchRange(pointId, fromTs, toTs) {
    const r = await fetch(`${API_URL}/points/${pointId}/range?from_ts=${fromTs}&to_ts=${toTs}`);
    return r.json();
}