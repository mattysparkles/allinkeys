const chartCanvas = document.getElementById("rangeDistributionChart");
const statusEl = document.getElementById("rangeDistributionStatus");
const coverageEl = document.getElementById("coveragePercent");
const uniqueRangesEl = document.getElementById("uniqueRanges");
const totalSubmissionsEl = document.getElementById("totalSubmissions");
const machineGridEl = document.getElementById("machineGrid");
const machineStatusEl = document.getElementById("machineStatus");

let rangeChart = null;

const formatPercent = (value) => `${value.toFixed(2)}%`;
const formatUsage = (value) =>
  typeof value === "number" && !Number.isNaN(value)
    ? `${value.toFixed(1)}%`
    : "n/a";
const formatMachineTime = (value) =>
  typeof value === "string" && value.trim() ? value : "n/a";
const formatTimestamp = (value) =>
  typeof value === "string" && value.trim()
    ? new Date(value).toLocaleString()
    : "n/a";

const mergeIntervals = (intervals) => {
  if (!intervals.length) return [];
  const sorted = intervals.slice().sort((a, b) => a[0] - b[0]);
  return sorted.reduce((acc, [start, end]) => {
    const last = acc[acc.length - 1];
    if (!last || start > last[1]) {
      acc.push([start, end]);
    } else {
      last[1] = Math.max(last[1], end);
    }
    return acc;
  }, []);
};

const calculateCoverage = (ranges) => {
  const intervals = ranges
    .filter(
      (range) =>
        typeof range.normalized_min === "number" &&
        typeof range.normalized_max === "number",
    )
    .map((range) => [range.normalized_min, range.normalized_max]);
  const merged = mergeIntervals(intervals);
  const covered = merged.reduce((sum, [start, end]) => sum + (end - start), 0);
  return covered * 100;
};

const updateSummary = (data) => {
  const coverage =
    typeof data.coverage_percent === "number"
      ? data.coverage_percent
      : calculateCoverage(data.ranges || []);
  const uniqueRanges = data.unique_ranges ?? (data.ranges || []).length;
  const totalSubmissions = data.total_submissions ?? 0;

  coverageEl.textContent = `${coverage.toFixed(2)}%`;
  uniqueRangesEl.textContent = uniqueRanges.toLocaleString();
  totalSubmissionsEl.textContent = totalSubmissions.toLocaleString();
};

const renderScatter = (points) => {
  if (rangeChart) {
    rangeChart.destroy();
  }
  rangeChart = new Chart(chartCanvas, {
    type: "scatter",
    data: {
      datasets: [
        {
          label: "Range coverage",
          data: points,
          borderColor: "#4fd1c5",
          backgroundColor: "rgba(79, 209, 197, 0.6)",
          showLine: true,
          tension: 0.25,
          pointRadius: 4,
          pointHoverRadius: 6,
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
        },
        y: {
          min: 0,
          max: 100,
          title: { display: true, text: "Submission share (%)" },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            title: (items) => `Range ${items[0].raw.rangeValue}`,
            label: (item) =>
              `Submissions: ${item.raw.submissionCount} (${formatPercent(
                item.raw.submissionPercent,
              )})`,
          },
        },
        legend: { display: false },
      },
    },
  });
};

const renderBar = (ranges) => {
  if (rangeChart) {
    rangeChart.destroy();
  }
  rangeChart = new Chart(chartCanvas, {
    type: "bar",
    data: {
      labels: ranges.map((range) => range.range_value),
      datasets: [
        {
          label: "Submission share (%)",
          data: ranges.map((range) => range.submission_percent),
          backgroundColor: "rgba(130, 170, 255, 0.6)",
          borderColor: "#82aaff",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          min: 0,
          max: 100,
          title: { display: true, text: "Submission share (%)" },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            title: (items) => `Range ${items[0].label}`,
            label: (item) => {
              const range = ranges[item.dataIndex];
              return `Submissions: ${range.submission_count} (${formatPercent(
                range.submission_percent,
              )})`;
            },
          },
        },
        legend: { display: false },
      },
    },
  });
};

