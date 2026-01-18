const chartCanvas = document.getElementById("rangeDistributionChart");
const statusEl = document.getElementById("rangeDistributionStatus");
const coverageEl = document.getElementById("coveragePercent");
const uniqueRangesEl = document.getElementById("uniqueRanges");
const totalSubmissionsEl = document.getElementById("totalSubmissions");

let rangeChart = null;

const formatPercent = (value) => `${value.toFixed(2)}%`;

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
