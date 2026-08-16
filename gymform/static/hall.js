/* Amenity booking form — progressive enhancement only.
 *
 * Lives beside gym.js because both forms are served from one static directory.
 * Everything here is a courtesy: the server validates the same rules, and the
 * clash check is re-run under a lock at submit time. Nothing below is a gate —
 * a resident with JavaScript switched off gets the same answers, just later.
 */
(function () {
  "use strict";

  var CONFIG = window.HALL_FORM_CONFIG || {};
  var venues = CONFIG.venues || [];

  var form = document.getElementById("hallForm");
  if (!form) { return; }

  var slotChoices = document.getElementById("slotChoices");
  var startField = document.getElementById("startTimeField");
  var startInput = document.getElementById("start_time");
  var dateInput = document.getElementById("event_date");
  var personsInput = document.getElementById("expected_persons");
  var capacityHint = document.getElementById("capacityHint");
  var endTimeHint = document.getElementById("endTimeHint");
  var chargeBox = document.getElementById("chargeBox");
  var chargeAmount = document.getElementById("chargeAmount");
  var clashNotice = document.getElementById("clashNotice");
  var freeNotice = document.getElementById("freeNotice");
  var ackProgress = document.getElementById("ackProgress");

  function venueByKey(key) {
    for (var i = 0; i < venues.length; i++) {
      if (venues[i].key === key) { return venues[i]; }
    }
    return null;
  }

  function selectedVenue() {
    var checked = form.querySelector('input[name="venue_key"]:checked');
    return checked ? venueByKey(checked.value) : null;
  }

  function selectedSlot() {
    var venue = selectedVenue();
    var checked = form.querySelector('input[name="slot_key"]:checked');
    if (!venue || !checked) { return null; }
    for (var i = 0; i < venue.slots.length; i++) {
      if (venue.slots[i].key === checked.value) { return venue.slots[i]; }
    }
    return null;
  }

  function toMinutes(hhmm) {
    var parts = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
    if (!parts) { return null; }
    return parseInt(parts[1], 10) * 60 + parseInt(parts[2], 10);
  }

  function timeLabel(total) {
    var hour = Math.floor(total / 60), minute = total % 60;
    var suffix = hour < 12 ? "am" : "pm";
    var display = hour % 12 === 0 ? 12 : hour % 12;
    return display + ":" + (minute < 10 ? "0" : "") + minute + " " + suffix;
  }

  function money(value) {
    return value.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // --- Slot choices, redrawn whenever the amenity changes -------------------

  function renderSlots(keepKey) {
    var venue = selectedVenue();
    slotChoices.innerHTML = "";
    if (!venue) {
      slotChoices.innerHTML =
        '<p class="muted small" data-slot-placeholder>Choose an amenity above first.</p>';
      return;
    }
    venue.slots.forEach(function (slot) {
      var label = document.createElement("label");
      label.className = "slot-choice";
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "slot_key";
      input.value = slot.key;
      if (slot.key === keepKey) { input.checked = true; }
      var text = document.createElement("span");
      text.innerHTML =
        "<strong>" + slot.label + "</strong><br>₹" + money(slot.charge);
      label.appendChild(input);
      label.appendChild(text);
      slotChoices.appendChild(label);
    });
  }

  function refreshVenueUI() {
    var venue = selectedVenue();
    var slot = selectedSlot();

    form.querySelectorAll(".venue").forEach(function (node) {
      var input = node.querySelector('input[name="venue_key"]');
      node.classList.toggle("is-selected", !!(input && input.checked));
    });
    slotChoices.querySelectorAll(".slot-choice").forEach(function (node) {
      var input = node.querySelector('input[name="slot_key"]');
      node.classList.toggle("is-selected", !!(input && input.checked));
    });

    if (venue && capacityHint) {
      capacityHint.textContent =
        "The " + venue.name + " allows a maximum of " + venue.maxPersons + " persons.";
      if (personsInput) { personsInput.max = venue.maxPersons; }
    } else if (capacityHint) {
      capacityHint.textContent = "";
    }

    // A full-day booking simply takes the whole permitted window, so asking
    // for a start time would only invite a wrong answer.
    var wholeDay = !!(slot && slot.key === "day");
    if (startField) { startField.hidden = wholeDay; }
    if (wholeDay && endTimeHint) {
      endTimeHint.textContent =
        "Booked for the full day, " + CONFIG.dayStart + " to " + CONFIG.dayEnd + ".";
    }

    if (chargeBox) {
      chargeBox.hidden = !slot;
      if (slot && chargeAmount) { chargeAmount.textContent = money(slot.charge); }
    }
    updateEndHint();
  }

  function updateEndHint() {
    if (!endTimeHint || !startInput) { return; }
    var slot = selectedSlot();
    if (!slot || slot.key === "day") { return; }
    var start = toMinutes(startInput.value);
    if (start === null) {
      endTimeHint.textContent = "";
      return;
    }
    var end = start + slot.hours * 60;
    var latest = toMinutes(CONFIG.dayEnd);
    if (end > latest) {
      endTimeHint.textContent =
        "A " + slot.label.toLowerCase() + " booking must start by " +
        timeLabel(latest - slot.hours * 60) + ".";
    } else {
      endTimeHint.textContent = "Your function would run until " + timeLabel(end) + ".";
    }
  }

  // --- Availability --------------------------------------------------------

  var availabilityTimer = null;

  function hideNotices() {
    if (clashNotice) { clashNotice.hidden = true; }
    if (freeNotice) { freeNotice.hidden = true; }
  }

  function checkAvailability() {
    var venue = selectedVenue();
    var slot = selectedSlot();
    var date = dateInput ? dateInput.value : "";
    if (!venue || !slot || !date) { hideNotices(); return; }
    if (slot.key !== "day" && !(startInput && startInput.value)) {
      hideNotices();
      return;
    }

    var query =
      "?venue_key=" + encodeURIComponent(venue.key) +
      "&slot_key=" + encodeURIComponent(slot.key) +
      "&event_date=" + encodeURIComponent(date) +
      "&start_time=" + encodeURIComponent(startInput ? startInput.value : "");

    fetch((CONFIG.base || "") + "/availability" + query, {
      headers: { "Accept": "application/json" }
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.checked) { hideNotices(); return; }
        if (data.available) {
          if (clashNotice) { clashNotice.hidden = true; }
          if (freeNotice) {
            freeNotice.textContent = venue.name + " is free then. Nobody else has booked it.";
            freeNotice.hidden = false;
          }
        } else {
          if (freeNotice) { freeNotice.hidden = true; }
          if (clashNotice) {
            clashNotice.textContent = data.message || "That slot is already taken.";
            clashNotice.hidden = false;
          }
        }
      })
      .catch(function () {
        // Offline, or the check failed. Say nothing rather than something
        // wrong — the server checks again on submit either way.
        hideNotices();
      });
  }

  function scheduleAvailabilityCheck() {
    window.clearTimeout(availabilityTimer);
    availabilityTimer = window.setTimeout(checkAvailability, 350);
  }

  // --- Acknowledgement counter ---------------------------------------------

  function updateAckProgress() {
    if (!ackProgress) { return; }
    var boxes = form.querySelectorAll("input[data-ack]");
    var ticked = form.querySelectorAll("input[data-ack]:checked").length;
    ackProgress.textContent =
      ticked + " of " + boxes.length + " acknowledgements accepted" +
      (ticked === boxes.length ? " — all done." : ".");
  }

  // --- Wiring --------------------------------------------------------------

  form.addEventListener("change", function (event) {
    var name = event.target.name;
    if (name === "venue_key") {
      renderSlots(null);
      refreshVenueUI();
      hideNotices();
    } else if (name === "slot_key") {
      refreshVenueUI();
      scheduleAvailabilityCheck();
    } else if (name === "event_date" || name === "start_time") {
      updateEndHint();
      scheduleAvailabilityCheck();
    }
    if (event.target.hasAttribute && event.target.hasAttribute("data-ack")) {
      updateAckProgress();
    }
  });

  if (startInput) { startInput.addEventListener("input", updateEndHint); }

  // Today is never bookable: the charges are collected the day before.
  if (dateInput && !dateInput.min) {
    var tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    dateInput.min = tomorrow.toISOString().slice(0, 10);
  }

  renderSlots(CONFIG.selectedSlot || null);
  refreshVenueUI();
  updateAckProgress();
  if (CONFIG.selectedVenue) { scheduleAvailabilityCheck(); }
})();