const loadRangeDistribution = async () => {
  statusEl.textContent = "Loading range distribution...";
  const params = new URLSearchParams(window.location.search);
  const slug = params.get("slug") || "default";

  try {
    const response = await fetch(
      `/v1/dashboard/${slug}/ranges/distribution${window.location.search}`,
    );
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    const data = await response.json();
    updateSummary(data);

    const ranges = data.ranges || [];
    if (!ranges.length) {
      statusEl.textContent = "No range distribution data available.";
      return;
    }

    const points = ranges
      .filter((range) => typeof range.position === "number")
      .map((range) => ({
        x: range.position,
        y: range.submission_percent,
        rangeValue: range.range_value,
        submissionCount: range.submission_count,
        submissionPercent: range.submission_percent,
      }));

    if (points.length) {
      renderScatter(points);
      statusEl.textContent = "";
    } else {
      renderBar(ranges);
      statusEl.textContent = "Position data unavailable. Showing bar chart.";
    }
  } catch (error) {
    statusEl.textContent = "Unable to load range distribution.";
    console.error(error);
  }
};

loadRangeDistribution();

const renderMetric = (label, value, percent) => {
  const wrapper = document.createElement("div");
  wrapper.className = "metric-row";

  const labelEl = document.createElement("span");
  labelEl.textContent = label;

  const valueEl = document.createElement("span");
  const isNumeric = typeof percent === "number" && !Number.isNaN(percent);
  valueEl.textContent = formatUsage(percent);
  if (!isNumeric) {
    valueEl.classList.add("metric-na");
  }

  wrapper.appendChild(labelEl);
  wrapper.appendChild(valueEl);

  const bar = document.createElement("div");
  bar.className = "metric-bar";
  const fill = document.createElement("div");
  fill.className = "metric-bar-fill";
  fill.style.width = isNumeric ? `${Math.min(100, percent)}%` : "0%";
  bar.appendChild(fill);

  return { wrapper, bar, valueEl };
};

const renderMachines = (machines) => {
  machineGridEl.innerHTML = "";
  if (!machines.length) {
    machineStatusEl.textContent = "No machine telemetry available yet.";
    return;
  }
  machineStatusEl.textContent = "";
  machines.forEach((machine) => {
    const card = document.createElement("div");
    card.className = "machine-card";

    const title = document.createElement("h3");
    title.textContent = machine.machine_name || machine.machine_id || "Unknown";
    card.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "machine-meta";
    const gpuName =
      typeof machine.gpu_name === "string" && machine.gpu_name.trim()
        ? machine.gpu_name
        : "n/a";
    meta.textContent = `ID: ${
      machine.machine_id || "n/a"
    } • GPU: ${gpuName} • Last seen: ${formatTimestamp(
      machine.last_seen,
    )}`;
    card.appendChild(meta);

    const cpuMetric = renderMetric("CPU", machine.cpu_percent, machine.cpu_percent);
    card.appendChild(cpuMetric.wrapper);
    card.appendChild(cpuMetric.bar);

    const ramMetric = renderMetric("RAM", machine.ram_percent, machine.ram_percent);
    card.appendChild(ramMetric.wrapper);
    card.appendChild(ramMetric.bar);

    const gpuMetric = renderMetric(
      "GPU",
      machine.gpu_load_percent,
      machine.gpu_load_percent,
    );
    card.appendChild(gpuMetric.wrapper);
    card.appendChild(gpuMetric.bar);

    const diskRow = document.createElement("div");
    diskRow.className = "metric-row";
    const diskLabel = document.createElement("span");
    diskLabel.textContent = "Disk free";
    const diskValue = document.createElement("span");
    const diskPercent =
      typeof machine.disk_free_percent === "number" &&
      !Number.isNaN(machine.disk_free_percent)
        ? `${machine.disk_free_percent.toFixed(1)}%`
        : "n/a";
    if (diskPercent === "n/a") {
      diskValue.classList.add("metric-na");
    }
    diskValue.textContent = diskPercent;
    diskRow.appendChild(diskLabel);
    diskRow.appendChild(diskValue);
    card.appendChild(diskRow);

    const diskEta = document.createElement("div");
    diskEta.className = "machine-meta";
    diskEta.textContent = `Disk ETA: ${formatMachineTime(
      machine.time_to_disk_full,
    )}`;
    card.appendChild(diskEta);

    machineGridEl.appendChild(card);
  });
};

const loadMachineStats = async () => {
  const params = new URLSearchParams(window.location.search);
  const slug = params.get("slug") || "default";
  try {
    const response = await fetch(`/v1/dashboard/${slug}/machines`);
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    const data = await response.json();
    renderMachines(Array.isArray(data.machines) ? data.machines : []);
  } catch (error) {
    machineStatusEl.textContent = "Unable to load machine telemetry.";
    console.error(error);
  }
};

loadMachineStats();
setInterval(loadMachineStats, 5000);
