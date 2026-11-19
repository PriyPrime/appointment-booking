/**
 * Page bootstrap: session restore, auth forms, navigation and module wiring.
 * ES modules are loaded directly by the browser, so there is no build step.
 */

import {
  getUser,
  loadSession,
  login,
  logout,
  register,
} from "./auth.js";
import { initBooking, onAuthChanged as onBookingAuthChanged } from "./booking.js";
import {
  clearAppointments,
  initAppointments,
  loadAppointments,
} from "./appointments.js";
import {
  initStaff,
  onAuthChanged as onStaffAuthChanged,
  openAvailability,
} from "./staff.js";
import {
  $,
  $$,
  clearMessage,
  make,
  setBusy,
  setMessage,
  showView,
} from "./ui.js";

function renderAccount() {
  const bar = $("#account-bar");
  bar.replaceChildren();
  const user = getUser();

  // Staff navigation appears only for the roles that can use it.
  for (const button of $$(".nav-button[data-roles]")) {
    const roles = button.dataset.roles.split(" ");
    button.hidden = !user || !roles.includes(user.role);
  }

  if (!user) {
    bar.append(make("span", { className: "muted", text: "Browsing as guest" }));
    const signIn = make("button", {
      className: "button ghost",
      text: "Sign in",
      attrs: { type: "button" },
    });
    signIn.addEventListener("click", focusSignIn);
    bar.append(signIn);
    return;
  }

  bar.append(
    make("span", { className: "user-chip" }, [
      make("strong", { text: user.name }),
      make("span", { className: "role-chip", text: user.role }),
    ]),
  );

  const signOut = make("button", {
    className: "button ghost",
    text: "Sign out",
    attrs: { type: "button" },
  });
  signOut.addEventListener("click", signOutUser);
  bar.append(signOut);
}

function focusSignIn() {
  showView("book");
  history.replaceState(null, "", "#book");
  const panel = $("#auth-panel");
  if (panel.hidden) return; // Already signed in.
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
  $("#login-email").focus({ preventScroll: true });
}

async function signOutUser() {
  try {
    await logout();
    await handleAuthChange();
    setMessage("Signed out.", "info");
    showView("book");
    history.replaceState(null, "", "#book");
  } catch (error) {
    setMessage(error.message, "error");
  }
}

async function handleAuthChange() {
  const user = getUser();
  renderAccount();
  $("#auth-panel").hidden = Boolean(user);
  onBookingAuthChanged();
  onStaffAuthChanged();

  // Losing the role must also close screens the account can no longer use.
  if (
    (!user || user.role !== "admin") &&
    !$("#manage-view").hidden
  ) {
    showView("book");
    history.replaceState(null, "", "#book");
  }
  if (
    (!user || (user.role !== "admin" && user.role !== "provider")) &&
    !$("#availability-view").hidden
  ) {
    showView("book");
    history.replaceState(null, "", "#book");
  }

  if (user) {
    await loadAppointments();
  } else {
    clearAppointments();
  }
}

function switchAuthTab(name) {
  for (const tab of $$("[data-auth-tab]")) {
    const active = tab.dataset.authTab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  $("#login-form").hidden = name !== "login";
  $("#register-form").hidden = name !== "register";
}

function wireAuth() {
  for (const tab of $$("[data-auth-tab]")) {
    tab.addEventListener("click", () => switchAuthTab(tab.dataset.authTab));
  }

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    clearMessage();
    const button = $("#login-submit");
    setBusy(button, true, "Signing in…");

    try {
      const user = await login(
        $("#login-email").value.trim(),
        $("#login-password").value,
      );
      await handleAuthChange();
      setMessage(`Welcome back, ${user.name}.`, "success");
      $("#login-form").reset();
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  });

  $("#register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    clearMessage();
    const button = $("#register-submit");
    setBusy(button, true, "Creating…");

    try {
      const user = await register({
        name: $("#register-name").value.trim(),
        email: $("#register-email").value.trim(),
        phone: $("#register-phone").value.trim(),
        password: $("#register-password").value,
      });
      await handleAuthChange();
      setMessage(`Welcome, ${user.name}. Your account is ready.`, "success");
      $("#register-form").reset();
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  });
}

function navigate(name, options = {}) {
  const user = getUser();

  if (name === "appointments" && !user) {
    setMessage("Sign in to see and manage your appointments.", "info");
    focusSignIn();
    return;
  }
  if (name === "manage" && (!user || user.role !== "admin")) {
    setMessage("Only administrators can manage services and providers.", "info");
    showView("book");
    history.replaceState(null, "", "#book");
    return;
  }
  if (
    name === "availability" &&
    (!user || (user.role !== "admin" && user.role !== "provider"))
  ) {
    setMessage("Sign in with a staff account to edit availability.", "info");
    focusSignIn();
    return;
  }

  showView(name);
  history.replaceState(null, "", `#${name}`);

  if (name === "appointments") {
    loadAppointments();
  }
  if (name === "availability") {
    openAvailability(options.providerId);
  }
}

function wireNavigation() {
  for (const button of $$(".nav-button")) {
    button.addEventListener("click", () => navigate(button.dataset.view));
  }

  $(".brand").addEventListener("click", (event) => {
    event.preventDefault();
    navigate("book");
  });
}

async function init() {
  initBooking({
    onRequireLogin: () => {
      setMessage("Sign in to book this time.", "info");
      focusSignIn();
    },
  });
  initStaff({ navigate });
  initAppointments();
  wireNavigation();
  wireAuth();

  // A new booking while the appointments view is open should show up there.
  document.addEventListener("appointments:changed", () => {
    if (!$("#appointments-view").hidden && getUser()) {
      loadAppointments();
    }
  });

  try {
    await loadSession();
  } catch {
    // A network problem should not break the page; stay signed out.
    setMessage("Could not check your session. You can still browse.", "error");
  }
  await handleAuthChange();

  const user = getUser();
  if (location.hash === "#appointments" && user) {
    navigate("appointments");
  } else if (location.hash === "#manage" && user?.role === "admin") {
    navigate("manage");
  } else if (
    location.hash === "#availability" &&
    (user?.role === "admin" || user?.role === "provider")
  ) {
    navigate("availability");
  } else {
    showView("book");
  }
}

init();
