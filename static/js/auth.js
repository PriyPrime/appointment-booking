/**
 * Authentication state and API calls.
 *
 * The server keeps the session in an HttpOnly cookie; this module only stores
 * the current user in memory for rendering. A page refresh calls /auth/me to
 * restore it.
 */

import { request } from "./api.js";

let currentUser = null;

export function getUser() {
  return currentUser;
}

export function isLoggedIn() {
  return currentUser !== null;
}

export async function loadSession() {
  currentUser = await request("/api/auth/me");
  return currentUser;
}

export async function login(email, password) {
  currentUser = await request("/api/auth/login", {
    method: "POST",
    body: { email, password },
  });
  return currentUser;
}

export async function register({ name, email, phone, password }) {
  currentUser = await request("/api/auth/register", {
    method: "POST",
    body: { name, email, phone: phone || null, password },
  });
  return currentUser;
}

export async function logout() {
  await request("/api/auth/logout", { method: "POST" });
  currentUser = null;
}
