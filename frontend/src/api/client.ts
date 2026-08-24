import { API_PREFIX, buildUrl } from "./config";

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  method?: HttpMethod;
  body?: unknown;
  query?: Record<string, string | number | undefined | null>;
  signal?: AbortSignal;
  skipAuth?: boolean;
}

export class ApiError extends Error {
  status: number;
  title?: string;
  detail?: string;
  instance?: string;
  errors?: unknown[];

  constructor(
    message: string,
    status: number,
    fields: Partial<Omit<ApiError, "status" | "message">> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.title = fields.title;
    this.detail = fields.detail;
    this.instance = fields.instance;
    this.errors = fields.errors;
  }

  static async fromResponse(
    response: Response,
    body: unknown,
  ): Promise<ApiError> {
    const status = response.status;

    if (body && typeof body === "object") {
      const problem = body as Record<string, unknown>;
      const detail =
        typeof problem.detail === "string" ? problem.detail : undefined;
      const title =
        typeof problem.title === "string" ? problem.title : undefined;
      const instance =
        typeof problem.instance === "string" ? problem.instance : undefined;
      const errors = Array.isArray(problem.errors) ? problem.errors : undefined;
      return new ApiError(detail || title || response.statusText, status, {
        title,
        detail,
        instance,
        errors,
      });
    }

    return new ApiError(response.statusText, status);
  }
}

let tokenProvider: (() => string | null) | null = null;
let refreshHandler: (() => Promise<boolean>) | null = null;
let logoutHandler: (() => void) | null = null;

export function setTokenProvider(provider: () => string | null) {
  tokenProvider = provider;
}

export function setRefreshHandler(handler: () => Promise<boolean>) {
  refreshHandler = handler;
}

export function setLogoutHandler(handler: () => void) {
  logoutHandler = handler;
}

let inFlightRefresh: Promise<boolean> | null = null;

async function performRefresh(): Promise<boolean> {
  if (!refreshHandler) return false;
  if (!inFlightRefresh) {
    inFlightRefresh = refreshHandler().finally(() => {
      inFlightRefresh = null;
    });
  }
  return inFlightRefresh;
}

export function getAuthHeader(): string | null {
  if (!tokenProvider) return null;
  const token = tokenProvider();
  return token ? `Bearer ${token}` : null;
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const url = buildUrl(`${API_PREFIX}${path}`, options.query);
  const headers: HeadersInit = {
    Accept: "application/json",
  };

  if (options.body !== undefined) {
    (headers as Record<string, string>)["Content-Type"] = "application/json";
  }

  if (!options.skipAuth) {
    const auth = getAuthHeader();
    if (auth) {
      (headers as Record<string, string>)["Authorization"] = auth;
    }
  }

  const init: RequestInit = {
    method: options.method || "GET",
    headers,
    signal: options.signal,
  };

  if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
  }

  const response = await fetch(url, init);

  if (response.status === 401 && !options.skipAuth) {
    const refreshed = await performRefresh();
    if (!refreshed) {
      if (logoutHandler) logoutHandler();
      const body = await safeReadBody(response);
      throw await ApiError.fromResponse(response, body);
    }

    // Retry once with the new token.
    const newAuth = getAuthHeader();
    const retryHeaders: HeadersInit = { ...headers };
    if (newAuth) {
      (retryHeaders as Record<string, string>)["Authorization"] = newAuth;
    }

    const retry = await fetch(url, {
      ...init,
      headers: retryHeaders,
    });

    if (retry.status === 401) {
      if (logoutHandler) logoutHandler();
      const body = await safeReadBody(retry);
      throw await ApiError.fromResponse(retry, body);
    }

    return handleResponse<T>(retry);
  }

  return handleResponse<T>(response);
}

async function safeReadBody(response: Response): Promise<unknown> {
  try {
    const text = await response.text();
    if (!text) return null;
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  const body = await safeReadBody(response);
  if (!response.ok) {
    throw await ApiError.fromResponse(response, body);
  }
  return body as T;
}
