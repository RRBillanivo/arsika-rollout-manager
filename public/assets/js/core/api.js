// HTTP client for the JSON API (/api/v1). Every request of the app goes through this module.
// Success bodies are {data, meta}; errors are {error: {code, message, fields, details}} in the interface language.
import { lang, t } from './i18n.js';
const BASE = 'api/v1/';
let csrfToken = '';
let sessionExpiredHandler = null;

export function setCsrfToken(token) {
  csrfToken = token || '';
}

/** Called when the server answers 401 "unauthenticated" (session expired, account blocked or deleted). */
export function onSessionExpired(handler) {
  sessionExpiredHandler = handler;
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = '', fields = null, details = '' } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code || '';
    this.fields = fields || {};
    this.details = details || '';
  }
}

function httpMessage(status) {
  if (status === 404) return t('common.api.not_found');
  if (status === 413) return t('common.api.too_large');
  if (status === 502 || status === 503 || status === 504) return t('common.api.busy');
  return t('common.api.failed', { status });
}

/** Remember a rotated CSRF token and report expired sessions. */
function inspect(status, body) {
  const data = body && body.data;
  if (data && typeof data === 'object' && data.csrf_token) csrfToken = data.csrf_token;
  if (status === 401 && body && body.error && body.error.code === 'unauthenticated' && sessionExpiredHandler) sessionExpiredHandler();
}

function toError(status, body) {
  const e = (body && body.error) || {};
  return new ApiError(e.message || httpMessage(status), { status, code: e.code, fields: e.fields, details: e.details });
}

const isCsrfError = (status, body) => status === 403 && body && body.error && body.error.code === 'csrf_token_mismatch';

/** Fetch a fresh CSRF token (after the session expired on the server). */
async function refreshToken() {
  await request('app', { retry: false });
}

export async function request(path, { method = 'GET', data = null, retry = true } = {}) {
  const options = { method, credentials: 'same-origin', headers: { Accept: 'application/json', 'X-Locale': lang() } };
  if (method !== 'GET') options.headers['X-CSRF-Token'] = csrfToken;
  if (data !== null) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(data);
  }
  let res;
  try {
    res = await fetch(BASE + path, options);
  } catch (e) {
    throw new ApiError(t('common.api.offline'));
  }
  if (res.status === 204) return { data: null };
  let body = null;
  try {
    body = await res.json();
  } catch (e) {
    /* not JSON */
  }
  if (!body) throw new ApiError(httpMessage(res.status), { status: res.status });
  inspect(res.status, body);
  if (isCsrfError(res.status, body) && retry) {
    await refreshToken();
    return request(path, { method, data, retry: false });
  }
  if (!res.ok) throw toError(res.status, body);
  return body;
}

export const get = (path) => request(path);
export const post = (path, data = {}) => request(path, { method: 'POST', data });
export const put = (path, data = {}) => request(path, { method: 'PUT', data });
export const patch = (path, data = {}) => request(path, { method: 'PATCH', data });
export const del = (path, data = null) => request(path, { method: 'DELETE', data });
