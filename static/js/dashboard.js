/* Dashboard logic: KPI cards, Chart.js visualizations, confidence heatmap,
 * and the full DataTables-powered comparison table. Reads a single JSON
 * payload from /api/results (written by core/pipeline.py) so this file has
 * no server-side templating dependency beyond that one endpoint. */
(() => {
  "use strict";

  // ---------------- Theme (shared behavior with the setup page) ----------------
  const root = document.documentElement;
  const themeToggle = document.getElementById("themeToggle");
  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    themeToggle.querySelector("i").className = theme === "dark" ? "bi bi-moon-stars" : "bi bi-sun";
    localStorage.setItem("rtu-theme", theme);
  }
  applyTheme(localStorage.getItem("rtu-theme") || "dark");
  themeToggle.addEventListener("click", () => {
    applyTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });
  document.getElementById("refreshBtn").addEventListener("click", () => location.reload());

  // ---------------- Helpers ----------------
  const NUMERIC_RE = /-?\d[\d,]*\.?\d*/;
  function parseNumeric(value) {
    if (!value) return null;
    const match = NUMERIC_RE.exec(String(value));
    if (!match) return null;
    const n = parseFloat(match[0].replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  }

  function confidenceColor(score) {
    // 0 -> red, 0.5 -> amber, 1 -> green
    const stops = [
      { p: 0, c: [248, 113, 113] },
      { p: 0.5, c: [251, 191, 36] },
      { p: 1, c: [52, 211, 153] },
    ];
    let lo = stops[0], hi = stops[stops.length - 1];
    for (let i = 0; i < stops.length - 1; i++) {
      if (score >= stops[i].p && score <= stops[i + 1].p) { lo = stops[i]; hi = stops[i + 1]; break; }
    }
    const range = hi.p - lo.p || 1;
    const t = (score - lo.p) / range;
    const rgb = lo.c.map((v, i) => Math.round(v + (hi.c[i] - v) * t));
    return `rgb(${rgb.join(",")})`;
  }

  // ---------------- Load data ----------------
  fetch("/api/results")
    .then((r) => (r.ok ? r.json() : Promise.reject(r)))
    .then(render)
    .catch(() => {
      document.getElementById("emptyState").classList.remove("d-none");
    });

  function render(data) {
    document.getElementById("dashboardContent").classList.remove("d-none");

    const competitors = data.competitors; // [{id, name, color}]
    const parameters = data.parameters;
    const summary = data.summary;

    renderScopeBanner(data.unit_query);
    renderKpis(summary);
    renderParameterSelect(parameters);
    const barChart = renderBarChart(competitors, parameters, 0);
    renderRadarChart(competitors, parameters);
    renderPieChart(summary);
    renderStackedChart(competitors, parameters);
    renderHeatmap(competitors, parameters);
    renderTable(competitors, parameters);

    document.getElementById("parameterSelect").addEventListener("change", (e) => {
      updateBarChart(barChart, competitors, parameters, parseInt(e.target.value, 10));
    });
  }

  // ---------------- Query scope banner ----------------
  function renderScopeBanner(unitQuery) {
    const banner = document.getElementById("scopeBanner");
    if (!unitQuery) {
      banner.classList.add("d-none");
      return;
    }
    document.getElementById("scopeBannerText").textContent = `Benchmark scope: ${unitQuery}`;
    banner.classList.remove("d-none");
  }

  // ---------------- KPI cards ----------------
  function renderKpis(summary) {
    document.getElementById("kpiCompetitors").textContent = summary.competitors.length;
    document.getElementById("kpiParameters").textContent = summary.parameters_total;
    document.getElementById("kpiMatched").textContent = summary.parameters_matched;
    document.getElementById("kpiMissing").textContent = summary.parameters_missing;
    document.getElementById("kpiDocs").textContent = summary.documents_processed;
    document.getElementById("kpiAccuracy").textContent = `${Math.round(summary.extraction_accuracy * 100)}%`;
  }

  // ---------------- Parameter selector ----------------
  function renderParameterSelect(parameters) {
    const select = document.getElementById("parameterSelect");
    select.innerHTML = "";
    parameters.forEach((p, idx) => {
      const opt = document.createElement("option");
      opt.value = idx;
      opt.textContent = p.parameter;
      select.appendChild(opt);
    });
  }

  // ---------------- Bar chart ----------------
  function renderBarChart(competitors, parameters, index) {
    const ctx = document.getElementById("barChart");
    const param = parameters[index];
    const { labels, values, colors } = barChartData(competitors, param);
    return new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label: param.parameter, data: values, backgroundColor: colors, borderRadius: 6 }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  function updateBarChart(chart, competitors, parameters, index) {
    const param = parameters[index];
    const { labels, values, colors } = barChartData(competitors, param);
    chart.data.labels = labels;
    chart.data.datasets[0].label = param.parameter;
    chart.data.datasets[0].data = values;
    chart.data.datasets[0].backgroundColor = colors;
    chart.update();
  }

  function barChartData(competitors, param) {
    const labels = competitors.map((c) => c.name);
    const colors = competitors.map((c) => c.color);
    const values = competitors.map((c) => parseNumeric(param.values[c.id]?.value) ?? 0);
    return { labels, values, colors };
  }

  // ---------------- Radar chart ----------------
  function renderRadarChart(competitors, parameters) {
    const ctx = document.getElementById("radarChart");

    // Pick up to 6 parameters that have >=2 competitors with numeric values.
    const numericParams = [];
    for (const p of parameters) {
      const numeric = competitors
        .map((c) => parseNumeric(p.values[c.id]?.value))
        .filter((v) => v !== null);
      if (numeric.length >= 2) numericParams.push(p);
      if (numericParams.length === 6) break;
    }

    if (numericParams.length < 3) {
      ctx.parentElement.innerHTML =
        '<p class="text-muted small mb-0">Not enough overlapping numeric parameters yet to draw a radar chart.</p>';
      return;
    }

    const labels = numericParams.map((p) => p.parameter);
    const datasets = competitors.map((c) => {
      const data = numericParams.map((p) => {
        const raw = competitors.map((cc) => parseNumeric(p.values[cc.id]?.value)).filter((v) => v !== null);
        const max = Math.max(...raw, 1);
        const value = parseNumeric(p.values[c.id]?.value);
        return value === null ? 0 : value / max;
      });
      return {
        label: c.name, data,
        borderColor: c.color, backgroundColor: c.color + "33",
        pointBackgroundColor: c.color,
      };
    });

    new Chart(ctx, {
      type: "radar",
      data: { labels, datasets },
      options: { responsive: true, scales: { r: { min: 0, max: 1, ticks: { display: false } } } },
    });
  }

  // ---------------- Pie chart ----------------
  function renderPieChart(summary) {
    const ctx = document.getElementById("pieChart");
    new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["Matched", "Missing"],
        datasets: [{
          data: [summary.parameters_matched, summary.parameters_missing],
          backgroundColor: ["#34d399", "#f87171"],
        }],
      },
      options: { responsive: true, plugins: { legend: { position: "bottom" } } },
    });
  }

  // ---------------- Stacked matched/missing per competitor ----------------
  function renderStackedChart(competitors, parameters) {
    const ctx = document.getElementById("stackedChart");
    const matched = competitors.map((c) => parameters.filter((p) => p.values[c.id]?.value).length);
    const missing = competitors.map((c) => parameters.length - parameters.filter((p) => p.values[c.id]?.value).length);

    new Chart(ctx, {
      type: "bar",
      data: {
        labels: competitors.map((c) => c.name),
        datasets: [
          { label: "Matched", data: matched, backgroundColor: "#34d399" },
          { label: "Missing", data: missing, backgroundColor: "#f87171" },
        ],
      },
      options: {
        responsive: true,
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  // ---------------- Confidence heatmap ----------------
  function renderHeatmap(competitors, parameters) {
    const table = document.getElementById("heatmapTable");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.innerHTML = "<th>Parameter</th>" + competitors.map((c) => `<th>${c.name}</th>`).join("");
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    parameters.forEach((p) => {
      const tr = document.createElement("tr");
      let html = `<th>${p.parameter}</th>`;
      competitors.forEach((c) => {
        const cell = p.values[c.id];
        const conf = cell?.value ? cell.confidence : 0;
        const bg = cell?.value ? confidenceColor(conf) : "transparent";
        const text = cell?.value ? conf.toFixed(2) : "—";
        html += `<td class="heatmap-cell" style="background:${bg}">${text}</td>`;
      });
      tr.innerHTML = html;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
  }

  // ---------------- Main comparison DataTable ----------------
  function renderTable(competitors, parameters) {
    const table = document.getElementById("comparisonTable");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.innerHTML =
      "<th>Category</th><th>Parameter</th><th>Unit</th>" +
      competitors.map((c) => `<th>${c.name}</th>`).join("") +
      "<th>Discrepancy</th>";
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    parameters.forEach((p) => {
      const tr = document.createElement("tr");
      if (p.has_discrepancy) tr.classList.add("row-discrepancy");
      tr.dataset.discrepancy = p.has_discrepancy ? "1" : "0";

      let html = `<td>${p.category || ""}</td><td>${p.parameter}</td><td>${p.unit || ""}</td>`;
      competitors.forEach((c) => {
        const cell = p.values[c.id];
        const value = cell?.value || "";
        let cls = "";
        if (!value) cls = "cell-missing";
        else if (cell.is_best) cls = "cell-best";
        html += `<td class="${cls}" title="${cell?.source_document ? "Source: " + cell.source_document + " (p." + (cell.page_number || "?") + ") — confidence " + cell.confidence : ""}">${value || "—"}</td>`;
      });
      html += `<td>${p.has_discrepancy ? '<span class="badge bg-warning text-dark">Yes</span>' : '<span class="badge bg-secondary">No</span>'}</td>`;
      tr.innerHTML = html;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    $.fn.dataTable.ext.search.push((settings, data, dataIndex) => {
      if (!document.getElementById("discrepancyOnly").checked) return true;
      const row = table.querySelectorAll("tbody tr")[dataIndex];
      return row && row.dataset.discrepancy === "1";
    });

    const dt = $(table).DataTable({
      scrollY: "420px",
      scrollX: true,
      scrollCollapse: true,
      paging: true,
      pageLength: 25,
      dom: "Bfrtip",
      buttons: [{ extend: "colvis", text: '<i class="bi bi-eye"></i> Columns' }],
      order: [[1, "asc"]],
    });

    document.getElementById("discrepancyOnly").addEventListener("change", () => dt.draw());
  }
})();
