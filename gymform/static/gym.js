/* =========================================================================
   Silicon Bay gym registration form — client-side helpers.

   Everything here is convenience only: it tells the trainer what their fee
   will be, warns when a slot breaks a society rule, and manages the client
   rows. The server re-validates all of it (gymform/models.py), so a trainer
   with JavaScript disabled still gets a correct, fully validated form.
   ========================================================================= */
(function () {
  "use strict";

  var config = window.GYM_FORM_CONFIG || {};
  var form = document.getElementById("trainerForm");
  if (!form) return;

  var rowsContainer = document.getElementById("clientRows");
  var rowTemplate = document.getElementById("clientRowTemplate");
  var addButton = document.getElementById("addClientBtn");
  var clientCountEl = document.getElementById("clientCount");
  var feeAmountEl = document.getElementById("feeAmount");
  var feeTable = document.getElementById("feeTable");
  var concurrencyWarning = document.getElementById("concurrencyWarning");
  var outsideHoursBlock = document.getElementById("outsideHoursBlock");
  var ackProgress = document.getElementById("ackProgress");
  var submitBtn = document.getElementById("submitBtn");

  // ---------------------------------------------------------------- helpers

  function rows() {
    return Array.prototype.slice.call(rowsContainer.querySelectorAll("[data-row]"));
  }

  function toMinutes(value) {
    var match = /^(\d{1,2}):(\d{2})$/.exec((value || "").trim());
    if (!match) return null;
    var hours = parseInt(match[1], 10);
    var minutes = parseInt(match[2], 10);
    if (hours > 23 || minutes > 59) return null;
    return hours * 60 + minutes;
  }

  function withinOperatingHours(start, end) {
    return (config.operatingWindows || []).some(function (window) {
      var windowStart = toMinutes(window.start);
      var windowEnd = toMinutes(window.end);
      return windowStart !== null && windowEnd !== null &&
             start >= windowStart && end <= windowEnd;
    });
  }

  function feeFor(count) {
    if (count <= 0) return 0;
    var slabs = config.feeSlabs || [];
    for (var i = 0; i < slabs.length; i++) {
      var slab = slabs[i];
      if (count >= slab.min && (slab.max === null || count <= slab.max)) return slab.fee;
    }
    return slabs.length ? slabs[slabs.length - 1].fee : 0;
  }

  function rowIsFilled(row) {
    var fields = row.querySelectorAll('input[name="client_name"], input[name="client_flat"], input[data-time]');
    for (var i = 0; i < fields.length; i++) {
      if (fields[i].value.trim()) return true;
    }
    return row.querySelectorAll('.day-chip input:checked').length > 0;
  }

  /* Day checkboxes are grouped per row by name (client_days_0, client_days_1,
     …), and the server reads them by row index — so the names have to be
     renumbered whenever a row is added or removed. */
  function reindexRows() {
    rows().forEach(function (row, index) {
      var number = row.querySelector("[data-row-number]");
      if (number) number.textContent = String(index + 1);
      row.querySelectorAll('.day-chip input').forEach(function (checkbox) {
        checkbox.name = "client_days_" + index;
      });
      var removeButton = row.querySelector("[data-remove-row]");
      if (removeButton) removeButton.hidden = rows().length === 1;
    });
  }

  // ------------------------------------------------------------ live totals

  function collectSchedules() {
    return rows().filter(rowIsFilled).map(function (row) {
      var days = Array.prototype.slice
        .call(row.querySelectorAll('.day-chip input:checked'))
        .map(function (checkbox) { return checkbox.value; });
      return {
        row: row,
        days: days,
        start: toMinutes(row.querySelector('input[name="client_start"]').value),
        end: toMinutes(row.querySelector('input[name="client_end"]').value)
      };
    });
  }

  function maxConcurrent(schedules) {
    var busiest = 0;
    var allDays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
    allDays.forEach(function (day) {
      var events = [];
      schedules.forEach(function (item) {
        if (item.days.indexOf(day) === -1) return;
        if (item.start === null || item.end === null || item.end <= item.start) return;
        events.push([item.start, 1]);
        events.push([item.end, -1]);
      });
      // A session ending at 07:00 does not overlap one starting at 07:00.
      events.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
      var running = 0;
      events.forEach(function (event) {
        running += event[1];
        if (running > busiest) busiest = running;
      });
    });
    return busiest;
  }

  function refresh() {
    var schedules = collectSchedules();
    var count = schedules.length;

    if (clientCountEl) clientCountEl.textContent = String(count);
    if (feeAmountEl) feeAmountEl.textContent = feeFor(count).toLocaleString("en-IN");

    if (feeTable) {
      Array.prototype.slice.call(feeTable.querySelectorAll("tr[data-min]")).forEach(function (tr) {
        var min = parseInt(tr.getAttribute("data-min"), 10);
        var max = parseInt(tr.getAttribute("data-max"), 10);
        tr.classList.toggle("is-current", count >= min && count <= max);
      });
    }

    // Per-row warnings: end before start, or a slot outside gym hours.
    var anyOutside = false;
    schedules.forEach(function (item) {
      var warning = item.row.querySelector("[data-row-warning]");
      var message = "";
      if (item.start !== null && item.end !== null) {
        if (item.end <= item.start) {
          message = "The end time must be after the start time.";
        } else if (!withinOperatingHours(item.start, item.end)) {
          anyOutside = true;
          message = "This slot is outside gym hours (" + config.operatingHoursText +
                    "). You must inform the office and security — see section 3.";
        }
      }
      if (warning) {
        warning.textContent = message;
        warning.hidden = !message;
      }
    });

    if (outsideHoursBlock) {
      var checkbox = outsideHoursBlock.querySelector('input[name="outside_hours_informed"]');
      // Keep the block visible if it is already ticked, so the answer is never
      // silently discarded by an edit elsewhere on the form.
      outsideHoursBlock.hidden = !anyOutside && !(checkbox && checkbox.checked);
    }

    if (concurrencyWarning) {
      var concurrent = maxConcurrent(schedules);
      var limit = config.maxClientsPerSession || 4;
      if (concurrent > limit) {
        concurrencyWarning.innerHTML =
          "<strong>" + concurrent + " clients are scheduled at the same time.</strong> " +
          "Rules 4 and 14 allow a maximum of " + limit + " clients per session. " +
          "Stagger the slots, or enter the committee's written approval reference above.";
        concurrencyWarning.hidden = false;
      } else {
        concurrencyWarning.hidden = true;
      }
    }
  }

  function refreshAcknowledgements() {
    var boxes = Array.prototype.slice.call(form.querySelectorAll("input[data-ack]"));
    var ticked = boxes.filter(function (box) { return box.checked; }).length;
    if (ackProgress) {
      ackProgress.textContent = ticked === boxes.length
        ? "All " + boxes.length + " rules acknowledged."
        : ticked + " of " + boxes.length + " rules acknowledged.";
    }
  }

  // ------------------------------------------------------------------ rows

  function addRow() {
    // The template ships with a placeholder day-checkbox name; reindexRows
    // replaces it with the row's real index.
    rowsContainer.appendChild(rowTemplate.content.cloneNode(true));
    reindexRows();
    refresh();
    var firstInput = rowsContainer.lastElementChild.querySelector('input[name="client_name"]');
    if (firstInput) firstInput.focus();
  }

  if (addButton) addButton.addEventListener("click", addRow);

  rowsContainer.addEventListener("click", function (event) {
    var button = event.target.closest("[data-remove-row]");
    if (!button) return;
    if (rows().length === 1) return;   // Always keep one row on screen.
    button.closest("[data-row]").remove();
    reindexRows();
    refresh();
  });

  // ----------------------------------------------------------- form events

  form.addEventListener("input", function (event) {
    if (event.target.closest("[data-row]")) refresh();
  });

  form.addEventListener("change", function (event) {
    if (event.target.closest("[data-row]") ||
        event.target.name === "outside_hours_informed") {
      refresh();
    }
    if (event.target.hasAttribute && event.target.hasAttribute("data-ack")) {
      refreshAcknowledgements();
    }
  });

  // Phones on weak connections double-tap Submit; one registration is enough.
  form.addEventListener("submit", function () {
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Submitting…";
      // Re-enable if the browser restores the page from the back/forward cache.
      window.setTimeout(function () {
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit registration";
      }, 15000);
    }
  });

  reindexRows();
  refresh();
  refreshAcknowledgements();

  var errorSummary = document.getElementById("errorSummary");
  if (errorSummary) errorSummary.scrollIntoView({ behavior: "smooth", block: "center" });
})();
