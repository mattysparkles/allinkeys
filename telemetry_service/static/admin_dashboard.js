const adminToken = window.ADMIN_AUTH_TOKEN || "";
const authHeaders = adminToken ? { Authorization: `Bearer ${adminToken}` } : {};

const summaryEls = {
  totalUsers: document.getElementById("totalUsers"),
  totalMachines: document.getElementById("totalMachines"),
  coveragePercent: document.getElementById("coveragePercent"),
  averageKps: document.getElementById("averageKps"),
};

const machinesTableBody = document.querySelector("#machinesTable tbody");
const usersTableBody = document.querySelector("#usersTable tbody");

const formatNumber = (value) =>
  typeof value === "number" && !Number.isNaN(value)
    ? value.toLocaleString()
    : "0";
const formatPercent = (value) =>
  typeof value === "number" && !Number.isNaN(value)
    ? `${value.toFixed(2)}%`
    : "0.00%";
const formatKps = (value) =>
  typeof value === "number" && !Number.isNaN(value)
    ? value.toFixed(2)
    : "0.00";
const formatTimestamp = (value) =>
  typeof value === "string" && value.trim()
    ? new Date(value).toLocaleString()
    : "n/a";

const fetchJson = async (url) => {
  const response = await fetch(url, { headers: authHeaders });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
};

const renderMachines = (machines) => {
  machinesTableBody.innerHTML = "";
  machines.forEach((machine) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${machine.id}</td>
      <td>${machine.username}</td>
      <td>${machine.machine_name || "n/a"}</td>
      <td>${formatKps(machine.keys_per_sec)}</td>
      <td>${machine.status}</td>
      <td>${formatTimestamp(machine.last_seen)}</td>
    `;
    machinesTableBody.appendChild(row);
  });
};

const renderUsers = (users) => {
  usersTableBody.innerHTML = "";
  users.forEach((user) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${user.username}</td>
      <td>${formatNumber(user.machine_count)}</td>
      <td>${formatKps(user.avg_kps)}</td>
      <td>${formatPercent(user.coverage_percent)}</td>
    `;
    usersTableBody.appendChild(row);
  });
};

const renderLineChart = (canvasId, label, points) => {
  const ctx = document.getElementById(canvasId);
  return new Chart(ctx, {
    type: "line",
    data: {
      labels: points.map((point) => new Date(point.timestamp).toLocaleTimeString()),
      datasets: [
        {
          label,
          data: points.map((point) => point.value),
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56, 189, 248, 0.2)",
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          ticks: { color: "#cbd5f5" },
        },
        x: {
          ticks: { color: "#cbd5f5" },
        },
      },
      plugins: {
        legend: { labels: { color: "#e2e8f0" } },
      },
    },
  });
};

const renderCoverageChart = (points) => {
  return renderLineChart("coverageChart", "Coverage %", points);
};

const loadDashboard = async () => {
  try {
    const [users, machines, keyspace, kpsSeries, backlogSeries, coverageSeries] =
      await Promise.all([
        fetchJson("/admin/users/summary"),
        fetchJson("/admin/machines/summary"),
        fetchJson("/admin/keyspace/progress"),
        fetchJson("/admin/timeseries/kps"),
        fetchJson("/admin/timeseries/backlog"),
        fetchJson("/admin/timeseries/coverage"),
      ]);

    summaryEls.totalUsers.textContent = formatNumber(users.length);
    summaryEls.totalMachines.textContent = formatNumber(machines.length);
    summaryEls.coveragePercent.textContent = formatPercent(
      keyspace.coverage_percent,
    );
    const totalAvgKps = users.reduce((sum, user) => sum + user.avg_kps, 0);
    const avgKps = users.length ? totalAvgKps / users.length : 0;
    summaryEls.averageKps.textContent = formatKps(avgKps);

    renderMachines(machines);
    renderUsers(users);
    renderCoverageChart(coverageSeries.points);
    renderLineChart("kpsChart", "Keys per second", kpsSeries.points);
    renderLineChart("backlogChart", "Backlog events", backlogSeries.points);
  } catch (error) {
    console.error(error);
  }
};

loadDashboard();
