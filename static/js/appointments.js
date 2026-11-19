/**
 * Appointment history, cancellation and rescheduling.
 *
 * Cutoff checks here are only a courtesy: the server re-checks the 2-hour
 * rule and returns 409 if the browser is wrong or the clock has moved.
 */

import { request } from "./api.js";
import { getUser, isLoggedIn } from "./auth.js";
import {
  fetchSlots,
  findNextAvailable,
  renderSlotButtons,
} from "./slots.js";
import {
  $,
  addDays,
  formatDate,
  formatTime,
  isCustomerTooLate,
  make,
  maxDateInputValue,
  parseDateOnly,
  setBusy,
  setMessage,
  startDateTime,
  statusLabel,
  toDateInputValue,
  todayInputValue,
} from "./ui.js";

let appointments = [];
let loadToken = 0;
let rescheduleTarget = null;
let rescheduleDate = "";
let rescheduleSlot = null;
let rescheduleToken = 0;

const els = {};

export function initAppointments() {
  els.list = $("#appointments-list");
  els.refresh = $("#refresh-appointments");
  els.dialog = $("#reschedule-dialog");
  els.summary = $("#reschedule-summary");
  els.date = $("#reschedule-date");
  els.slots = $("#reschedule-slots");
  els.form = $("#reschedule-form");
  els.close = $("#reschedule-close");
  els.cancel = $("#reschedule-cancel");
  els.confirm = $("#reschedule-confirm");

  els.date.min = todayInputValue();
  els.date.max = maxDateInputValue();

  els.refresh.addEventListener("click", async () => {
    setBusy(els.refresh, true, "Refreshing…");
    await loadAppointments();
    setBusy(els.refresh, false);
  });

  els.close.addEventListener("click", closeDialog);
  els.cancel.addEventListener("click", closeDialog);
  els.dialog.addEventListener("close", resetDialog);
  els.form.addEventListener("submit", confirmReschedule);
  els.date.addEventListener("change", () => {
    rescheduleDate = els.date.value;
    rescheduleSlot = null;
    fetchRescheduleSlots();
  });
}

export async function loadAppointments() {
  if (!isLoggedIn()) {
    clearAppointments();
    return;
  }

  const token = ++loadToken;
  els.list.replaceChildren(
    make("p", { className: "muted", text: "Loading appointments…" }),
  );

  try {
    const rows = await request("/api/appointments");
    if (token !== loadToken) return;
    appointments = rows;
  } catch (error) {
    if (token !== loadToken) return;
    els.list.replaceChildren(
      make("p", { className: "empty-state", text: error.message }),
    );
    return;
  }

  renderAppointments();
}

export function clearAppointments() {
  appointments = [];
  loadToken += 1;
  els.list.replaceChildren(
    make("p", {
      className: "empty-state",
      text: "Sign in to see and manage your appointments.",
    }),
  );
}

function renderAppointments() {
  if (!appointments.length) {
    els.list.replaceChildren(
      make("p", {
        className: "empty-state",
        text: "No appointments yet. Choose a service above to book one.",
      }),
    );
    return;
  }

  const now = Date.now();
  const upcoming = [];
  const history = [];

  for (const appointment of appointments) {
    const start = startDateTime(appointment);
    if (appointment.status === "booked" && start && start.getTime() >= now) {
      upcoming.push(appointment);
    } else {
      history.push(appointment);
    }
  }

  upcoming.sort((a, b) => startDateTime(a) - startDateTime(b));
  history.sort((a, b) => startDateTime(b) - startDateTime(a));

  const groups = [];
  if (upcoming.length) groups.push(renderGroup("Upcoming", upcoming));
  if (history.length) groups.push(renderGroup("History", history));
  els.list.replaceChildren(...groups);
}

function renderGroup(title, rows) {
  const list = make("div", { className: "appointment-list" });
  for (const appointment of rows) {
    list.append(renderAppointment(appointment));
  }
  return make("section", { className: "appointment-group" }, [
    make("h2", { text: title }),
    list,
  ]);
}

