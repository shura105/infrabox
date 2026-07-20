let _chartInstance = null;
let _rangeTimer = null;
let _currentMode = false;
const TS_MIN = 946684800;   // 2000-01-01 — ігнорувати неправдоподібні ts (1970/uptime пристрою)
let _abortController = null;
let _renderGeneration = 0;

const _BINARY_TYPES  = new Set(["discrete", "operation_mode", "control"]);
// state_calc: value is a severity rank 0..4 → these labels on the Y axis
const _STATE_LABELS  = ["GOOD", "INIT", "UNCERT", "WARN", "ALARM"];
const _CHART_COLORS  = ["#7eb8f7", "#f7a27e", "#7ef7a2", "#f7e27e"];


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
        loading: false,

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
            this.loading = true;
            const toTs   = Math.floor(Date.now() / 1000);
            const fromTs = toTs - 1800;   // останні 30 хв

            try {
                const results = await Promise.all(
                    this.points.map(p => fetchRange(p.id, fromTs, toTs, signal))
                );
                this.points.forEach((p, i) => {
                    this.records[p.id] = (results[i] || []).filter(r => r && r.ts >= TS_MIN);
                });
                this.status = "Останні 30 хв";
                this.loading = false;
                this.renderChart(fromTs * 1000, toTs * 1000);   // фіксуємо вісь на 30-хв вікні
            } catch (e) {
                if (e.name === "AbortError") return;
                this.loading = false;
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

            this.loading = true;

            try {
                const results = await Promise.all(
                    this.points.map(p => fetchRange(p.id, fromTs, toTs, signal))
                );
                this.points.forEach((p, i) => {
                    this.records[p.id] = (results[i] || []).filter(r => r && r.ts >= TS_MIN);
                });
                this.status = `${this.fromDt} → ${this.toDt}`;
                this.loading = false;
                this.renderChart();
            } catch (e) {
                if (e.name === "AbortError") return;
                this.loading = false;
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

                this.loading = true;
                try {
                    const results = await Promise.all(
                        this.points.map(p => fetchRange(p.id, fromTs, toTs, signal))
                    );
                    this.points.forEach((p, i) => {
                        this.records[p.id] = (results[i] || []).filter(r => r && r.ts >= TS_MIN);
                    });
                    this.status = `${formatTs(fromTs)} → ${formatTs(toTs)}`;
                    this.loading = false;
                    this._updateChartData();
                } catch (e) {
                    if (e.name === "AbortError") return;
                    this.loading = false;
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
                const isActive  = p.id === this.activePointId;
                const isBinary  = _BINARY_TYPES.has(p.type);
                const records   = this.records[p.id] || [];
                if (isBinary) {
                    const baseColor  = _CHART_COLORS[i % _CHART_COLORS.length];
                    const baseRad    = isActive ? 2 : 0;
                    const accentRad  = isActive ? 4 : 3;
                    const dataPoints = records.map(r => {
                        const v = r.value;
                        return { x: r.ts * 1000, y: (v === 0 || v === 1) ? v : null };
                    });
                    const bg = [], br = [], rad = [];
                    for (let k = 0; k < dataPoints.length; k++) {
                        const cur  = dataPoints[k].y;
                        const prev = k > 0 ? dataPoints[k-1].y : undefined;
                        const next = k < dataPoints.length - 1 ? dataPoints[k+1].y : undefined;
                        if (cur == null) {
                            bg.push(baseColor); br.push(baseColor); rad.push(0);
                        } else if (next === null) {
                            bg.push("#f47067"); br.push("#f47067"); rad.push(accentRad);
                        } else if (prev === null) {
                            bg.push("#56d364"); br.push("#56d364"); rad.push(accentRad);
                        } else {
                            bg.push(baseColor); br.push(baseColor); rad.push(baseRad);
                        }
                    }
                    ds.data = dataPoints;
                    ds.pointBackgroundColor = bg;
                    ds.pointBorderColor = br;
                    ds.pointRadius = rad;
                } else {
                    ds.data = records.map(r => ({ x: r.ts * 1000, y: r.value ?? null }));
                    ds.borderWidth = isActive ? 2 : 1;
                    ds.pointRadius = isActive ? 1.5 : 0;
                }
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
            const isSingle = this.points.length === 1;
            // одна ВИДИМА точка (галочками) → показуємо її фонові зони станів
            const _visible = this.points.filter(p => this.pointVisible[p.id]);
            const soloPoint = _visible.length === 1 ? _visible[0] : null;

            const datasets = this.points.map((p, i) => {
                const records   = this.records[p.id] || [];
                const isActive  = p.id === this.activePointId;
                const isBinary  = _BINARY_TYPES.has(p.type);
                const isState   = p.type === "state_calc";
                const baseColor = _CHART_COLORS[i % _CHART_COLORS.length];

                // Binary (discrete/operation_mode/control): out-of-range or null
                // values become null in the dataset — spanGaps:false breaks the
                // step-line at UNCERT/NODATA moments.
                let dataPoints, pointBg, pointBorder, pointRad;
                if (isBinary) {
                    dataPoints = records.map(r => {
                        const v = r.value;
                        const valid = (v === 0 || v === 1);
                        return { x: r.ts * 1000, y: valid ? v : null };
                    });

                    // Mark gap boundaries: last valid before gap → red,
                    // first valid after gap → green, both ~2× size.
                    const baseRad   = isActive ? 2 : 0;
                    const accentRad = isActive ? 4 : 3;
                    const bg = [], br = [], rad = [];
                    for (let k = 0; k < dataPoints.length; k++) {
                        const cur  = dataPoints[k].y;
                        const prev = k > 0 ? dataPoints[k-1].y : undefined;
                        const next = k < dataPoints.length - 1 ? dataPoints[k+1].y : undefined;
                        if (cur == null) {
                            bg.push(baseColor); br.push(baseColor); rad.push(0);
                        } else if (next === null) {
                            bg.push("#f47067"); br.push("#f47067"); rad.push(accentRad);
                        } else if (prev === null) {
                            bg.push("#56d364"); br.push("#56d364"); rad.push(accentRad);
                        } else {
                            bg.push(baseColor); br.push(baseColor); rad.push(baseRad);
                        }
                    }
                    pointBg = bg; pointBorder = br; pointRad = rad;
                } else {
                    dataPoints = records.map(r => ({ x: r.ts * 1000, y: r.value ?? null }));
                    pointBg = baseColor;
                    pointBorder = baseColor;
                    pointRad = isActive ? 0.75 : 0;
                }

                return {
                    label: p.pointname,
                    data: dataPoints,
                    spanGaps: false,
                    borderColor: baseColor,
                    backgroundColor: isSingle ? "rgba(126,184,247,0.08)" : "transparent",
                    borderWidth: isActive ? 2 : 1,
                    pointBackgroundColor: pointBg,
                    pointBorderColor: pointBorder,
                    pointRadius: pointRad,
                    pointHoverRadius: isActive ? 4 : 0,
                    stepped: (isBinary || isState) ? "before" : false,
                    tension:  (isBinary || isState) ? 0       : 0.2,
                    order: isActive ? 0 : 1,
                    yAxisID: `y_${p.id}`,
                    hidden: !this.pointVisible[p.id]
                };
            });

            const annotations = {};

            // Analog: фонові зони станів, коли ВИДИМА рівно одна точка (вибрана чи лишена галочками)
            if (soloPoint && !_BINARY_TYPES.has(soloPoint.type) && soloPoint.type !== "state_calc") {
                const p = soloPoint;
                const ax = `y_${p.id}`;   // прив'язка до осі саме цієї точки
                annotations.alarmHigh = { type: "box", yScaleID: ax, yMin: p.alarm_max, yMax: p.max, backgroundColor: "rgba(255,60,60,0.18)", borderWidth: 0 };
                annotations.warnHigh  = { type: "box", yScaleID: ax, yMin: p.warn_max, yMax: p.alarm_max, backgroundColor: "rgba(255,200,0,0.16)", borderWidth: 0 };
                annotations.good      = { type: "box", yScaleID: ax, yMin: p.warn_min, yMax: p.warn_max, backgroundColor: "rgba(60,200,60,0.14)", borderWidth: 0 };
                annotations.warnLow   = { type: "box", yScaleID: ax, yMin: p.alarm_min, yMax: p.warn_min, backgroundColor: "rgba(255,200,0,0.16)", borderWidth: 0 };
                annotations.alarmLow  = { type: "box", yScaleID: ax, yMin: p.min, yMax: p.alarm_min, backgroundColor: "rgba(255,60,60,0.18)", borderWidth: 0 };
                // зони недостовірності — за шкалою (над max / під min), у полі ±3; лавандовий
                const UNCERT = "rgba(179,157,219,0.22)";
                annotations.uncertHigh = { type: "box", yScaleID: ax, yMin: p.max, yMax: p.max + 3, backgroundColor: UNCERT, borderWidth: 0 };
                annotations.uncertLow  = { type: "box", yScaleID: ax, yMin: p.min - 3, yMax: p.min, backgroundColor: UNCERT, borderWidth: 0 };
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
                grid: { color: "#3a4256" }
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
                const isActive  = p.id === this.activePointId;
                const isBinary  = _BINARY_TYPES.has(p.type);
                const isState   = p.type === "state_calc";
                const tickColor = isActive ? "#4caf50" : "#6b7280";

                if (isState) {
                    // severity rank 0..4 with state names as tick labels
                    scales[`y_${p.id}`] = {
                        display: this.pointVisible[p.id],
                        position: "left",
                        min: -0.15, max: 4.15,
                        ticks: {
                            color: tickColor,
                            stepSize: 1,
                            autoSkip: false,
                            callback: function(value) { return _STATE_LABELS[value] || ""; }
                        },
                        afterBuildTicks: function(axis) {
                            axis.ticks = axis.ticks.filter(t => Number.isInteger(t.value) && t.value >= 0 && t.value <= 4);
                        },
                        border: { color: isActive ? "#4caf50" : "#4b5563" },
                        grid:   { color: "#3a4256", drawOnChartArea: isActive },
                    };
                } else if (isBinary) {
                    // Narrow 0/1 range; label_0/label_1 as tick labels
                    scales[`y_${p.id}`] = {
                        display: this.pointVisible[p.id],
                        position: "left",
                        min: -0.15, max: 1.15,
                        ticks: {
                            color: tickColor,
                            stepSize: 1,
                            autoSkip: false,
                            callback: function(value) {
                                if (value === 0) return p.label_0 || "0";
                                if (value === 1) return p.label_1 || "1";
                                return "";
                            }
                        },
                        afterBuildTicks: function(axis) {
                            // keep only the {0, 1} ticks
                            axis.ticks = axis.ticks.filter(t => t.value === 0 || t.value === 1);
                        },
                        border: { color: isActive ? "#4caf50" : "#4b5563" },
                        grid:   { color: "#3a4256", drawOnChartArea: isActive },
                    };
                } else {
                    const PAD = 3;   // розтиснути поле: по 3 одиниці згори й знизу
                    let yMin, yMax;
                    if (p.min != null && p.max != null) {
                        yMin = p.min; yMax = p.max;
                    } else {
                        let dMin = Infinity, dMax = -Infinity;
                        (this.records[p.id] || []).forEach(r => {
                            const v = r.value;
                            if (v != null && isFinite(v)) { if (v < dMin) dMin = v; if (v > dMax) dMax = v; }
                        });
                        if (!isFinite(dMin)) { dMin = 0; dMax = 100; }
                        yMin = dMin; yMax = dMax;
                    }
                    scales[`y_${p.id}`] = {
                        display: this.pointVisible[p.id],
                        position: "left",
                        ticks:   { color: tickColor },
                        border:  { color: isActive ? "#4caf50" : "#4b5563" },
                        grid:    { color: "#3a4256", drawOnChartArea: isActive },
                        min: yMin - PAD, max: yMax + PAD,
                    };
                }
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
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    const p = self.points[ctx.datasetIndex];
                                    const v = ctx.parsed.y;
                                    if (p && p.type === "state_calc") {
                                        return `${p.pointname}: ${_STATE_LABELS[v] || "—"}`;
                                    }
                                    if (p && _BINARY_TYPES.has(p.type)) {
                                        if (v === 0) return `${p.pointname}: ${p.label_0 || "0"}`;
                                        if (v === 1) return `${p.pointname}: ${p.label_1 || "1"}`;
                                        return `${p.pointname}: —`;
                                    }
                                    const unit = p?.unit ? ` ${p.unit}` : "";
                                    return `${p?.pointname || ""}: ${v}${unit}`;
                                }
                            }
                        },
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
