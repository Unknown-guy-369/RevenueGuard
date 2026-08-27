import "server-only";

import type { ContractValidator, DataStatus } from "./contracts";

export type ApiResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "degraded"; data: T; message: string }
  | { kind: "unauthenticated" }
  | { kind: "forbidden" }
  | { kind: "not-found" }
  | { kind: "unavailable"; message: string; retry_after_seconds?: number }
  | { kind: "invalid-contract"; message: string };

interface ApiErrorResponse {
  code: "RESOURCE_NOT_FOUND";
  message: string;
  resource_type?: string;
  resource_id?: string;
}

interface ApiGetOptions<T> {
  path: string;
  validator: ContractValidator<T>;
  query?: URLSearchParams;
  timeout_ms?: number;
}

const defaultTimeoutMs = 8_000;
const maximumTimeoutMs = 30_000;

function apiBaseUrl(): URL | null {
  const configured = process.env.REVENUEGUARD_API_URL?.trim();
  if (!configured) return null;

  try {
    const url = new URL(configured);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password || url.search || url.hash) return null;
    if (
      process.env.NODE_ENV === "production" &&
      url.protocol !== "https:" &&
      url.hostname !== "localhost" &&
      url.hostname !== "127.0.0.1"
    ) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function apiUrl(path: string, query?: URLSearchParams): URL | null {
  const baseUrl = apiBaseUrl();
  const hasControlCharacters = [...path].some((character) => character.charCodeAt(0) < 32);
  if (baseUrl === null || !path.startsWith("/") || path.startsWith("//") || hasControlCharacters) {
    return null;
  }

  const url = new URL(path, baseUrl);
  if (url.origin !== baseUrl.origin) return null;
  if (query !== undefined) url.search = query.toString();
  return url;
}

function retryAfterSeconds(response: Response): number | undefined {
  const value = response.headers.get("retry-after");
  if (value === null || !/^\d+$/.test(value)) return undefined;
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) && seconds >= 0 ? seconds : undefined;
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    record.code === "RESOURCE_NOT_FOUND" &&
    typeof record.message === "string" &&
    record.message.trim().length > 0
  );
}

function apiErrorPayload(value: unknown): unknown {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return value;
  const detail = Reflect.get(value, "detail");
  return detail ?? value;
}

async function authoritativeNotFound(response: Response): Promise<boolean> {
  const contentType = response.headers.get("content-type")?.toLowerCase();
  if (!contentType?.includes("application/json")) return false;

  try {
    return isApiErrorResponse(apiErrorPayload(await response.json()));
  } catch {
    return false;
  }
}

function isDegradedPayload(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const context = Reflect.get(value, "context");
  if (typeof context !== "object" || context === null) return false;
  const status = Reflect.get(context, "data_status") as DataStatus | undefined;
  return status === "DEGRADED" || status === "PARTIAL" || status === "PROVISIONAL";
}

function safeTimeout(timeoutMs: number | undefined): number {
  if (timeoutMs === undefined || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    return defaultTimeoutMs;
  }
  return Math.min(timeoutMs, maximumTimeoutMs);
}

export async function apiGet<T>({
  path,
  validator,
  query,
  timeout_ms: timeoutMs,
}: ApiGetOptions<T>): Promise<ApiResult<T>> {
  const url = apiUrl(path, query);
  if (url === null) {
    return {
      kind: "unavailable",
      message: "The RevenueGuard API is not configured for this workspace.",
    };
  }

  const apiToken = process.env.REVENUEGUARD_DASHBOARD_API_TOKEN?.trim();
  const merchantId = process.env.REVENUEGUARD_DASHBOARD_MERCHANT_ID?.trim();
  if (!apiToken || !merchantId) {
    return {
      kind: "unavailable",
      message: "Dashboard API authentication is not configured for this workspace.",
    };
  }
  const requestHeaders = new Headers({
    accept: "application/json",
    authorization: `Bearer ${apiToken}`,
    "x-revenueguard-merchant-id": merchantId,
  });

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      cache: "no-store",
      credentials: "omit",
      headers: requestHeaders,
      redirect: "error",
      signal: AbortSignal.timeout(safeTimeout(timeoutMs)),
    });
  } catch {
    return {
      kind: "unavailable",
      message: "Authoritative data is temporarily unavailable from RevenueGuard.",
    };
  }

  if (response.status === 401) return { kind: "unauthenticated" };
  if (response.status === 403) return { kind: "forbidden" };
  if (response.status === 404) {
    return (await authoritativeNotFound(response))
      ? { kind: "not-found" }
      : {
          kind: "unavailable",
          message: "RevenueGuard did not provide an authoritative resource-not-found response.",
        };
  }
  if (response.status === 429 || response.status >= 500) {
    const retryAfter = retryAfterSeconds(response);
    return {
      kind: "unavailable",
      message: "Authoritative data is temporarily unavailable from RevenueGuard.",
      ...(retryAfter !== undefined && { retry_after_seconds: retryAfter }),
    };
  }
  if (!response.ok) {
    return {
      kind: "unavailable",
      message: `RevenueGuard could not serve this read-only request (HTTP ${response.status}).`,
    };
  }

  const contentType = response.headers.get("content-type");
  if (contentType === null || !contentType.toLowerCase().includes("application/json")) {
    return {
      kind: "invalid-contract",
      message: "RevenueGuard returned an unsupported response format.",
    };
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return {
      kind: "invalid-contract",
      message: "RevenueGuard returned malformed JSON.",
    };
  }

  if (!validator(payload)) {
    return {
      kind: "invalid-contract",
      message: "RevenueGuard returned data that does not match the workspace contract.",
    };
  }

  return isDegradedPayload(payload)
    ? {
        kind: "degraded",
        data: payload,
        message: "RevenueGuard marked this response as degraded.",
      }
    : { kind: "ok", data: payload };
}
