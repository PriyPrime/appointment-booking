/**
 * Staff screens: services, providers, weekly availability and time off.
 *
 * The admin API already enforces every rule. These forms exist to make those
 * endpoints usable; they never decide whether a save is allowed.
 */

import { request } from "./api.js";
import { getUser } from "./auth.js";
import { loadReport, initReports, resetReports } from "./reports.js";
import {
  $,
  $$,
  formatMoney,
  make,
  setBusy,
  setMessage,
  todayInputValue,
} from "./ui.js";

const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

let services = [];
let providers = [];
let editingServiceId = null;
let editingProviderId = null;
let availabilityProviderId = null;
let availabilityWindows = [];
let timeOff = [];
let staffDataPromise = null;

const els = {};
let navigate = () => {};

export function initStaff(options = {}) {
  navigate = options.navigate || (() => {});

  els.serviceList = $("#service-admin-list");
  els.serviceForm = $("#service-form");
  els.serviceId = $("#service-id");
  els.serviceName = $("#service-name");
  els.serviceDescription = $("#service-description");
  els.serviceDuration = $("#service-duration");
  els.servicePrice = $("#service-price");
  els.serviceActive = $("#service-active");
  els.serviceTitle = $("#service-form-title");
  els.serviceSubmit = $("#service-submit");
  els.serviceReset = $("#service-reset");

  els.providerList = $("#provider-admin-list");
  els.providerForm = $("#provider-form");
  els.providerId = $("#provider-id");
  els.providerName = $("#provider-name");
  els.providerEmail = $("#provider-email");
  els.providerPhone = $("#provider-phone");
  els.providerPassword = $("#provider-password");
  els.providerSpecialization = $("#provider-specialization");
  els.providerActive = $("#provider-active");
  els.providerActiveField = $("#provider-active-field");
  els.providerCreateFields = $("#provider-create-fields");
  els.providerIdentity = $("#provider-identity");
  els.providerServices = $("#provider-services");
  els.providerTitle = $("#provider-form-title");
  els.providerSubmit = $("#provider-submit");
  els.providerReset = $("#provider-reset");

  els.windows = $("#availability-windows");
  els.providerField = $("#availability-provider-field");
  els.providerSelect = $("#availability-provider");
  els.availabilityNote = $("#availability-note");
  els.timeOffForm = $("#time-off-form");
  els.timeOffDate = $("#time-off-date");
  els.timeOffReason = $("#time-off-reason");
  els.timeOffList = $("#time-off-list");

  wireTabs();
  wireServices();
  wireProviders();
  wireAvailability();

  els.timeOffDate.min = todayInputValue();
  initReports();
  showAdminTab("services");
}

export function onAuthChanged() {
  const user = getUser();
  resetReports();

  if (!user || user.role !== "admin") {
    services = [];
    providers = [];
    availabilityProviderId = null;
    availabilityWindows = [];
    timeOff = [];
    renderEmpty(els.serviceList, "Administrators only.");
    renderEmpty(els.providerList, "Administrators only.");
    resetServiceForm();
    resetProviderForm();
  } else {
    ensureStaffData();
  }

  syncAvailabilityOptions();
}

function wireTabs() {
  for (const tab of $$("[data-admin-tab]")) {
    tab.addEventListener("click", () => showAdminTab(tab.dataset.adminTab));
  }

  $("#refresh-manage").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    setBusy(button, true, "Refreshing…");
    try {
      staffDataPromise = null;
      await ensureStaffData();
      if (!$("#availability-view").hidden) {
        await loadAvailability();
      }
      setMessage("Staff data refreshed.", "info");
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  });
}

export function showAdminTab(name) {
  for (const tab of $$("[data-admin-tab]")) {
    const active = tab.dataset.adminTab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const panel of $$("[data-admin-panel]")) {
    panel.hidden = panel.dataset.adminPanel !== name;
  }
  if (name === "reports") {
    loadReport();
  }
}

/* ---------- Shared loading ---------- */

function ensureStaffData() {
  if (staffDataPromise === null) {
    staffDataPromise = Promise.all([loadServices(), loadProviders()]).finally(() => {
      staffDataPromise = null;
    });
  }
  return staffDataPromise;
}

