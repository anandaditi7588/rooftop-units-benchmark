/* Setup page logic: theme toggle, competitor selection, parameter upload,
 * job kick-off and progress polling. */
(() => {
  "use strict";

  // ---------------- Theme ----------------
  const root = document.documentElement;
  const themeToggle = document.getElementById("themeToggle");

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    const icon = themeToggle.querySelector("i");
    icon.className = theme === "dark" ? "bi bi-moon-stars" : "bi bi-sun";
    localStorage.setItem("rtu-theme", theme);
  }
  applyTheme(localStorage.getItem("rtu-theme") || "dark");
  themeToggle.addEventListener("click", () => {
    applyTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });

  // ---------------- Competitor selection ----------------
  document.getElementById("selectAllBtn").addEventListener("click", () => {
    document.querySelectorAll(".competitor-checkbox").forEach((cb) => (cb.checked = true));
  });
  document.getElementById("clearAllBtn").addEventListener("click", () => {
    document.querySelectorAll(".competitor-checkbox").forEach((cb) => (cb.checked = false));
  });

  function selectedCompetitors() {
    return Array.from(document.querySelectorAll(".competitor-checkbox"))
      .filter((cb) => cb.checked)
      .map((cb) => cb.value);
  }

  // ---------------- Default parameter info ----------------
  fetch("/api/config/default-parameters")
    .then((r) => (r.ok ? r.json() : Promise.reject(r)))
    .then((data) => {
      document.getElementById("defaultParamText").textContent =
        `Physical_Data.xlsx found — ${data.count} parameters ready to benchmark.`;
    })
    .catch(() => {
      document.getElementById("defaultParamText").textContent =
        "Physical_Data.xlsx will be auto-generated with sample parameters on first run.";
    });

  // ---------------- Upload toggle ----------------
  const uploadBox = document.getElementById("uploadBox");
  let uploadedToken = null;

  document.querySelectorAll('input[name="uploadChoice"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const yes = document.getElementById("uploadYes").checked;
      uploadBox.classList.toggle("d-none", !yes);
    });
  });

  document.getElementById("uploadBtn").addEventListener("click", async () => {
    const fileInput = document.getElementById("paramFile");
    const resultBox = document.getElementById("uploadResult");
    if (!fileInput.files.length) {
      alert("Choose an Excel file first.");
      return;
    }
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    resultBox.classList.remove("d-none");
    resultBox.innerHTML = '<i class="bi bi-hourglass-split pulsing"></i> Reading Column B…';

    try {
      const resp = await fetch("/api/upload-parameters", { method: "POST", body: formData });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Upload failed");
      uploadedToken = data.token;
      resultBox.innerHTML =
        `<i class="bi bi-check-circle text-success"></i> Loaded <strong>${data.count}</strong> parameters ` +
        `from Column B. First few: <em>${data.preview.slice(0, 5).map(p => p.parameter).join(", ")}…</em>`;
    } catch (err) {
      resultBox.innerHTML = `<i class="bi bi-x-circle text-danger"></i> ${err.message}`;
    }
  });

  // ---------------- Start benchmark ----------------
  const progressSection = document.getElementById("progressSection");
  const progressBar = document.getElementById("progressBar");
  const progressPct = document.getElementById("progressPct");
  const progressStage = document.getElementById("progressStage");
  const statusMessage = document.getElementById("statusMessage");
  const errorMessage = document.getElementById("errorMessage");
  const resultActions = document.getElementById("resultActions");
  const cancelBtn = document.getElementById("cancelBtn");

  let pollTimer = null;
  let currentJobId = null;

  cancelBtn.addEventListener("click", async () => {
    if (!currentJobId) return;
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Cancelling…";
    try {
      await fetch(`/api/cancel-benchmark/${currentJobId}`, { method: "POST" });
      // The next poll tick will pick up status "cancelled" and update the UI.
    } catch (err) {
      cancelBtn.disabled = false;
      cancelBtn.innerHTML = '<i class="bi bi-x-circle"></i> Cancel';
    }
  });

  document.getElementById("startBtn").addEventListener("click", async () => {
    const competitors = selectedCompetitors();
    if (!competitors.length) {
      alert("Select at least one competitor.");
      return;
    }
    const useDefault = document.getElementById("uploadNo").checked;
    if (!useDefault && !uploadedToken) {
      alert("Upload a parameter Excel first, or switch back to using Physical_Data.xlsx.");
      return;
    }

    const seriesName = document.getElementById("seriesName").value.trim();
    const unitConfigDescription = document.getElementById("unitConfigDescription").value.trim();

    const payload = {
      competitors,
      use_default_parameters: useDefault,
      uploaded_file_token: useDefault ? null : uploadedToken,
      enable_web_scraping: document.getElementById("enableScraping").checked,
      series_name: seriesName || null,
      unit_config_description: unitConfigDescription || null,
    };

    progressSection.classList.remove("d-none");
    errorMessage.classList.add("d-none");
    resultActions.classList.add("d-none");
    progressBar.classList.add("progress-bar-animated");
    progressBar.style.width = "2%";
    progressPct.textContent = "2%";
    progressStage.textContent = "Queuing job…";
    statusMessage.textContent = "";
    cancelBtn.disabled = false;
    cancelBtn.classList.remove("d-none");
    cancelBtn.innerHTML = '<i class="bi bi-x-circle"></i> Cancel';

    try {
      const resp = await fetch("/api/start-benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Failed to start benchmark");
      currentJobId = data.job_id;
      pollJob(data.job_id);
    } catch (err) {
      showError(err.message);
    }
  });

  function pollJob(jobId) {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const resp = await fetch(`/api/job-status/${jobId}`);
        const job = await resp.json();
        progressBar.style.width = `${job.progress}%`;
        progressPct.textContent = `${job.progress}%`;
        progressStage.textContent = prettyStage(job.stage) || "Working…";
        statusMessage.textContent = job.message || "";

        if (job.status === "completed") {
          clearInterval(pollTimer);
          progressBar.classList.remove("progress-bar-animated");
          cancelBtn.classList.add("d-none");
          currentJobId = null;
          const scopeNote = job.stats.unit_query
            ? ` (scoped to "${job.stats.unit_query}")`
            : "";
          statusMessage.innerHTML =
            `<i class="bi bi-check-circle text-success"></i> Done${scopeNote} — ` +
            `${job.stats.parameters_matched}/${job.stats.parameters_total} parameters matched ` +
            `(${Math.round(job.stats.extraction_accuracy * 100)}% accuracy) from ` +
            `${job.stats.documents_processed} document(s).`;
          resultActions.classList.remove("d-none");
        } else if (job.status === "failed") {
          clearInterval(pollTimer);
          cancelBtn.classList.add("d-none");
          currentJobId = null;
          showError(job.error || "Benchmarking job failed.");
        } else if (job.status === "cancelled") {
          clearInterval(pollTimer);
          progressBar.classList.remove("progress-bar-animated");
          cancelBtn.classList.add("d-none");
          currentJobId = null;
          statusMessage.innerHTML = '<i class="bi bi-x-circle text-warning"></i> Cancelled — ' +
            (job.message || "stopped before finishing.");
        }
      } catch (err) {
        clearInterval(pollTimer);
        showError("Lost connection to the server while polling job status.");
      }
    }, 1200);
  }

  function prettyStage(stage) {
    const labels = {
      init: "Preparing",
      collecting: "Searching official sources",
      extracting: "Extracting PDF specifications",
      matching: "AI semantic matching",
      reporting: "Generating Excel & dashboard data",
      done: "Completed",
    };
    return labels[stage] || stage;
  }

  function showError(msg) {
    errorMessage.textContent = msg;
    errorMessage.classList.remove("d-none");
    progressBar.classList.remove("progress-bar-animated");
  }
})();
