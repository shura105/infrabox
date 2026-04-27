let _chartInstance = null;
let _rangeTimer = null;
let _currentMode = false;
let _abortController = null;
let _renderGeneration = 0;


function _newRequest() {
    if (_abortController) _abortController.abort();
    _abortController = new AbortController();
    return _abortController.signal;
}

function _abortAll() {
    clearTimeout(_rangeTimer);
    if (_abortController) {
        _abortController.abort();
        _abortController = null;
    }
}

window.addEventListener("pagehide", _abortAll);


function pointApp() {
    return {
        pointIds: [],
        points: [],
        activePointId: null,
        records: {},
        pointVisible: {},
        view: "chart",
        fromDt: "",
        toDt: "",
        title: "Loading...",
        status: "",

        goBack() {
            _abortAll();
            window.location.href = "index.html";
        },

        async init() {
            const params = new URLSearchParams(window.location.search);
            const idsParam = params.get("ids") || params.get("id");

            if (!idsParam) {
                this.title = "No points selected";
                return;
            }

            this.pointIds = idsParam.split(",").map(Number).filter(n => Number.isFinite(n));
            this.activePointId = this.pointIds[0];

            try {
                const allPoints = await fetchPoints();
                this.points = this.pointIds
                    .map(id => allPoints.find(p => p.id === id))
                    .filter(Boolean);
            } catch (e) {
                this.title = "Error loading points";
                this.status = e.message;
                console.error("init fetchPoints error:", e);
                return;
            }

            this.points.forEach(p => { this.pointVisible[p.id] = true; });

            if (this.points.length === 1) {
                const p = this.points[0];
                const loc = p.object || p.drop || "";
                const sys = p.system || p.socket || "";
                this.title = `${loc} / ${sys} / ${p.pointname} (${p.id})`;
            } else {
                this.title = this.points.map(p => `${p.pointname} (${p.id})`).join(", ");
            }

            await this.loadCurrent();
        },

        async loadCurrent() {
            _currentMode = true;
            const signal = _newRequest();
            this.status = "Loading...";

            try {
                const results = await Promise.all(
                    this.points.map(p => fetchCurrent(p.id, signal))
                );
                this.points.forEach((p, i) => {
                    this.records[p.id] = results[i];
                });
                this.status = "Current volume";
                this.renderChart();
            } catch (e) {
                if (e.name === "AbortError") return;
                this.status = "Error loading data";
                console.error("loadCurrent error:", e);
            }
        },

        async loadArchive() {
            if (!this.fromDt || !this.toDt) {
                alert("Please select From and To dates");
                return;
            }

            _currentMode = false;
            const signal = _newRequest();
            const fromTs = Math.floor(new Date(this.fromDt).getTime() / 1000);
            const toTs = Math.floor(new Date(this.toDt).getTime() / 1000);

            this.status = "Loading archive...";

            try {
                const results = await Promise.all(
                    this.points.map(p => fetchRange(p.id, fromTs, toTs, signal))
                );
                this.points.forEach((p, i) => {
                    this.records[p.id] = results[i];
                });
                this.status = `${this.fromDt} → ${this.toDt}`;
                this.renderChart();
            } catch (e) {
                if (e.name === "AbortError") return;
                this.status = "Error loading archive";
                console.error("loadArchive error:", e);
            }
        },

        setActive(id) {
            if (!this.pointVisible[id]) return;
            this.activePointId = id;
            this._applyActivePoint();
        },

        toggleVisible(id) {
            this.pointVisible[id] = !this.pointVisible[id];
            this.activePointId = id;
            // Якщо активна точка стала прихованою — передаємо активність першій видимій
            if (!this.pointVisible[this.activePointId]) {
                const next = this.points.find(p => this.pointVisible[p.id]);
                if (next) this.activePointId = next.id;
            }
            this._applyActivePoint();
        },

        zoomIn()  { if (_chartInstance) _chartInstance.zoom(1.2); },
        zoomOut() { if (_chartInstance) _chartInstance.zoom(0.8); },

        _onRangeChange(chart) {
            // У current-режимі зум/пан — суто візуальний, дані не підвантажуємо
            if (_currentMode) return;

            clearTimeout(_rangeTimer);
            if (_abortController) _abortController.abort();

            _rangeTimer = setTimeout(async () => {
                const signal = _newRequest();
                const { min, max } = chart.scales.x;
                const fromTs = Math.floor(min / 1000);
                const toTs = Math.floor(max / 1000);

                this.status = "Loading...";
                try {
                    const results = await Promise.all(
                        this.points.map(p => fetchRange(p.id, fromTs, toTs, signal))
                    );
                    this.points.forEach((p, i) => {
                        this.records[p.id] = results[i];
                    });
                    this.status = `${formatTs(fromTs)} → ${formatTs(toTs)}`;
                    this._updateChartData();
                } catch (e) {
                    if (e.name === "AbortError") return;
                    this.status = "Error loading data";
                    console.error("_onRangeChange error:", e);
                }
            }, 400);
        },

        _updateChartData() {
            if (!_chartInstance) return;
            _chartInstance.data.datasets.forEach((ds, i) => {
                const p = this.points[i];
                if (!p) return;
                const isActive = p.id === this.activePointId;
                ds.data = (this.records[p.id] || []).map(r => ({ x: r.ts * 1000, y: r.value }));
                ds.borderWidth = isActive ? 2 : 1;
                ds.pointRadius = isActive ? 1.5 : 0;
                ds.order = isActive ? 0 : 1;
                ds.yAxisID = `y_${p.id}`;
            });
            _chartInstance.update("none");
        },

        // Зміна активної точки: зберігаємо x-діапазон і перебудовуємо
        // (порядок осей у Chart.js визначає їх позицію — активна має бути першою = найближчою до поля)
        _applyActivePoint() {
            let xMin = null, xMax = null;
            if (_chartInstance) {
                xMin = _chartInstance.scales.x.min;
                xMax = _chartInstance.scales.x.max;
            }
            this.renderChart(xMin, xMax);
        },

        renderChart(xMin = null, xMax = null) {
            const generation = ++_renderGeneration;
            const colors = ["#7eb8f7", "#f7a27e", "#7ef7a2", "#f7e27e"];
            const isSingle = this.points.length === 1;

            const datasets = this.points.map((p, i) => {
                const data = this.records[p.id] || [];
                const isActive = p.id === this.activePointId;
                return {
                    label: p.pointname,
                    data: data.map(r => ({ x: r.ts * 1000, y: r.value ?? null })),
                    spanGaps: false,
                    borderColor: colors[i],
                    backgroundColor: isSingle ? "rgba(126,184,247,0.08)" : "transparent",
                    borderWidth: isActive ? 2 : 1,
                    pointRadius: isActive ? 0.75 : 0,
                    pointHoverRadius: isActive ? 4 : 0,
                    tension: 0.2,
                    order: isActive ? 0 : 1,
                    yAxisID: `y_${p.id}`,
                    hidden: !this.pointVisible[p.id]
                };
            });

            const annotations = {};
            if (isSingle && this.points[0]) {
                const p = this.points[0];
                annotations.alarmHigh = { type: "box", yMin: p.alarm_max, yMax: p.max, backgroundColor: "rgba(255,60,60,0.12)", borderWidth: 0 };
                annotations.warnHigh = { type: "box", yMin: p.warn_max, yMax: p.alarm_max, backgroundColor: "rgba(255,200,0,0.10)", borderWidth: 0 };
                annotations.good = { type: "box", yMin: p.warn_min, yMax: p.warn_max, backgroundColor: "rgba(60,200,60,0.08)", borderWidth: 0 };
                annotations.warnLow = { type: "box", yMin: p.alarm_min, yMax: p.warn_min, backgroundColor: "rgba(255,200,0,0.10)", borderWidth: 0 };
                annotations.alarmLow = { type: "box", yMin: p.min, yMax: p.alarm_min, backgroundColor: "rgba(255,60,60,0.12)", borderWidth: 0 };
            }

            if (generation !== _renderGeneration) return;

            if (_chartInstance) {
                _chartInstance.destroy();
                _chartInstance = null;
            }

            const xAxisConfig = {
                type: "time",
                time: {
                    tooltipFormat: "dd.MM.yyyy HH:mm:ss",
                    displayFormats: {
                        second: "HH:mm:ss",
                        minute: "dd.MM HH:mm",
                        hour:   "dd.MM HH:mm",
                        day:    "dd.MM.yyyy",
                        week:   "dd.MM.yyyy",
                        month:  "MM.yyyy",
                    }
                },
                ticks: {
                    color: "#6b7280",
                    maxTicksLimit: 8,
                    maxRotation: 35,
                    minRotation: 35
                },
                grid: { color: "#1e2130" }
            };
            if (xMin != null) xAxisConfig.min = xMin;
            if (xMax != null) xAxisConfig.max = xMax;

            const scales = { x: xAxisConfig };

            // Активна вісь — першою (Chart.js: перша зліва = найближча до поля)
            const orderedPoints = [
                this.points.find(p => p.id === this.activePointId),
                ...this.points.filter(p => p.id !== this.activePointId)
            ].filter(Boolean);

            orderedPoints.forEach(p => {
                const isActive = p.id === this.activePointId;
                scales[`y_${p.id}`] = {
                    display: this.pointVisible[p.id],

                    position: "left",
                    ticks: { color: isActive ? "#4caf50" : "#6b7280" },
                    border: { color: isActive ? "#4caf50" : "#4b5563" },
                    grid: { color: "#1e2130", drawOnChartArea: isActive },
                    ...(p.min != null && p.max != null ? { min: p.min, max: p.max } : {})
                };
            });

            const ctx = document.getElementById("pointChart").getContext("2d");
            const self = this;

            _chartInstance = new Chart(ctx, {
                type: "line",
                data: { datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    parsing: false,
                    layout: { padding: { top: 16 } },
                    plugins: {
                        legend: { display: false },
                        zoom: {
                            zoom: {
                                wheel: { enabled: true },
                                pinch: { enabled: true },
                                mode: "x",
                                onZoomComplete: ({ chart }) => self._onRangeChange(chart)
                            },
                            pan: {
                                enabled: true,
                                mode: "x",
                                onPanComplete: ({ chart }) => self._onRangeChange(chart)
                            }
                        },
                        annotation: { annotations }
                    },
                    scales
                }
            });

        },

        tableRows() {
            const tsSet = new Set();
            this.points.forEach(p => (this.records[p.id] || []).forEach(r => tsSet.add(r.ts)));
            const lookup = {};
            this.points.forEach(p => {
                (this.records[p.id] || []).forEach(r => {
                    if (!lookup[r.ts]) lookup[r.ts] = {};
                    lookup[r.ts][p.id] = r.value;
                });
            });
            return [...tsSet].sort((a, b) => a - b).map(ts => ({
                ts,
                values: this.points.map(p => lookup[ts]?.[p.id] ?? null)
            }));
        },

        exportCSV() {
            const headers = ["date", "time", ...this.points.map(p => `${p.pointname} (${p.unit})`)];
            const rows = [headers];
            this.tableRows().forEach(row => {
                rows.push([formatDate(row.ts), formatTime(row.ts), ...row.values.map(v => v ?? '')]);
            });
            const csv = rows.map(r => r.join(",")).join("\n");
            const blob = new Blob([csv], { type: "text/csv" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `point_${this.activePointId}.csv`;
            a.click();
        },

        printTable() {
            window.print();
        }
    }
}

function formatTs(ts) {
    return new Date(ts * 1000).toLocaleString();
}

function formatDate(ts) {
    return new Date(ts * 1000).toLocaleDateString();
}

function formatTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString();
}