async function loadServices() {
  try {
    services = await request("/api/services?include_inactive=1");
  } catch (error) {
    renderEmpty(els.serviceList, error.message);
    return;
  }
  renderServices();
  renderServiceCheckboxes();
}

async function loadProviders() {
  try {
    providers = await request("/api/providers?include_inactive=1");
  } catch (error) {
    renderEmpty(els.providerList, error.message);
    return;
  }
  renderProviders();
  syncAvailabilityOptions();
}

function renderEmpty(container, message) {
  container.replaceChildren(make("p", { className: "muted", text: message }));
}

function serviceName(serviceId) {
  return services.find((service) => service.id === serviceId)?.name || `#${serviceId}`;
}

/* ---------- Services ---------- */

function wireServices() {
  els.serviceForm.addEventListener("submit", saveService);
  els.serviceReset.addEventListener("click", resetServiceForm);
}

function renderServices() {
  if (!services.length) {
    renderEmpty(els.serviceList, "No services yet. Create the first one.");
    return;
  }

  const head = make("tr", {}, [
    make("th", { text: "Service" }),
    make("th", { text: "Duration" }),
    make("th", { text: "Price" }),
    make("th", { text: "Status" }),
    make("th", { text: "" }),
  ]);

  const body = make("tbody");
  for (const service of services) {
    const edit = make("button", {
      className: "button ghost",
      text: "Edit",
      attrs: { type: "button" },
    });
    edit.addEventListener("click", () => editService(service));

    body.append(
      make("tr", {}, [
        make("td", {}, [
          make("strong", { text: service.name }),
          service.description
            ? make("div", { className: "muted", text: service.description })
            : null,
        ]),
        make("td", { text: `${service.duration_min} min` }),
        make("td", { text: formatMoney(service.price) }),
        make("td", {}, [
          make("span", {
            className: `badge ${service.active ? "booked" : "inactive"}`,
            text: service.active ? "Active" : "Inactive",
          }),
        ]),
        make("td", {}, [make("div", { className: "table-actions" }, [edit])]),
      ]),
    );
  }

  els.serviceList.replaceChildren(
    make("table", {}, [make("thead", {}, [head]), body]),
  );
}

function editService(service) {
  editingServiceId = service.id;
  els.serviceTitle.textContent = "Edit service";
  els.serviceSubmit.textContent = "Save changes";
  els.serviceReset.hidden = false;
  els.serviceId.value = service.id;
  els.serviceName.value = service.name;
  els.serviceDescription.value = service.description || "";
  els.serviceDuration.value = service.duration_min;
  els.servicePrice.value = service.price;
  els.serviceActive.checked = service.active;
  els.serviceName.focus();
}

function resetServiceForm() {
  editingServiceId = null;
  els.serviceForm.reset();
  els.serviceId.value = "";
  els.serviceActive.checked = true;
  els.serviceTitle.textContent = "New service";
  els.serviceSubmit.textContent = "Save service";
  els.serviceReset.hidden = true;
}

