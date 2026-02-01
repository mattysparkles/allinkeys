(() => {
  const root = document.getElementById("observer-search");
  if (!root) return;

  root.innerHTML = `
    <div class="observer-search-card">
      <div>
        <h2>Seed Range Search</h2>
        <p>Plot a seed value or percent in the keyspace and view the closest submitted ranges.</p>
      </div>
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
          <h3 class="observer-subtitle">Recency vs Position</h3>
          <canvas id="observerRecencyChart"></canvas>
        </div>
      </div>
    </div>
  `;

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

  if (!window.Chart) {
    statusEl.textContent = "Chart.js unavailable; cannot render range chart.";
    return;
  }

  const params = new URLSearchParams(window.location.search);
  const slug = params.get("slug") || "telemetry-dashboard";
  const mode = params.get("mode");
  const since = params.get("since");

  let rangeChart = null;
  let densityChart = null;
  let jitterChart = null;
  let sizeChart = null;
  let recencyChart = null;
  let baseRanges = [];

  const formatPercent = (value, digits = 2) =>
    typeof value === "number" && !Number.isNaN(value)
      ? `${value.toFixed(digits)}%`
      : "n/a";

  const buildQuery = (extra = {}) => {
    const query = new URLSearchParams();
    if (mode) query.set("mode", mode);
    if (since) query.set("since", since);
    Object.entries(extra).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        query.set(key, value);
      }
    });
    const qs = query.toString();
    return qs ? `?${qs}` : "";
  };

  const buildBasePoints = () =>
    baseRanges
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
            min: 0,
            max: 100,
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
    const bins = Math.max(60, Math.min(200, Math.floor(baseRanges.length / 15) || 60));
    const counts = new Array(bins).fill(0);
    baseRanges.forEach((range) => {
      if (typeof range.position !== "number") return;
      const idx = Math.min(
        bins - 1,
        Math.max(0, Math.floor((range.position / 100) * bins)),
      );
      counts[idx] += 1;
    });
    const labels = counts.map((_, idx) => ((idx + 0.5) * (100 / bins)).toFixed(1));
    if (densityChart) {
      densityChart.data.labels = labels;
      densityChart.data.datasets[0].data = counts;
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
            ticks: { color: "#9fb0c6", maxTicksLimit: 8 },
            title: { display: true, text: "Keyspace position (%)" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            ticks: { color: "#9fb0c6" },
            title: { display: true, text: "Range count" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: {
          legend: { display: false },
        },
      },
    });
  };

  const renderJitterChart = () => {
    if (!jitterCanvas) return;
    const points = baseRanges
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
            min: 0,
            max: 100,
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
          legend: { display: false },
        },
      },
    });
  };

  const drawSpanChart = () => {
    if (!spanCanvas) return;
    const ranges = baseRanges
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
    ctx.scale(dpr, dpr);
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
    const points = baseRanges
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
    if (sizeChart) {
      sizeChart.data.datasets[0].data = points;
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
            min: 0,
            max: 100,
            title: { display: true, text: "Keyspace position (%)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
          y: {
            min: 0,
            title: { display: true, text: "Range span (%)" },
            ticks: { color: "#9fb0c6" },
            grid: { color: "rgba(255,255,255,0.05)" },
          },
        },
        plugins: { legend: { display: false } },
      },
    });
  };

  const renderRecencyChart = () => {
    if (!recencyCanvas) return;
    const dates = baseRanges
      .map((range) => (range.last_seen ? new Date(range.last_seen) : null))
      .filter((d) => d && !Number.isNaN(d.getTime()));
    if (!dates.length) return;
    const minTime = Math.min(...dates.map((d) => d.getTime()));
    const maxTime = Math.max(...dates.map((d) => d.getTime()));
    const span = Math.max(1, maxTime - minTime);
    const points = baseRanges
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
            min: 0,
            max: 100,
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
        plugins: { legend: { display: false } },
      },
    });
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

  const loadDistribution = async () => {
    statusEl.textContent = "Loading range distribution...";
    try {
      const distributionLimit = 250000;
      const response = await fetch(
        `/v1/dashboard/${slug}/ranges/distribution${buildQuery({ limit: distributionLimit })}`,
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
      renderChart(null, []);
      renderDensityChart();
      renderJitterChart();
      drawSpanChart();
      renderSizeChart();
      renderRecencyChart();
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

  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      runSearch();
    });
  }

  loadDistribution();
})();
