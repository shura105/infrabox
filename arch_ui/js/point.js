let _chartInstance = null;
let _rangeTimer = null;
let _currentMode = false;
let _abortController = null;


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

            this.pointIds = idsParam.split(",").map(Number);
            this.activePointId = this.pointIds[0];

            const allPoints = await fetchPoints();
            this.points = this.pointIds
                .map(id => allPoints.find(p => p.id === id))
                .filter(Boolean);

            if (this.points.length === 1) {
                this.title = `${this.points[0].object} / ${this.points[0].system} / ${this.points[0].pointname}`;
            } else {
                this.title = this.points.map(p => p.pointname).join(", ");
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
            this.activePointId = id;
            this._updateChartData();
        },

        zoomIn() { if (_chartInstance) _chartInstance.zoom(1.2); },
        zoomOut() { if (_chartInstance) _chartInstance.zoom(0.8); },
        zoomReset() {
            if (_chartInstance) {
                _chartInstance.resetZoom();
                if (!_currentMode) this._onRangeChange(_chartInstance);
            }
        },

        _onRangeChange(chart) {
            _currentMode = false;

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
                ds.order = isActive ? 0 : 1;
            });
            _chartInstance.update("none");
        },

        renderChart() {
            const colors = ["#7eb8f7", "#f7a27e", "#7ef7a2", "#f7e27e"];
            const isSingle = this.points.length === 1;

            const datasets = this.points.map((p, i) => {
                const data = this.records[p.id] || [];
                const isActive = p.id === this.activePointId;
                return {
                    label: p.pointname,
                    data: data.map(r => ({ x: r.ts * 1000, y: r.value })),
                    borderColor: colors[i],
                    backgroundColor: isSingle ? "rgba(126,184,247,0.08)" : "transparent",
                    borderWidth: isActive ? 2 : 1,
                    pointRadius: 0,
                    tension: 0.2,
                    order: isActive ? 0 : 1,
                    yAxisID: isActive ? "y" : `y${i}`
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

            if (_chartInstance) {
                _chartInstance.destroy();
                _chartInstance = null;
            }

            const ctx = document.getElementById("pointChart").getContext("2d");
            const self = this;

            _chartInstance = new Chart(ctx, {
                type: "line",
                data: { datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    parsing: false,
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
                    scales: {
                        x: {
                            type: "time",
                            time: { tooltipFormat: "dd.MM.yyyy HH:mm:ss" },
                            ticks: { color: "#6b7280", maxTicksLimit: 8 },
                            grid: { color: "#1e2130" }
                        },
                        y: {
                            ticks: { color: "#6b7280" },
                            grid: { color: "#1e2130" }
                        }
                    }
                }
            });
        },

        exportCSV() {
            const activeData = this.records[this.activePointId] || [];
            const rows = [["timestamp", "value"]];
            activeData.forEach(r => rows.push([formatTs(r.ts), r.value]));
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