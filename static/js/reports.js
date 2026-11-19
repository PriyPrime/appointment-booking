/** Admin reports: a date range, five totals and three breakdown tables. */

import { request } from "./api.js";
import { getUser } from "./auth.js";
import {
  $,
  addDays,
  formatDate,
  formatMoney,
  make,
  parseDateOnly,
  setBusy,
  setMessage,
  toDateInputValue,
  todayInputValue,
} from "./ui.js";

const els = {};

export function initReports() {
  els.form = $("#report-form");
  els.from = $("#report-from");
  els.to = $("#report-to");
  els.export = $("#report-export");
  els.summary = $("#report-summary");
  els.tables = $("#report-tables");

  setDefaultRange();
  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadReport();
  });
}

export function resetReports() {
  setDefaultRange();
  els.summary.replaceChildren(
    make("p", { className: "muted", text: "Choose a range and run the report." }),
  );
  els.tables.replaceChildren();
  els.export.href = "#";
}

function setDefaultRange() {
  const today = parseDateOnly(todayInputValue());
  els.from.value = toDateInputValue(addDays(today, -29));
  els.to.value = todayInputValue();
}

export async function loadReport() {
  const user = getUser();
  if (!user || user.role !== "admin") {
    setMessage("Reports are available to administrators.", "error");
    return;
  }

  const from = els.from.value;
  const to = els.to.value;
  if (!from || !to) {
    setMessage("Choose both a start and an end date.", "error");
    return;
  }
  if (from > to) {
    setMessage("The start date must be on or before the end date.", "error");
    return;
  }

  const button = els.form.querySelector("button[type='submit']");
  setBusy(button, true, "Running…");
  try {
    const query = new URLSearchParams({ from, to });
    const data = await request(`/api/reports/summary?${query}`);
    renderReport(data);
    els.export.href = `/api/reports/export?${query}`;
    setMessage("");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function renderReport(data) {
  const totals = data.totals;

  els.summary.replaceChildren(
    card("Appointments", String(totals.appointments)),
    card("Completed", String(totals.completed)),
    card("Revenue", formatMoney(totals.revenue)),
    card("No-show rate", `${(totals.no_show_rate * 100).toFixed(1)}%`),
    card("Customers", String(totals.unique_customers)),
  );

  els.tables.replaceChildren(
    tableCard(
      "By service",
      ["Service", "Appointments", "Completed", "No-shows", "Revenue"],
      data.by_service,
      (row) => [
        row.service_name,
        row.appointments,
        row.completed,
        row.no_show,
        formatMoney(row.revenue),
      ],
    ),
    tableCard(
      "By provider",
      ["Provider", "Appointments", "Completed", "No-shows", "Revenue"],
      data.by_provider,
      (row) => [
        row.provider_name,
        row.appointments,
        row.completed,
        row.no_show,
        formatMoney(row.revenue),
      ],
    ),
    tableCard(
      "By day",
      ["Date", "Appointments", "Completed", "No-shows", "Revenue"],
      data.by_day,
      (row) => [
        formatDate(row.date),
        row.appointments,
        row.completed,
        row.no_show,
        formatMoney(row.revenue),
      ],
    ),
  );
}

function card(label, value) {
  return make("div", { className: "report-card" }, [
    make("span", { text: label }),
    make("strong", { text: value }),
  ]);
}

function tableCard(title, headers, rows, cells) {
  const head = make("tr", {}, headers.map((text) => make("th", { text })));
  const body = make("tbody");

  if (!rows.length) {
    body.append(
      make("tr", {}, [
        make("td", {
          className: "muted",
          text: "No appointments in this range.",
          attrs: { colspan: headers.length },
        }),
      ]),
    );
  } else {
    for (const row of rows) {
      body.append(
        make(
          "tr",
          {},
          cells(row).map((value) => make("td", { text: String(value) })),
        ),
      );
    }
  }

  return make("div", { className: "card panel-card report-table" }, [
    make("h3", { text: title }),
    make("div", { className: "table-wrap" }, [
      make("table", {}, [make("thead", {}, [head]), body]),
    ]),
  ]);
}
