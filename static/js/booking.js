/**
 * The three-step booking flow: service -> provider -> date/time.
 *
 * The browser never decides whether a booking is valid. It only hides
 * impossible choices; the server re-checks every rule when the form posts.
 */

import { request } from "./api.js";
import { isLoggedIn } from "./auth.js";
import {
  fetchSlots,
  findNextAvailable,
  renderSlotButtons,
} from "./slots.js";
import {
  $,
  addDays,
  formatDate,
  formatMoney,
  formatTime,
  make,
  maxDateInputValue,
  parseDateOnly,
  setBusy,
  setMessage,
  toDateInputValue,
  todayInputValue,
} from "./ui.js";

let services = [];
let providers = [];
let selectedService = null;
let selectedProvider = null;
let selectedDate = "";
let selectedSlot = null;
let slotRequest = 0;
let requireLogin = () => {};

const els = {};

export function initBooking({ onRequireLogin } = {}) {
  requireLogin = onRequireLogin || (() => {});

  els.serviceList = $("#service-list");
  els.providerList = $("#provider-list");
  els.date = $("#booking-date");
  els.slotArea = $("#slot-area");
  els.notes = $("#booking-notes");
  els.bookButton = $("#book-button");

  els.date.min = todayInputValue();
  els.date.max = maxDateInputValue();
  els.date.addEventListener("change", () => {
    selectedDate = els.date.value;
    selectedSlot = null;
    fetchAndRenderSlots();
  });
  els.bookButton.addEventListener("click", book);

  loadServices();
}

export function onAuthChanged() {
  updateBookButton();
}

async function loadServices() {
  try {
    services = await request("/api/services");
  } catch (error) {
    els.serviceList.replaceChildren(
      make("p", { className: "empty-state", text: error.message }),
    );
    return;
  }

  if (!services.length) {
    els.serviceList.replaceChildren(
      make("p", {
        className: "empty-state",
        text: "No services are available right now. Please check back soon.",
      }),
    );
    return;
  }

  renderServices();
}

function renderServices() {
  const list = make("div", { className: "choice-stack" });
  for (const service of services) {
    const selected = selectedService && selectedService.id === service.id;
    const button = make(
      "button",
      {
        className: `choice${selected ? " is-selected" : ""}`,
        attrs: { type: "button" },
      },
      [
        make("span", { className: "choice-title", text: service.name }),
        make(
          "span",
          {
            className: "choice-meta",
            text: `${service.duration_min} min · ${formatMoney(service.price)}`,
          },
        ),
        service.description
          ? make("span", {
              className: "choice-description",
              text: service.description,
            })
          : null,
      ],
    );
    button.addEventListener("click", () => chooseService(service));
    list.append(button);
  }
  els.serviceList.replaceChildren(list);
}

async function chooseService(service) {
  selectedService = service;
  selectedProvider = null;
  selectedDate = "";
  selectedSlot = null;
  slotRequest += 1;

  renderServices();
  els.providerList.replaceChildren(
    make("p", { className: "muted", text: "Loading providers…" }),
  );
  els.slotArea.replaceChildren(
    make("p", {
      className: "muted",
      text: "Choose a provider to see available times.",
    }),
  );
  els.date.value = "";
  els.date.disabled = true;
  updateBookButton();

  try {
    providers = await request(`/api/providers?service_id=${service.id}`);
  } catch (error) {
    els.providerList.replaceChildren(
      make("p", { className: "empty-state", text: error.message }),
    );
    return;
  }

  // A slow response for an earlier service must not replace the new one.
  if (!selectedService || selectedService.id !== service.id) return;
  renderProviders();
}

function renderProviders() {
  if (!selectedService) {
    els.providerList.replaceChildren(
      make("p", { className: "muted", text: "Pick a service first." }),
    );
    return;
  }

  if (!providers.length) {
    els.providerList.replaceChildren(
      make("p", {
        className: "empty-state",
        text: "No provider currently offers this service.",
      }),
    );
    return;
  }

  const list = make("div", { className: "choice-stack" });
  for (const provider of providers) {
    const selected = selectedProvider && selectedProvider.id === provider.id;
    const button = make(
      "button",
      {
        className: `choice${selected ? " is-selected" : ""}`,
        attrs: { type: "button" },
      },
      [
        make("span", { className: "choice-title", text: provider.name }),
        make("span", {
          className: "choice-meta",
          text: provider.specialization || "Service provider",
        }),
      ],
    );
    button.addEventListener("click", () => chooseProvider(provider));
    list.append(button);
  }
  els.providerList.replaceChildren(list);
}

