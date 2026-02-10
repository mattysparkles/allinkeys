(() => {
  const root = document.getElementById("observer-search");
  if (!root) return;

  root.innerHTML = `
    <div class="observer-search-card">
      <div>
        <h2>Seed Range Search</h2>
        <p>Plot a seed value or percent in the keyspace and explore recent range submissions.</p>
      </div>
      <form id="observerFilterForm" class="observer-filter-grid">
        <label>
          Scope
          <select id="observerScope">
            <option value="global">Global (all users)</option>
            <option value="user">My account</option>
            <option value="machine">Specific machine</option>
          </select>
        </label>
        <label class="observer-machine-field">
          Machine
          <select id="observerMachine"></select>
        </label>
        <label>
          Mode
          <select id="observerMode">
            <option value="">All modes</option>
            <option value="vanity">Main (vanity)</option>
            <option value="btc_only">Only (--only)</option>
            <option value="puzzle">Puzzle</option>
            <option value="mnemonic">Mnemonic</option>
          </select>
        </label>
        <label class="observer-range-field">
          Puzzle range
          <select id="observerRangeId"></select>
        </label>
        <label>
          Since
          <input id="observerSince" type="datetime-local" />
        </label>
        <label>
          Until
          <input id="observerUntil" type="datetime-local" />
        </label>
        <label>
          Position min (%)
          <input id="observerPosMin" type="number" min="0" max="100" step="0.1" placeholder="0" />
        </label>
        <label>
          Position max (%)
          <input id="observerPosMax" type="number" min="0" max="100" step="0.1" placeholder="100" />
        </label>
        <button type="submit">Apply Filters</button>
        <button type="button" id="observerResetZoom">Reset Zoom</button>
      </form>
      <div class="observer-filter-status" id="observerFilterStatus"></div>
      <form id="observerSearchForm" class="observer-search-grid">
        <label>
          Input type
          <select id="observerInputType">
            <option value="seed">Seed value</option>
            <option value="percent">Percent (0-100)</option>
          </select>
        </label>
        <label>
          Seed / Percent
          <input id="observerSeedInput" type="text" placeholder="0x... or 34" />
        </label>
        <label>
          Neighbors per side
          <input id="observerNeighbors" type="number" min="1" max="50" value="3" />
        </label>
        <button type="submit">Search</button>
      </form>
      <div class="observer-search-status" id="observerSearchStatus">
        Loading range distribution...
      </div>
      <div class="observer-search-chart">
        <canvas id="observerRangeChart"></canvas>
      </div>
      <div class="observer-search-results">
        <div>
          <h3>Closest below</h3>
          <ul id="observerLowerList"></ul>
        </div>
        <div>
          <h3>Closest above</h3>
          <ul id="observerUpperList"></ul>
        </div>
      </div>
      <div class="observer-visuals">
        <div class="observer-visual-card">
          <h3>Density Histogram</h3>
          <canvas id="observerDensityChart"></canvas>
        </div>
        <div class="observer-visual-card">
          <h3>Jittered Rug</h3>
          <canvas id="observerJitterChart"></canvas>
        </div>
        <div class="observer-visual-card">
          <h3>Coverage Spans</h3>
          <canvas id="observerSpanChart"></canvas>
        </div>
        <div class="observer-visual-card">
          <h3>Range Size vs Position</h3>
          <canvas id="observerSizeChart"></canvas>
        </div>
        <div class="observer-visual-card">
          <h3>Recency vs Position</h3>
          <canvas id="observerRecencyChart"></canvas>
        </div>
        <div class="observer-visual-card">
          <h3>Range Span Histogram</h3>
          <canvas id="observerSpanHistChart"></canvas>
        </div>
      </div>
      <div class="observer-metrics">
        <div class="observer-metric-card">
          <h3>Addresses Checked Today</h3>
          <canvas id="observerAddrTodayChart"></canvas>
        </div>
        <div class="observer-metric-card">
          <h3>Addresses Checked Lifetime</h3>
          <canvas id="observerAddrLifetimeChart"></canvas>
        </div>
        <div class="observer-metric-card">
          <h3>BTC Address Types (Today)</h3>
          <canvas id="observerBtcTypeTodayChart"></canvas>
        </div>
        <div class="observer-metric-card">
          <h3>BTC Address Types (Lifetime)</h3>
          <canvas id="observerBtcTypeLifetimeChart"></canvas>
        </div>
      </div>
      <div class="observer-metrics-status" id="observerMetricsStatus"></div>
    </div>
  `;

  const filterForm = document.getElementById("observerFilterForm");
  const scopeSelect = document.getElementById("observerScope");
  const machineSelect = document.getElementById("observerMachine");
  const modeSelect = document.getElementById("observerMode");
  const rangeSelect = document.getElementById("observerRangeId");
  const sinceInput = document.getElementById("observerSince");
  const untilInput = document.getElementById("observerUntil");
  const posMinInput = document.getElementById("observerPosMin");
  const posMaxInput = document.getElementById("observerPosMax");
  const resetZoomBtn = document.getElementById("observerResetZoom");
  const filterStatusEl = document.getElementById("observerFilterStatus");

  const form = document.getElementById("observerSearchForm");
  const inputTypeEl = document.getElementById("observerInputType");
  const seedInputEl = document.getElementById("observerSeedInput");
  const neighborsEl = document.getElementById("observerNeighbors");
  const statusEl = document.getElementById("observerSearchStatus");
  const lowerListEl = document.getElementById("observerLowerList");
  const upperListEl = document.getElementById("observerUpperList");
  const chartCanvas = document.getElementById("observerRangeChart");
  const densityCanvas = document.getElementById("observerDensityChart");
  const jitterCanvas = document.getElementById("observerJitterChart");
  const spanCanvas = document.getElementById("observerSpanChart");
  const sizeCanvas = document.getElementById("observerSizeChart");
  const recencyCanvas = document.getElementById("observerRecencyChart");
  const spanHistCanvas = document.getElementById("observerSpanHistChart");
  const addrTodayCanvas = document.getElementById("observerAddrTodayChart");
  const addrLifetimeCanvas = document.getElementById("observerAddrLifetimeChart");
  const btcTodayCanvas = document.getElementById("observerBtcTypeTodayChart");
  const btcLifetimeCanvas = document.getElementById("observerBtcTypeLifetimeChart");
  const metricsStatusEl = document.getElementById("observerMetricsStatus");

  if (!window.Chart) {
    statusEl.textContent = "Chart.js unavailable; cannot render range chart.";
    return;
  }

  const zoomPlugin =
    window.ChartZoom || window.ChartZoomPlugin || window["chartjs-plugin-zoom"];
  if (zoomPlugin) {
    window.Chart.register(zoomPlugin);
  }

  const zoomableCharts = [];

  const registerZoomable = (chart) => {
    if (chart && typeof chart.resetZoom === "function") {
      zoomableCharts.push(chart);
    }
  };

  const resetZoom = () => {
    zoomableCharts.forEach((chart) => {
      if (chart && typeof chart.resetZoom === "function") {
        chart.resetZoom();
      }
    });
    const bounds = getXAxisBounds();
    applyAxisBounds(rangeChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: 2 });
    applyAxisBounds(jitterChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: 1 });
    applyAxisBounds(densityChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: densityMax });
    applyAxisBounds(sizeChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: sizeMax });
    applyAxisBounds(recencyChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: 100 });
    applyAxisBounds(spanHistChart, { xMin: 0, xMax: 100, yMin: 0, yMax: spanHistMax });
  };

  const zoomOptions = {
    pan: {
      enabled: true,
      mode: "xy",
      modifierKey: "shift",
    },
    zoom: {
      wheel: { enabled: true },
      pinch: { enabled: true },
      mode: "xy",
    },
  };

  const params = new URLSearchParams(window.location.search);
  const slug = params.get("slug") || "telemetry-dashboard";

  const filterState = {
    scope: params.get("scope") || "global",
    machineId: params.get("machine_id") || "",
    mode: params.get("mode") || "",
    rangeId: params.get("range_id") || "",
    since: params.get("since") || "",
    until: params.get("until") || "",
    positionMin: params.get("pos_min") || "",
    positionMax: params.get("pos_max") || "",
  };

  let rangeChart = null;
  let densityChart = null;
  let jitterChart = null;
  let sizeChart = null;
  let recencyChart = null;
  let spanHistChart = null;
  let addrTodayChart = null;
  let addrLifetimeChart = null;
  let btcTodayChart = null;
  let btcLifetimeChart = null;
  let baseRanges = [];
  let activeRanges = [];
  let densityMax = 0;
  let sizeMax = 0;
  let spanHistMax = 0;

  const formatPercent = (value, digits = 2) =>
    typeof value === "number" && !Number.isNaN(value)
      ? `${value.toFixed(digits)}%`
      : "n/a";

  const parseFloatSafe = (value) => {
    const num = parseFloat(value);
    return Number.isNaN(num) ? null : num;
  };

  const getXAxisBounds = () => {
    let min = 0;
    let max = 100;
    const filteredMin = parseFloatSafe(filterState.positionMin);
    const filteredMax = parseFloatSafe(filterState.positionMax);
    if (filteredMin !== null) {
      min = Math.max(0, Math.min(100, filteredMin));
    }
    if (filteredMax !== null) {
      max = Math.max(min, Math.min(100, filteredMax));
    }
    return { min, max };
  };

  const applyAxisBounds = (chart, { xMin, xMax, yMin, yMax }) => {
    if (!chart) return;
    if (chart.options?.scales?.x) {
      chart.options.scales.x.min = xMin;
      chart.options.scales.x.max = xMax;
    }
    if (chart.options?.scales?.y) {
      if (yMin !== undefined && yMin !== null) {
        chart.options.scales.y.min = yMin;
      }
      if (yMax !== undefined && yMax !== null) {
        chart.options.scales.y.max = yMax;
      }
    }
    if (chart.options?.plugins?.zoom?.limits) {
      chart.options.plugins.zoom.limits.x = { min: xMin, max: xMax };
      if (yMin !== undefined || yMax !== undefined) {
        chart.options.plugins.zoom.limits.y = { min: yMin, max: yMax };
      }
    }
    chart.update("none");
  };

  const toDatetimeLocal = (isoValue) => {
    if (!isoValue) return "";
    const date = new Date(isoValue);
    if (Number.isNaN(date.getTime())) return "";
    const tzOffset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - tzOffset).toISOString().slice(0, 16);
  };

  const normalizeDatetimeInput = (value) => {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toISOString();
  };

  const buildQuery = (extra = {}) => {
    const query = new URLSearchParams();
    const scopeValue = filterState.scope || "global";
    if (scopeValue && scopeValue !== "global") {
      query.set("scope", scopeValue);
    }
    if (scopeValue === "machine" && filterState.machineId) {
      query.set("machine_id", filterState.machineId);
    }
    if (filterState.mode) query.set("mode", filterState.mode);
    if (filterState.rangeId) query.set("range_id", filterState.rangeId);
    if (filterState.since) query.set("since", filterState.since);
    if (filterState.until) query.set("until", filterState.until);
    Object.entries(extra).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        query.set(key, value);
      }
    });
    const qs = query.toString();
    return qs ? `?${qs}` : "";
  };

  const syncUrl = () => {
    const query = new URLSearchParams();
    if (filterState.scope && filterState.scope !== "global") {
      query.set("scope", filterState.scope);
    }
    if (filterState.machineId) query.set("machine_id", filterState.machineId);
    if (filterState.mode) query.set("mode", filterState.mode);
    if (filterState.rangeId) query.set("range_id", filterState.rangeId);
    if (filterState.since) query.set("since", filterState.since);
    if (filterState.until) query.set("until", filterState.until);
    if (filterState.positionMin) query.set("pos_min", filterState.positionMin);
    if (filterState.positionMax) query.set("pos_max", filterState.positionMax);
    const qs = query.toString();
    const next = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState({}, "", next);
  };

  const applyPositionFilter = () => {
    const minValue = parseFloatSafe(filterState.positionMin);
    const maxValue = parseFloatSafe(filterState.positionMax);
    activeRanges = baseRanges.filter((range) => {
      if (typeof range.position !== "number") return false;
      if (minValue !== null && range.position < minValue) return false;
      if (maxValue !== null && range.position > maxValue) return false;
      return true;
    });
    const suffix = baseRanges.length
      ? `Showing ${activeRanges.length} of ${baseRanges.length} ranges.`
      : "No range data loaded.";
    filterStatusEl.textContent = suffix;
  };

  const buildBasePoints = () =>
    activeRanges
      .filter((range) => typeof range.position === "number")
      .map((range) => ({
        x: range.position,
        y: 1,
        rangeValue: range.range_value || range.range_id,
        submissionCount: range.submission_count,
        submissionPercent: range.submission_percent,
      }));

  const buildNeighborPoints = (neighbors) =>
    neighbors.map((range) => ({
      x: range.position,
      y: 1.3,
      rangeValue: range.range_value || range.range_id,
      submissionCount: range.submissions,
      submissionPercent: range.submission_percent,
    }));

  const renderChart = (target, neighbors) => {
    const bounds = getXAxisBounds();
    const points = buildBasePoints();
    const maxY = 2;
    const datasets = [
      {
        label: "Range coverage",
        data: points,
        borderColor: "#3dd5f2",
        backgroundColor: "rgba(61, 213, 242, 0.4)",
        showLine: false,
        pointRadius: 4,
        pointHoverRadius: 6,
      },
    ];

    if (neighbors && neighbors.length) {
      datasets.push({
        label: "Nearest ranges",
        data: buildNeighborPoints(neighbors),
        borderColor: "#f2b63d",
        backgroundColor: "rgba(242, 182, 61, 0.7)",
        pointRadius: 6,
        pointHoverRadius: 8,
      });
    }

    if (target && typeof target.position_percent === "number") {
      datasets.push({
        label: "Target seed",
        data: [
          {
            x: target.position_percent,
            y: 1.6,
            rangeValue: "Target",
            submissionCount: 0,
            submissionPercent: 0,
          },
        ],
        borderColor: "#f25c3d",
        backgroundColor: "rgba(242, 92, 61, 0.9)",
        pointRadius: 8,
        pointHoverRadius: 10,
      });
    }

    if (rangeChart) {
      rangeChart.data.datasets = datasets;
      applyAxisBounds(rangeChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: maxY });
      rangeChart.update();
      return;
    }

    rangeChart = new Chart(chartCanvas, {
      type: "scatter",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            min: bounds.min,
            max: bounds.max,
            title: { display: true, text: "Keyspace position (%)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            min: 0,
            max: maxY,
            title: { display: true, text: "Ranges" },
            ticks: { display: false },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: {
          zoom: {
            ...zoomOptions,
            limits: { x: { min: bounds.min, max: bounds.max }, y: { min: 0, max: maxY } },
          },
          tooltip: {
            callbacks: {
              title: (items) =>
                `Range ${items[0].raw.rangeValue || "unknown"}`,
              label: (item) =>
                `Submissions: ${item.raw.submissionCount || 0} (${formatPercent(
                  item.raw.submissionPercent || 0,
                )})`,
            },
          },
          legend: { labels: { color: "#9fb0c6" } },
        },
      },
    });
    registerZoomable(rangeChart);
  };

  const hashString = (value) => {
    let hash = 0;
    for (let i = 0; i < value.length; i += 1) {
      hash = (hash * 31 + value.charCodeAt(i)) % 2147483647;
    }
    return hash;
  };

  const renderDensityChart = () => {
    if (!densityCanvas) return;
    const bounds = getXAxisBounds();
    const bins = Math.max(60, Math.min(200, Math.floor(activeRanges.length / 15) || 60));
    const counts = new Array(bins).fill(0);
    activeRanges.forEach((range) => {
      if (typeof range.position !== "number") return;
      const idx = Math.min(
        bins - 1,
        Math.max(0, Math.floor((range.position / 100) * bins)),
      );
      counts[idx] += 1;
    });
    const labels = counts.map((_, idx) => ((idx + 0.5) * (100 / bins)).toFixed(1));
    densityMax = Math.max(1, ...counts) * 1.05;
    if (densityChart) {
      densityChart.data.labels = labels;
      densityChart.data.datasets[0].data = counts;
      applyAxisBounds(densityChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: densityMax });
      densityChart.update();
      return;
    }
    densityChart = new Chart(densityCanvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Ranges per bucket",
            data: counts,
            backgroundColor: "rgba(61, 213, 242, 0.6)",
            borderRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            min: bounds.min,
            max: bounds.max,
            ticks: { color: "#9fb0c6", maxTicksLimit: 8 },
            title: { display: true, text: "Keyspace position (%)" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            ticks: { color: "#9fb0c6" },
            min: 0,
            max: densityMax,
            title: { display: true, text: "Range count" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: {
          zoom: {
            ...zoomOptions,
            limits: { x: { min: bounds.min, max: bounds.max }, y: { min: 0, max: densityMax } },
          },
          legend: { display: false },
        },
      },
    });
    registerZoomable(densityChart);
  };

  const renderJitterChart = () => {
    if (!jitterCanvas) return;
    const bounds = getXAxisBounds();
    const points = activeRanges
      .filter((range) => typeof range.position === "number")
      .map((range) => {
        const id = range.range_id || range.range_value || "range";
        const jitter = (hashString(id) % 1000) / 1000;
        return {
          x: range.position,
          y: 0.2 + jitter * 0.6,
          rangeValue: range.range_value || range.range_id,
        };
      });
    if (jitterChart) {
      jitterChart.data.datasets[0].data = points;
      applyAxisBounds(jitterChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: 1 });
      jitterChart.update();
      return;
    }
    jitterChart = new Chart(jitterCanvas, {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "Jittered ranges",
            data: points,
            borderColor: "#f2b63d",
            backgroundColor: "rgba(242, 182, 61, 0.6)",
            pointRadius: 3,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            min: bounds.min,
            max: bounds.max,
            title: { display: true, text: "Keyspace position (%)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            min: 0,
            max: 1,
            ticks: { display: false },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: {
          zoom: {
            ...zoomOptions,
            limits: { x: { min: bounds.min, max: bounds.max }, y: { min: 0, max: 1 } },
          },
          legend: { display: false },
        },
      },
    });
    registerZoomable(jitterChart);
  };

  const drawSpanChart = () => {
    if (!spanCanvas) return;
    const ranges = activeRanges
      .filter(
        (range) =>
          typeof range.normalized_min === "number" &&
          typeof range.normalized_max === "number",
      )
      .slice();
    if (!ranges.length) return;

    ranges.sort((a, b) => a.normalized_min - b.normalized_min);
    const maxLanes = 80;
    const laneEnds = [];
    const laneAssignments = [];

    ranges.forEach((range) => {
      let assigned = -1;
      for (let i = 0; i < laneEnds.length; i += 1) {
        if (range.normalized_min >= laneEnds[i]) {
          assigned = i;
          laneEnds[i] = range.normalized_max;
          break;
        }
      }
      if (assigned === -1) {
        assigned = laneEnds.length;
        laneEnds.push(range.normalized_max);
      }
      if (assigned >= maxLanes) {
        assigned = assigned % maxLanes;
      }
      laneAssignments.push(assigned);
    });

    const ctx = spanCanvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const width = spanCanvas.clientWidth || 600;
    const height = spanCanvas.clientHeight || 240;
    spanCanvas.width = width * dpr;
    spanCanvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0f1424";
    ctx.fillRect(0, 0, width, height);

    const lanesUsed = Math.min(laneEnds.length, maxLanes);
    const laneHeight = height / Math.max(1, lanesUsed);
    ctx.strokeStyle = "rgba(61, 213, 242, 0.7)";
    ctx.lineWidth = Math.max(1, laneHeight * 0.4);

    ranges.forEach((range, idx) => {
      const lane = laneAssignments[idx];
      if (lane >= lanesUsed) return;
      const y = laneHeight * lane + laneHeight / 2;
      const x1 = range.normalized_min * width;
      const x2 = range.normalized_max * width;
      ctx.beginPath();
      ctx.moveTo(x1, y);
      ctx.lineTo(x2, y);
      ctx.stroke();
    });
  };

  const renderSizeChart = () => {
    if (!sizeCanvas) return;
    const bounds = getXAxisBounds();
    const points = activeRanges
      .filter(
        (range) =>
          typeof range.position === "number" &&
          typeof range.normalized_min === "number" &&
          typeof range.normalized_max === "number",
      )
      .map((range) => ({
        x: range.position,
        y: (range.normalized_max - range.normalized_min) * 100,
      }));
    sizeMax = Math.max(1, ...points.map((point) => point.y || 0)) * 1.05;
    if (sizeChart) {
      sizeChart.data.datasets[0].data = points;
      applyAxisBounds(sizeChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: sizeMax });
      sizeChart.update();
      return;
    }
    sizeChart = new Chart(sizeCanvas, {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "Range size",
            data: points,
            borderColor: "#8dd5f2",
            backgroundColor: "rgba(141, 213, 242, 0.6)",
            pointRadius: 3,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            min: bounds.min,
            max: bounds.max,
            title: { display: true, text: "Keyspace position (%)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            min: 0,
            max: sizeMax,
            title: { display: true, text: "Range span (%)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: {
          zoom: {
            ...zoomOptions,
            limits: { x: { min: bounds.min, max: bounds.max }, y: { min: 0, max: sizeMax } },
          },
          legend: { display: false },
        },
      },
    });
    registerZoomable(sizeChart);
  };

  const renderRecencyChart = () => {
    if (!recencyCanvas) return;
    const bounds = getXAxisBounds();
    const dates = activeRanges
      .map((range) => (range.last_seen ? new Date(range.last_seen) : null))
      .filter((d) => d && !Number.isNaN(d.getTime()));
    if (!dates.length) return;
    const minTime = Math.min(...dates.map((d) => d.getTime()));
    const maxTime = Math.max(...dates.map((d) => d.getTime()));
    const span = Math.max(1, maxTime - minTime);
    const points = activeRanges
      .filter(
        (range) =>
          typeof range.position === "number" &&
          range.last_seen &&
          !Number.isNaN(new Date(range.last_seen).getTime()),
      )
      .map((range) => {
        const ts = new Date(range.last_seen).getTime();
        return {
          x: range.position,
          y: ((ts - minTime) / span) * 100,
        };
      });
    if (recencyChart) {
      recencyChart.data.datasets[0].data = points;
      applyAxisBounds(recencyChart, { xMin: bounds.min, xMax: bounds.max, yMin: 0, yMax: 100 });
      recencyChart.update();
      return;
    }
    recencyChart = new Chart(recencyCanvas, {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "Recency",
            data: points,
            borderColor: "#f27ea8",
            backgroundColor: "rgba(242, 126, 168, 0.6)",
            pointRadius: 3,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            min: bounds.min,
            max: bounds.max,
            title: { display: true, text: "Keyspace position (%)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            min: 0,
            max: 100,
            title: { display: true, text: "Recency (older → newer)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: {
          zoom: {
            ...zoomOptions,
            limits: { x: { min: bounds.min, max: bounds.max }, y: { min: 0, max: 100 } },
          },
          legend: { display: false },
        },
      },
    });
    registerZoomable(recencyChart);
  };

  const renderSpanHistogram = () => {
    if (!spanHistCanvas) return;
    const spans = activeRanges
      .filter(
        (range) =>
          typeof range.normalized_min === "number" &&
          typeof range.normalized_max === "number",
      )
      .map((range) => (range.normalized_max - range.normalized_min) * 100)
      .filter((span) => span >= 0);
    const bins = Math.max(20, Math.min(80, Math.floor(spans.length / 10) || 20));
    const counts = new Array(bins).fill(0);
    spans.forEach((span) => {
      const idx = Math.min(bins - 1, Math.max(0, Math.floor((span / 100) * bins)));
      counts[idx] += 1;
    });
    const labels = counts.map((_, idx) => ((idx + 0.5) * (100 / bins)).toFixed(1));
    spanHistMax = Math.max(1, ...counts) * 1.05;
    if (spanHistChart) {
      spanHistChart.data.labels = labels;
      spanHistChart.data.datasets[0].data = counts;
      applyAxisBounds(spanHistChart, { xMin: 0, xMax: 100, yMin: 0, yMax: spanHistMax });
      spanHistChart.update();
      return;
    }
    spanHistChart = new Chart(spanHistCanvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Ranges",
            data: counts,
            backgroundColor: "rgba(120, 166, 255, 0.6)",
            borderRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            min: 0,
            max: 100,
            ticks: { color: "#9fb0c6", maxTicksLimit: 8 },
            title: { display: true, text: "Range span (%)" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            ticks: { color: "#9fb0c6" },
            min: 0,
            max: spanHistMax,
            title: { display: true, text: "Count" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: {
          zoom: { ...zoomOptions, limits: { x: { min: 0, max: 100 }, y: { min: 0, max: spanHistMax } } },
          legend: { display: false },
        },
      },
    });
    registerZoomable(spanHistChart);
  };

  const renderNeighborList = (container, items) => {
    if (!items.length) {
      container.innerHTML = "<li>No ranges yet.</li>";
      return;
    }
    container.innerHTML = items
      .map(
        (range) => `
          <li>
            <strong>${range.range_value || range.range_id || "unknown"}</strong>
            <span>Position: ${formatPercent(range.position, 2)}</span>
            <span>Distance: ${formatPercent(range.distance_percent, 2)}</span>
            <span>Submissions: ${range.submissions}</span>
          </li>
        `,
      )
      .join("");
  };

  const renderMetricChart = (chartRef, canvas, labels, values, label, color) => {
    if (!canvas) return chartRef;
    if (chartRef) {
      chartRef.data.labels = labels;
      chartRef.data.datasets[0].data = values;
      chartRef.update();
      return chartRef;
    }
    const chart = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label,
            data: values,
            backgroundColor: color,
            borderRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: { legend: { display: false } },
      },
    });
    return chart;
  };

  const renderDonutChart = (chartRef, canvas, labels, values, label, colors) => {
    if (!canvas) return chartRef;
    if (chartRef) {
      chartRef.data.labels = labels;
      chartRef.data.datasets[0].data = values;
      chartRef.update();
      return chartRef;
    }
    const chart = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels,
        datasets: [
          {
            label,
            data: values,
            backgroundColor: colors,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: "#9fb0c6" } },
        },
      },
    });
    return chart;
  };

  const loadMetrics = async () => {
    metricsStatusEl.textContent = "Loading aggregate metrics...";
    try {
      const response = await fetch(
        `/v1/dashboard/${slug}/metrics/aggregate${buildQuery()}`,
        { credentials: "same-origin" },
      );
      if (!response.ok) {
        if (response.status === 401 && (filterState.scope || "") !== "global") {
          metricsStatusEl.textContent =
            "Sign in to view user/machine metrics. Showing global data.";
          scopeSelect.value = "global";
          filterState.scope = "global";
          syncUrl();
          return loadMetrics();
        }
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = await response.json();
      const metrics = data.metrics || {};
      const today = metrics.addresses_checked_today || {};
      const lifetime = metrics.addresses_checked_lifetime || {};

      const coinLabels = Object.keys(today).filter(
        (key) => !["p2pkh", "p2sh", "p2wpkh", "taproot"].includes(key),
      );
      const todayValues = coinLabels.map((key) => Number(today[key] || 0));
      const lifetimeValues = coinLabels.map((key) => Number(lifetime[key] || 0));

      addrTodayChart = renderMetricChart(
        addrTodayChart,
        addrTodayCanvas,
        coinLabels,
        todayValues,
        "Checked today",
        "rgba(61, 213, 242, 0.6)",
      );
      addrLifetimeChart = renderMetricChart(
        addrLifetimeChart,
        addrLifetimeCanvas,
        coinLabels,
        lifetimeValues,
        "Checked lifetime",
        "rgba(168, 85, 247, 0.6)",
      );

      const btcTypesToday = data.btc_address_types_today || {};
      const btcTypesLifetime = data.btc_address_types_lifetime || {};
      const btcLabels = ["p2pkh", "p2wpkh", "taproot", "p2sh"];
      const btcTodayValues = btcLabels.map((key) =>
        Number(btcTypesToday[key] || 0),
      );
      const btcLifetimeValues = btcLabels.map((key) =>
        Number(btcTypesLifetime[key] || 0),
      );
      const btcColors = [
        "#60a5fa",
        "#34d399",
        "#f472b6",
        "#f59e0b",
      ];
      btcTodayChart = renderDonutChart(
        btcTodayChart,
        btcTodayCanvas,
        btcLabels,
        btcTodayValues,
        "BTC types today",
        btcColors,
      );
      btcLifetimeChart = renderDonutChart(
        btcLifetimeChart,
        btcLifetimeCanvas,
        btcLabels,
        btcLifetimeValues,
        "BTC types lifetime",
        btcColors,
      );

      metricsStatusEl.textContent = data.timestamp
        ? `Latest telemetry snapshot: ${new Date(data.timestamp).toLocaleString()}`
        : "No metrics snapshots yet.";
    } catch (error) {
      console.error(error);
      metricsStatusEl.textContent = "Unable to load aggregate metrics.";
    }
  };

  const loadDistribution = async () => {
    statusEl.textContent = "Loading range distribution...";
    try {
      const distributionLimit = 250000;
      const response = await fetch(
        `/v1/dashboard/${slug}/ranges/distribution${buildQuery({
          limit: distributionLimit,
        })}`,
      );
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = await response.json();
      baseRanges = Array.isArray(data.ranges) ? data.ranges : [];
      if (!baseRanges.length) {
        statusEl.textContent = "No range distribution data yet.";
      } else {
        statusEl.textContent = "Ready. Submit a search to highlight a target.";
      }
      applyPositionFilter();
      renderChart(null, []);
      renderDensityChart();
      renderJitterChart();
      drawSpanChart();
      renderSizeChart();
      renderRecencyChart();
      renderSpanHistogram();
    } catch (error) {
      console.error(error);
      statusEl.textContent = "Unable to load distribution data.";
    }
  };

  const runSearch = async () => {
    const input = seedInputEl.value.trim();
    const neighbors = Math.max(parseInt(neighborsEl.value, 10) || 3, 1);
    if (!input) {
      statusEl.textContent = "Enter a seed value or percent to search.";
      return;
    }
    statusEl.textContent = "Searching ranges...";
    const query = buildQuery({
      seed: input,
      input_type: inputTypeEl.value,
      neighbors: neighbors,
    });
    try {
      const response = await fetch(`/v1/dashboard/${slug}/ranges/search${query}`);
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = await response.json();
      statusEl.textContent = `Target: ${formatPercent(
        data.position_percent,
        2,
      )} | Seed ${data.seed_hex}`;
      renderNeighborList(lowerListEl, data.lower || []);
      renderNeighborList(upperListEl, data.upper || []);
      const neighborsCombined = [...(data.lower || []), ...(data.upper || [])];
      renderChart(data, neighborsCombined);
    } catch (error) {
      console.error(error);
      statusEl.textContent = "Search failed. Check your input and try again.";
    }
  };

  const loadMachines = async () => {
    machineSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select machine";
    machineSelect.appendChild(placeholder);
    try {
      const response = await fetch("/v1/machines/me");
      if (!response.ok) {
        throw new Error("No session");
      }
      const machines = await response.json();
      machines.forEach((machine) => {
        const option = document.createElement("option");
        option.value = machine.id;
        option.textContent = machine.machine_name || machine.id;
        machineSelect.appendChild(option);
      });
      return true;
    } catch (error) {
      return false;
    }
  };

  const loadRangeIds = async () => {
    rangeSelect.innerHTML = "";
    rangeSelect.disabled = filterState.mode !== "puzzle";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "All ranges";
    rangeSelect.appendChild(placeholder);
    if (filterState.mode !== "puzzle") {
      return;
    }
    try {
      const response = await fetch(
        `/v1/dashboard/${slug}/ranges/ids${buildQuery()}`,
      );
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      const data = await response.json();
      (data.ranges || []).forEach((entry) => {
        if (!entry.range_id) return;
        const option = document.createElement("option");
        option.value = entry.range_id;
        option.textContent = `${entry.range_id} (${entry.count})`;
        rangeSelect.appendChild(option);
      });
    } catch (error) {
      console.error(error);
    }
  };

  const updateFilterStateFromInputs = () => {
    filterState.scope = scopeSelect.value || "global";
    filterState.machineId = machineSelect.value || "";
    filterState.mode = modeSelect.value || "";
    filterState.rangeId = rangeSelect.value || "";
    filterState.since = normalizeDatetimeInput(sinceInput.value);
    filterState.until = normalizeDatetimeInput(untilInput.value);
    filterState.positionMin = posMinInput.value;
    filterState.positionMax = posMaxInput.value;
    syncUrl();
  };

  const applyFilters = async () => {
    updateFilterStateFromInputs();
    await loadDistribution();
    await loadMetrics();
    if (seedInputEl.value.trim()) {
      await runSearch();
    }
  };

  scopeSelect.value = filterState.scope || "global";
  modeSelect.value = filterState.mode || "";
  sinceInput.value = toDatetimeLocal(filterState.since);
  untilInput.value = toDatetimeLocal(filterState.until);
  if (filterState.positionMin) posMinInput.value = filterState.positionMin;
  if (filterState.positionMax) posMaxInput.value = filterState.positionMax;

  const initializeFilters = async () => {
    const hasSession = await loadMachines();
    if (!hasSession) {
      scopeSelect.value = "global";
      scopeSelect.querySelectorAll("option").forEach((opt) => {
        if (opt.value !== "global") {
          opt.disabled = true;
        }
      });
    }
    machineSelect.disabled = scopeSelect.value !== "machine";
    if (filterState.machineId) {
      machineSelect.value = filterState.machineId;
    }
    await loadRangeIds();
    if (filterState.rangeId) {
      rangeSelect.value = filterState.rangeId;
    }
  };

  const handleScopeChange = async () => {
    filterState.scope = scopeSelect.value || "global";
    if (filterState.scope !== "machine") {
      machineSelect.value = "";
    }
    machineSelect.disabled = filterState.scope !== "machine";
    await loadRangeIds();
  };

  scopeSelect.addEventListener("change", handleScopeChange);
  modeSelect.addEventListener("change", async () => {
    filterState.mode = modeSelect.value || "";
    if (filterState.mode !== "puzzle") {
      rangeSelect.value = "";
      filterState.rangeId = "";
    }
    await loadRangeIds();
  });

  if (filterForm) {
    filterForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await applyFilters();
    });
  }

  if (resetZoomBtn) {
    resetZoomBtn.addEventListener("click", () => {
      resetZoom();
    });
  }

  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      runSearch();
    });
  }

  initializeFilters().then(() => {
    loadDistribution();
    loadMetrics();
  });
})();
