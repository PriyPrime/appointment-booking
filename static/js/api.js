/**
 * One place for every HTTP call.
 *
 * The wrapper keeps error handling consistent: the API always returns JSON,
 * and failures carry an `error` message we can show to the user.
 */

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

export async function request(path, { method = "GET", body } = {}) {
  const options = {
    method,
    // The session is a cookie, so same-origin credentials must be included.
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };

  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  const response = await fetch(path, options);
  const text = await response.text();
  let data = null;

  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // A non-JSON response usually means a proxy or server error page.
      data = null;
    }
  }

  if (!response.ok) {
    const message =
      (data && data.error) || `Request failed (${response.status}).`;
    throw new ApiError(message, response.status, data);
  }

  return data;
}
