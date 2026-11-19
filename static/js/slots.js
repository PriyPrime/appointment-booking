/** Slot lookup shared by the booking form and the reschedule dialog. */

import { request } from "./api.js";
import { addDays, make, parseDateOnly, toDateInputValue } from "./ui.js";

export async function fetchSlots(providerId, serviceId, date) {
  const query = new URLSearchParams({
    provider_id: providerId,
    service_id: serviceId,
    date,
  });
  const data = await request(`/api/slots?${query}`);
  return data.slots || [];
}

export async function findNextAvailable(
  providerId,
  serviceId,
  fromDateValue,
  days = 14,
) {
  let date = parseDateOnly(fromDateValue);
  if (!date) return null;

  for (let offset = 0; offset <= days; offset += 1) {
    const value = toDateInputValue(date);
    const slots = await fetchSlots(providerId, serviceId, value);
    if (slots.length) return { date: value, slots };
    date = addDays(date, 1);
  }
  return null;
}

export function renderSlotButtons(slots, selected, onSelect) {
  const grid = make("div", { className: "slot-grid" });
  for (const value of slots) {
    const button = make(
      "button",
      {
        className: `slot${value === selected ? " is-selected" : ""}`,
        text: value,
        attrs: {
          type: "button",
          "aria-pressed": value === selected ? "true" : "false",
        },
      },
    );
    button.addEventListener("click", () => onSelect(value));
    grid.append(button);
  }
  return grid;
}