function renderAppointment(appointment) {
  const user = getUser();
  const isCustomer = !user || user.role === "customer";
  const tooLate = isCustomer && isCustomerTooLate(appointment);

  const meta = make("div", { className: "appointment-meta" }, [
    make("span", {
      text: `${formatDate(appointment.date)} · ${formatTime(
        appointment.start_time,
      )}–${formatTime(appointment.end_time)}`,
    }),
    make("span", { text: `With ${appointment.provider_name}` }),
    make("span", { text: `#${appointment.id}` }),
  ]);

  if (user && user.role !== "customer") {
    meta.append(make("span", { text: `For ${appointment.customer_name}` }));
  }

  const body = make("div", {}, [
    make("div", { className: "appointment-top" }, [
      make("h3", { text: appointment.service_name }),
      make("span", {
        className: `badge ${appointment.status}`,
        text: statusLabel(appointment.status),
      }),
    ]),
    meta,
  ]);

  if (appointment.notes) {
    body.append(
      make("p", {
        className: "appointment-notes",
        text: `Notes: ${appointment.notes}`,
      }),
    );
  }

  const actions = make("div", { className: "appointment-actions" });
  if (appointment.status === "booked") {
    if (tooLate) {
      actions.append(
        make("span", {
          className: "cutoff-note",
          text: "Changes close 2 hours before the start.",
        }),
      );
    } else {
      const reschedule = make("button", {
        className: "button ghost",
        text: "Reschedule",
        attrs: { type: "button" },
      });
      reschedule.addEventListener("click", () =>
        openReschedule(appointment, reschedule),
      );

      const cancel = make("button", {
        className: "button danger",
        text: "Cancel",
        attrs: { type: "button" },
      });
      cancel.addEventListener("click", () =>
        cancelAppointment(appointment, cancel),
      );

      actions.append(reschedule, cancel);
    }

    // A visit can only be marked after it was due to start. Providers and
    // admins see these; customers never do.
    const start = startDateTime(appointment);
    if (!isCustomer && start && start.getTime() <= Date.now()) {
      const complete = make("button", {
        className: "button ghost",
        text: "Complete",
        attrs: { type: "button" },
      });
      complete.addEventListener("click", () =>
        changeStatus(appointment, "completed", complete),
      );

      const noShow = make("button", {
        className: "button danger",
        text: "No-show",
        attrs: { type: "button" },
      });
      noShow.addEventListener("click", () =>
        changeStatus(appointment, "no_show", noShow),
      );

      actions.append(complete, noShow);
    }
  }

  return make(
    "article",
    { className: `card appointment-card status-${appointment.status}` },
    [body, actions],
  );
}

async function cancelAppointment(appointment, button) {
  const when = `${formatDate(appointment.date)} at ${formatTime(
    appointment.start_time,
  )}`;
  if (!window.confirm(`Cancel ${appointment.service_name} on ${when}?`)) {
    return;
  }

  setBusy(button, true, "Cancelling…");
  try {
    await request(`/api/appointments/${appointment.id}/cancel`, {
      method: "PATCH",
    });
    setMessage("Appointment cancelled.", "success");
    await loadAppointments();
  } catch (error) {
    setMessage(error.message, "error");
    // The row may have changed underneath us; re-read the true state.
    await loadAppointments();
  } finally {
    setBusy(button, false);
  }
}

async function changeStatus(appointment, status, button) {
  if (status === "no_show") {
    const when = `${formatDate(appointment.date)} at ${formatTime(
      appointment.start_time,
    )}`;
    if (!window.confirm(`Mark ${appointment.service_name} on ${when} as a no-show?`)) {
      return;
    }
  }

  setBusy(button, true, "Saving…");
  try {
    await request(`/api/appointments/${appointment.id}/status`, {
      method: "PATCH",
      body: { status },
    });
    setMessage(
      `Marked ${appointment.service_name} as ${statusLabel(status).toLowerCase()}.`,
      "success",
    );
    await loadAppointments();
  } catch (error) {
    setMessage(error.message, "error");
    await loadAppointments();
  } finally {
    setBusy(button, false);
  }
}