async function saveService(event) {
  event.preventDefault();
  setMessage("");

  const body = {
    name: els.serviceName.value.trim(),
    description: els.serviceDescription.value.trim() || null,
    duration_min: Number(els.serviceDuration.value),
    price: Number(els.servicePrice.value),
    active: els.serviceActive.checked,
  };

  const button = els.serviceSubmit;
  setBusy(button, true, "Saving…");
  try {
    await request(
      editingServiceId ? `/api/services/${editingServiceId}` : "/api/services",
      { method: editingServiceId ? "PUT" : "POST", body },
    );
    setMessage(
      editingServiceId ? "Service updated." : "Service created.",
      "success",
    );
    // Restore the button label before resetting the wording it should show.
    setBusy(button, false);
    resetServiceForm();
    await loadServices();
    // Provider checkboxes show service names, so refresh that table too.
    await loadProviders();
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

/* ---------- Providers ---------- */

function wireProviders() {
  els.providerForm.addEventListener("submit", saveProvider);
  els.providerReset.addEventListener("click", resetProviderForm);
}

function renderProviders() {
  if (!providers.length) {
    renderEmpty(els.providerList, "No providers yet. Create the first one.");
    return;
  }

  const head = make("tr", {}, [
    make("th", { text: "Provider" }),
    make("th", { text: "Services" }),
    make("th", { text: "Status" }),
    make("th", { text: "" }),
  ]);

  const body = make("tbody");
  for (const provider of providers) {
    const edit = make("button", {
      className: "button ghost",
      text: "Edit",
      attrs: { type: "button" },
    });
    edit.addEventListener("click", () => editProvider(provider));

    const hours = make("button", {
      className: "button ghost",
      text: "Hours",
      attrs: { type: "button" },
    });
    hours.addEventListener("click", () =>
      navigate("availability", { providerId: provider.id }),
    );

    body.append(
      make("tr", {}, [
        make("td", {}, [
          make("strong", { text: provider.name }),
          make("div", { className: "muted", text: provider.email || "" }),
          provider.specialization
            ? make("div", { className: "muted", text: provider.specialization })
            : null,
        ]),
        make("td", {
          text:
            provider.service_ids.map(serviceName).join(", ") || "No services",
        }),
        make("td", {}, [
          make("span", {
            className: `badge ${provider.active ? "booked" : "inactive"}`,
            text: provider.active ? "Active" : "Inactive",
          }),
        ]),
        make(
          "td",
          {},
          [make("div", { className: "table-actions" }, [edit, hours])],
        ),
      ]),
    );
  }

  els.providerList.replaceChildren(
    make("table", {}, [make("thead", {}, [head]), body]),
  );
}

function renderServiceCheckboxes(selected = null) {
  // Re-rendering must not wipe what the admin already ticked.
  const checked =
    selected ||
    new Set(
      $$("#provider-services input:checked").map((input) => Number(input.value)),
    );

  els.providerServices.replaceChildren(
    make("legend", { text: "Services offered" }),
  );

  if (!services.length) {
    els.providerServices.append(
      make("p", { className: "muted", text: "Create a service first." }),
    );
    return;
  }

  for (const service of services) {
    const input = make("input", {
      attrs: { type: "checkbox", value: service.id },
    });
    input.checked = checked.has(service.id);
    els.providerServices.append(
      make("label", {}, [
        input,
        make("span", {
          text: `${service.name}${service.active ? "" : " (inactive)"}`,
        }),
      ]),
    );
  }
}

function editProvider(provider) {
  editingProviderId = provider.id;
  els.providerTitle.textContent = "Edit provider";
  els.providerSubmit.textContent = "Save changes";
  els.providerReset.hidden = false;
  els.providerId.value = provider.id;
  els.providerCreateFields.hidden = true;
  els.providerIdentity.hidden = false;
  els.providerIdentity.textContent = `${provider.name} · ${provider.email}`;
  els.providerSpecialization.value = provider.specialization || "";
  els.providerActiveField.hidden = false;
  els.providerActive.checked = provider.active;
  for (const input of [
    els.providerName,
    els.providerEmail,
    els.providerPassword,
  ]) {
    input.required = false;
  }
  renderServiceCheckboxes(new Set(provider.service_ids));
}

function resetProviderForm() {
  editingProviderId = null;
  els.providerForm.reset();
  els.providerId.value = "";
  els.providerTitle.textContent = "New provider";
  els.providerSubmit.textContent = "Create provider";
  els.providerReset.hidden = true;
  els.providerCreateFields.hidden = false;
  els.providerIdentity.hidden = true;
  els.providerIdentity.textContent = "";
  els.providerActiveField.hidden = true;
  els.providerActive.checked = true;
  for (const input of [
    els.providerName,
    els.providerEmail,
    els.providerPassword,
  ]) {
    input.required = true;
  }
  renderServiceCheckboxes(new Set());
}

async function saveProvider(event) {
  event.preventDefault();
  setMessage("");

  const serviceIds = $$("#provider-services input:checked").map((input) =>
    Number(input.value),
  );
  const specialization = els.providerSpecialization.value.trim() || null;

  let body;
  if (editingProviderId) {
    body = {
      specialization,
      active: els.providerActive.checked,
      service_ids: serviceIds,
    };
  } else {
    body = {
      name: els.providerName.value.trim(),
      email: els.providerEmail.value.trim(),
      phone: els.providerPhone.value.trim() || null,
      password: els.providerPassword.value,
      specialization,
      service_ids: serviceIds,
    };
  }

  const button = els.providerSubmit;
  setBusy(button, true, "Saving…");
  try {
    await request(
      editingProviderId ? `/api/providers/${editingProviderId}` : "/api/providers",
      { method: editingProviderId ? "PUT" : "POST", body },
    );
    setMessage(
      editingProviderId ? "Provider updated." : "Provider created.",
      "success",
    );
    setBusy(button, false);
    resetProviderForm();
    await loadProviders();
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

/* ---------- Availability ---------- */

function wireAvailability() {
  $("#add-window").addEventListener("click", () => {
    addWindowRow({ weekday: 0, start_time: "09:00", end_time: "17:00" });
  });
  $("#save-windows").addEventListener("click", saveWindows);
  els.providerSelect.addEventListener("change", () => {
    availabilityProviderId = Number(els.providerSelect.value);
    loadAvailability();
  });
  els.timeOffForm.addEventListener("submit", addTimeOff);
}

export async function openAvailability(providerId = null) {
  const user = getUser();
  if (!user || (user.role !== "admin" && user.role !== "provider")) {
    return;
  }

  if (user.role === "provider") {
    els.providerField.hidden = true;
    if (!user.provider_id) {
      renderEmpty(els.windows, "No provider profile is linked to this account.");
      return;
    }
    availabilityProviderId = user.provider_id;
    els.availabilityNote.textContent = `Your weekly hours, ${user.name}.`;
    await loadAvailability();
    return;
  }

  els.providerField.hidden = false;
  await ensureStaffData();
  syncAvailabilityOptions();

  if (providerId !== null && providerId !== undefined) {
    availabilityProviderId = Number(providerId);
  }
  if (
    !availabilityProviderId ||
    !providers.some((provider) => provider.id === availabilityProviderId)
  ) {
    availabilityProviderId = providers[0]?.id || null;
  }

  if (!availabilityProviderId) {
    renderEmpty(els.windows, "Create a provider before setting hours.");
    els.timeOffList.replaceChildren(
      make("p", { className: "muted", text: "No providers yet." }),
    );
    els.availabilityNote.textContent = "";
    return;
  }

  els.providerSelect.value = String(availabilityProviderId);
  const provider = providers.find((row) => row.id === availabilityProviderId);
  els.availabilityNote.textContent = provider
    ? `Weekly hours for ${provider.name}.`
    : "";
  await loadAvailability();
}

function syncAvailabilityOptions() {
  const user = getUser();
  if (!user || user.role !== "admin") {
    return;
  }

  els.providerField.hidden = false;
  els.providerSelect.replaceChildren(
    ...providers.map((provider) =>
      make("option", {
        text: `${provider.name}${provider.active ? "" : " (inactive)"}`,
        attrs: { value: provider.id },
      }),
    ),
  );

  if (
    availabilityProviderId &&
    providers.some((provider) => provider.id === availabilityProviderId)
  ) {
    els.providerSelect.value = String(availabilityProviderId);
  }
}

async function loadAvailability() {
  if (!availabilityProviderId) {
    return;
  }

  renderEmpty(els.windows, "Loading hours…");
  try {
    const data = await request(
      `/api/providers/${availabilityProviderId}/availability`,
    );
    availabilityWindows = data.windows || [];
    timeOff = data.time_off || [];
  } catch (error) {
    renderEmpty(els.windows, error.message);
    return;
  }

  renderWindows();
  renderTimeOff();
}

function renderWindows() {
  els.windows.replaceChildren();
  if (!availabilityWindows.length) {
    els.windows.append(
      make("p", {
        className: "muted",
        text: "No weekly hours. Add at least one window or this provider cannot be booked.",
      }),
    );
    return;
  }
  for (const window of availabilityWindows) {
    addWindowRow(window);
  }
}

function addWindowRow(window) {
  const empty = els.windows.querySelector("p.muted");
  if (empty) {
    empty.remove();
  }

  const weekday = make(
    "select",
    { attrs: { "aria-label": "Weekday" } },
    WEEKDAYS.map((label, index) =>
      make("option", { text: label, attrs: { value: index } }),
    ),
  );
  weekday.value = String(window.weekday);

  const start = make("input", {
    attrs: {
      type: "time",
      value: window.start_time,
      step: "900",
      "aria-label": "Start time",
      "data-field": "start",
    },
  });
  const end = make("input", {
    attrs: {
      type: "time",
      value: window.end_time,
      step: "900",
      "aria-label": "End time",
      "data-field": "end",
    },
  });

  const remove = make("button", {
    className: "button danger",
    text: "Remove",
    attrs: { type: "button" },
  });

  const row = make("div", { className: "window-row" }, [
    weekday,
    start,
    end,
    remove,
  ]);
  remove.addEventListener("click", () => {
    row.remove();
    if (!els.windows.querySelector(".window-row")) {
      renderWindows();
    }
  });

  els.windows.append(row);
}

function collectWindows() {
  return $$(".window-row", els.windows).map((row) => ({
    weekday: Number(row.querySelector("select").value),
    start_time: row.querySelector('[data-field="start"]').value,
    end_time: row.querySelector('[data-field="end"]').value,
  }));
}

async function saveWindows() {
  if (!availabilityProviderId) {
    return;
  }

  const windows = collectWindows();
  if (windows.some((window) => !window.start_time || !window.end_time)) {
    setMessage("Every window needs a start and an end time.", "error");
    return;
  }

  const button = $("#save-windows");
  setBusy(button, true, "Saving…");
  try {
    const data = await request(
      `/api/providers/${availabilityProviderId}/availability`,
      { method: "PUT", body: { windows } },
    );
    availabilityWindows = data.windows || [];
    renderWindows();
    setMessage("Weekly hours saved.", "success");
  } catch (error) {
    setMessage(error.message, "error");
    if (error.status === 409 && Array.isArray(error.data?.conflicts)) {
      // The server lists the bookings that would be stranded.
      const dates = error.data.conflicts
        .map((row) => `${row.date} ${row.start_time}`)
        .join(", ");
      setMessage(`${error.message} (${dates})`, "error");
    } else if (error.status === 409) {
      setMessage(error.message, "error");
    }
  } finally {
    setBusy(button, false);
  }
}

function renderTimeOff() {
  if (!timeOff.length) {
    renderEmpty(els.timeOffList, "No upcoming days off.");
    return;
  }

  const body = make("tbody");
  for (const row of timeOff) {
    const remove = make("button", {
      className: "button danger",
      text: "Remove",
      attrs: { type: "button" },
    });
    remove.addEventListener("click", () => removeTimeOff(row.date));

    body.append(
      make("tr", {}, [
        make("td", { text: row.date }),
        make("td", { text: row.reason || "—" }),
        make("td", {}, [
          make("div", { className: "table-actions" }, [remove]),
        ]),
      ]),
    );
  }

  els.timeOffList.replaceChildren(
    make("table", {}, [
      make("thead", {}, [
        make("tr", {}, [
          make("th", { text: "Date" }),
          make("th", { text: "Reason" }),
          make("th", { text: "" }),
        ]),
      ]),
      body,
    ]),
  );
}

async function addTimeOff(event) {
  event.preventDefault();
  if (!availabilityProviderId) {
    return;
  }

  const button = els.timeOffForm.querySelector("button[type='submit']");
  setBusy(button, true, "Adding…");
  try {
    await request(`/api/providers/${availabilityProviderId}/time-off`, {
      method: "POST",
      body: {
        date: els.timeOffDate.value,
        reason: els.timeOffReason.value.trim() || null,
      },
    });
    els.timeOffForm.reset();
    els.timeOffDate.min = todayInputValue();
    setMessage("Day off added.", "success");
    await loadAvailability();
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function removeTimeOff(date) {
  if (!availabilityProviderId) {
    return;
  }
  if (!window.confirm(`Remove the day off on ${date}?`)) {
    return;
  }

  try {
    await request(
      `/api/providers/${availabilityProviderId}/time-off/${date}`,
      { method: "DELETE" },
    );
    setMessage("Day off removed.", "success");
    await loadAvailability();
  } catch (error) {
    setMessage(error.message, "error");
  }
}