function chooseProvider(provider) {
  selectedProvider = provider;
  selectedSlot = null;
  renderProviders();

  els.date.disabled = false;
  if (!els.date.value) {
    els.date.value = todayInputValue();
  }
  selectedDate = els.date.value;
  fetchAndRenderSlots();
}

async function fetchAndRenderSlots() {
  if (!selectedProvider || !selectedService || !selectedDate) {
    updateBookButton();
    return;
  }

  const token = ++slotRequest;
  els.slotArea.replaceChildren(
    make("p", { className: "muted", text: "Checking available times…" }),
  );

  let slots;
  try {
    slots = await fetchSlots(
      selectedProvider.id,
      selectedService.id,
      selectedDate,
    );
  } catch (error) {
    if (token !== slotRequest) return;
    els.slotArea.replaceChildren(
      make("p", { className: "empty-state", text: error.message }),
    );
    updateBookButton();
    return;
  }

  // A slower request for an older date must not overwrite a newer choice.
  if (token !== slotRequest) return;

  selectedSlot = null;
  if (!slots.length) {
    showNoSlots();
    updateBookButton();
    return;
  }

  const grid = renderSlotButtons(slots, selectedSlot, (value) => {
    selectedSlot = value;
    for (const button of grid.children) {
      const active = button.textContent === selectedSlot;
      button.classList.toggle("is-selected", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
    updateBookButton();
  });

  els.slotArea.replaceChildren(
    make("p", {
      className: "muted",
      text: `Available on ${formatDate(selectedDate)}`,
    }),
    grid,
  );
  updateBookButton();
}

function showNoSlots() {
  const nextButton = make("button", {
    className: "button ghost",
    text: "Find next available day",
    attrs: { type: "button" },
  });
  nextButton.addEventListener("click", () => findNextDay(nextButton));

  els.slotArea.replaceChildren(
    make("p", {
      className: "empty-state",
      text: `No times are available on ${formatDate(selectedDate)}.`,
    }),
    make("div", { className: "slot-hint" }, [
      make("span", {
        className: "muted",
        text: "Try another date or search the next two weeks.",
      }),
      nextButton,
    ]),
  );
}

async function findNextDay(button) {
  if (!selectedProvider || !selectedService) return;
  setBusy(button, true, "Searching…");

  try {
    const found = await findNextAvailable(
      selectedProvider.id,
      selectedService.id,
      toDateInputValue(addDays(parseDateOnly(selectedDate), 1)),
    );
    if (!found) {
      setMessage("No open times in the next two weeks. Try a later date.", "info");
      return;
    }
    selectedDate = found.date;
    els.date.value = found.date;
    selectedSlot = null;
    setMessage(`Next available day: ${formatDate(found.date)}`, "info");
    await fetchAndRenderSlots();
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function updateBookButton() {
  const button = els.bookButton;
  if (!selectedSlot) {
    button.textContent = "Choose a time";
    button.disabled = true;
    return;
  }

  button.disabled = false;
  button.textContent = isLoggedIn()
    ? `Book ${formatTime(selectedSlot)}`
    : "Sign in to book";
}

async function book() {
  if (!isLoggedIn()) {
    requireLogin();
    return;
  }
  if (!selectedService || !selectedProvider || !selectedSlot) return;

  const button = els.bookButton;
  const bookedSlot = selectedSlot;
  setBusy(button, true, "Booking…");

  try {
    await request("/api/appointments", {
      method: "POST",
      body: {
        provider_id: selectedProvider.id,
        service_id: selectedService.id,
        date: selectedDate,
        start_time: bookedSlot,
        notes: els.notes.value.trim() || null,
      },
    });

    setMessage(
      `Booked ${selectedService.name} with ${selectedProvider.name} on ` +
        `${formatDate(selectedDate)} at ${formatTime(bookedSlot)}.`,
      "success",
    );
    els.notes.value = "";
    selectedSlot = null;
    await fetchAndRenderSlots();
    document.dispatchEvent(new CustomEvent("appointments:changed"));
  } catch (error) {
    if (error.status === 401) {
      requireLogin();
    } else {
      setMessage(error.message, "error");
    }
    // A 409 means the slot went away; refresh so the user sees the truth.
    if (error.status === 409) {
      await fetchAndRenderSlots();
    }
  } finally {
    setBusy(button, false);
    updateBookButton();
  }
}
