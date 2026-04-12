console.log("START");

const schemeEl = document.getElementById("scheme");
const tableBody = document.getElementById("pointsBody");

// ====== SVG ======
async function loadScheme() {
    try {
        const res = await fetch("/schemes/home.svg");
        const svgText = await res.text();
        schemeEl.innerHTML = svgText;
        console.log("SVG LOADED");
    } catch (e) {
        console.error("SVG LOAD ERROR", e);
    }
}

// ====== таблиця ======
function renderTable(points) {
    tableBody.innerHTML = "";

    for (const p of points) {

        const row = document.createElement("tr");

        // 🔹 колір
        if (p.quality === "GOOD") row.style.background = "#003300";
        else if (p.quality === "WARN") row.style.background = "#665500";
        else if (p.quality === "ALARM") row.style.background = "#660000";

        row.style.color = "white";
        row.style.fontSize = "16px"; // 🔥 збільшений шрифт

        row.innerHTML = `
            <td>${p.object}</td>
            <td>${p.system}</td>
            <td>${p.pointname}</td>
            <td style="width:120px; text-align:right; font-family:monospace">
                ${p.value}
            </td>
            <td>${p.unit}</td>
            <td>${p.quality}</td>
        `;

        tableBody.appendChild(row);
    }
}

// ====== дані ======
async function loadPoints() {
    try {
        const res = await fetch("/api/points");
        const data = await res.json();
        const points = data.points;

        // 🔹 SVG
        for (const p of points) {
            const id = `${p.object}_${p.system}_${p.pointname}`;
            const el = document.getElementById(id);

            if (!el) continue;

            el.textContent = `${p.value} ${p.unit}`;

            if (p.quality === "GOOD") el.style.fill = "lime";
            else if (p.quality === "WARN") el.style.fill = "orange";
            else if (p.quality === "ALARM") el.style.fill = "red";
        }

        // 🔹 таблиця
        renderTable(points);

    } catch (err) {
        console.error("ERROR:", err);
    }
}

// ====== старт ======
loadScheme();
loadPoints();
setInterval(loadPoints, 2000);