function openReschedule(appointment, trigger) {
  rescheduleTarget = appointment;
  rescheduleSlot = null;
  rescheduleToken += 1;

  const today = parseDateOnly(todayInputValue());
  const current = parseDateOnly(appointment.date);
  rescheduleDate = current && current >= today ? appointment.date : todayInputValue();

  els.summary.textContent =
    `${appointment.service_name} with ${appointment.provider_name}. ` +
    `Currently ${formatDate(appointment.date)} at ${formatTime(
      appointment.start_time,
    )}.`;
  els.date.value = rescheduleDate;
  els.confirm.disabled = true;
  els.dialog.showModal();
  fetchRescheduleSlots();
  trigger.blur();
}

function resetDialog() {
  rescheduleTarget = null;
  rescheduleSlot = null;
  rescheduleToken += 1;
  els.slots.replaceChildren(
    make("p", { className: "muted", text: "Pick a date to see available times." }),
  );
}

function closeDialog() {
  els.dialog.close();
}

async function fetchRescheduleSlots() {
  if (!rescheduleTarget || !rescheduleDate) return;

  const token = ++rescheduleToken;
  rescheduleSlot = null;
  els.slots.replaceChildren(
    make("p", { className: "muted", text: "Checking available times…" }),
  );
  els.confirm.disabled = true;

  let slots;
  try {
    slots = await fetchSlots(
      rescheduleTarget.provider_id,
      rescheduleTarget.service_id,
      rescheduleDate,
    );
  } catch (error) {
    if (token !== rescheduleToken) return;
    els.slots.replaceChildren(
      make("p", { className: "empty-state", text: error.message }),
    );
    return;
  }

  if (token !== rescheduleToken) return;

  if (!slots.length) {
    els.slots.replaceChildren(
      make("p", {
        className: "empty-state",
        text: `No times are available on ${formatDate(rescheduleDate)}.`,
      }),
    );
    const nextButton = make("button", {
      className: "button ghost",
      text: "Find next available day",
      attrs: { type: "button" },
    });
    nextButton.addEventListener("click", () => findRescheduleDay(nextButton));
    els.slots.append(nextButton);
    return;
  }

  const grid = renderSlotButtons(slots, rescheduleSlot, (value) => {
    rescheduleSlot = value;
    for (const button of grid.children) {
      const active = button.textContent === rescheduleSlot;
      button.classList.toggle("is-selected", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
    els.confirm.disabled = false;
  });
  els.slots.replaceChildren(grid);
}

async function findRescheduleDay(button) {
  if (!rescheduleTarget) return;
  setBusy(button, true, "Searching…");

  try {
    const found = await findNextAvailable(
      rescheduleTarget.provider_id,
      rescheduleTarget.service_id,
      toDateInputValue(addDays(parseDateOnly(rescheduleDate), 1)),
    );
    if (!found) {
      setMessage("No open times in the next two weeks. Try a later date.", "info");
      return;
    }
    rescheduleDate = found.date;
    els.date.value = found.date;
    rescheduleSlot = null;
    await fetchRescheduleSlots();
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function confirmReschedule(event) {
  event.preventDefault();
  if (!rescheduleTarget || !rescheduleSlot) return;

  const target = rescheduleTarget;
  const newDate = rescheduleDate;
  const newSlot = rescheduleSlot;
  setBusy(els.confirm, true, "Moving…");

  try {
    await request(`/api/appointments/${target.id}/reschedule`, {
      method: "PATCH",
      body: { date: newDate, start_time: newSlot },
    });
    closeDialog();
    setMessage(
      `Moved to ${formatDate(newDate)} at ${formatTime(newSlot)}.`,
      "success",
    );
    await loadAppointments();
  } catch (error) {
    setMessage(error.message, "error");
    if (error.status === 409) {
      await fetchRescheduleSlots();
    }
  } finally {
    setBusy(els.confirm, false);
    els.confirm.disabled = !rescheduleSlot;
  }
}
