/**
 * Small DOM and formatting helpers shared by the page modules.
 * Everything user-supplied is rendered with textContent, never innerHTML.
 */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Mirrors the server's CANCEL_CUTOFF_HOURS. The server remains authoritative;
// this value only decides whether to pre-emptively disable a button.
const CANCEL_CUTOFF_HOURS = 2;
const BOOKING_HORIZON_DAYS = 60;

export function make(tag, { className = "", text, attrs = {} } = {}, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;

  for (const [name, value] of Object.entries(attrs)) {
    if (name.startsWith("on") && typeof value === "function") {
      node.addEventListener(name.slice(2).toLowerCase(), value);
    } else if (name === "hidden") {
      node.hidden = Boolean(value);
    } else if (value !== null && value !== undefined) {
      node.setAttribute(name, String(value));
    }
  }

  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

export function setMessage(text, kind = "info") {
  const box = $("#message");
  box.textContent = text || "";
  box.className = `message ${kind}`;
  box.hidden = !text;
}

export function clearMessage() {
  setMessage("");
}

export function showView(name) {
  for (const view of $$(".view")) {
    view.hidden = view.id !== `${name}-view`;
  }
  for (const button of $$(".nav-button")) {
    const active = button.dataset.view === name;
    button.classList.toggle("is-active", active);
    if (active) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

export function setBusy(button, busy, busyText = "Working…") {
  if (busy) {
    button.dataset.idleLabel = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    if (button.dataset.idleLabel) {
      button.textContent = button.dataset.idleLabel;
      delete button.dataset.idleLabel;
    }
    button.disabled = false;
  }
}

/* ---------- Dates and times ---------- */

export function parseDateOnly(value) {
  if (typeof value !== "string") return null;
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return null;
  const date = new Date(year, month - 1, day);
  // Reject impossible dates such as 2026-02-30.
  if (
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) {
    return null;
  }
  return date;
}

export function formatDate(value) {
  const date = parseDateOnly(value);
  if (!date) return value;
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

export function formatTime(value) {
  if (typeof value !== "string" || !value.includes(":")) return value;
  const [hours, minutes] = value.split(":").map(Number);
  const date = new Date(2000, 0, 1, hours, minutes);
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function startDateTime(appointment) {
  const date = parseDateOnly(appointment.date);
  if (!date) return null;
  const [hours, minutes] = appointment.start_time.split(":").map(Number);
  date.setHours(hours, minutes, 0, 0);
  return date;
}

export function isCustomerTooLate(appointment) {
  const start = startDateTime(appointment);
  if (!start) return true;
  return Date.now() > start.getTime() - CANCEL_CUTOFF_HOURS * 60 * 60 * 1000;
}

export function toDateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function todayInputValue() {
  return toDateInputValue(new Date());
}

export function addDays(date, days) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

export function maxDateInputValue() {
  return toDateInputValue(addDays(new Date(), BOOKING_HORIZON_DAYS));
}

export function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
  }).format(number);
}

export function statusLabel(status) {
  return {
    booked: "Booked",
    completed: "Completed",
    cancelled: "Cancelled",
    no_show: "No show",
  }[status] || status;
}
