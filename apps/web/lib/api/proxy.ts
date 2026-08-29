import "server-only";

import { getDashboardSession } from "@/lib/auth/session";
import { isSameOriginFormPost } from "@/lib/auth/origin";

type ProxyOptions = {
  backendPath: string;
  authenticated: boolean;
  includeQuery?: boolean;
  mutation?: boolean;
  requireOperator?: boolean;
};

function backendUrl(path: string, request: Request, includeQuery: boolean): URL | null {
  const configured = process.env.REVENUEGUARD_API_URL?.trim();
  if (!configured || !path.startsWith("/") || path.startsWith("//")) return null;
  try {
    const base = new URL(configured);
    if (base.protocol !== "http:" && base.protocol !== "https:") return null;
    if (base.username || base.password || base.search || base.hash) return null;
    if (
      process.env.NODE_ENV === "production" &&
      base.protocol !== "https:" &&
      base.hostname !== "localhost" &&
      base.hostname !== "127.0.0.1"
    ) {
      return null;
    }
    const url = new URL(path, base);
    if (url.origin !== base.origin) return null;
    if (includeQuery) url.search = new URL(request.url).search;
    return url;
  } catch {
    return null;
  }
}

export async function proxyApi(request: Request, options: ProxyOptions): Promise<Response> {
  if (options.mutation && !isSameOriginFormPost(request)) {
    return Response.json({ detail: "invalid request origin" }, { status: 403 });
  }
  const session = options.authenticated ? await getDashboardSession() : null;
  if (options.authenticated && session === null) {
    return Response.json({ detail: "dashboard session is required" }, { status: 401 });
  }
  const url = backendUrl(options.backendPath, request, options.includeQuery ?? false);
  if (url === null) {
    return Response.json({ detail: "RevenueGuard API is not configured" }, { status: 503 });
  }
  const headers = new Headers({ accept: "application/json" });
  if (options.authenticated) {
    const token = process.env.REVENUEGUARD_DASHBOARD_API_TOKEN?.trim();
    if (!token || session === null) {
      return Response.json(
        { detail: "dashboard API authentication is unavailable" },
        { status: 503 },
      );
    }
    headers.set("authorization", `Bearer ${token}`);
    headers.set("x-revenueguard-merchant-id", session.merchant_id);
    if (options.requireOperator) {
      const operatorId = process.env.REVENUEGUARD_DASHBOARD_OPERATOR_ID?.trim();
      if (!operatorId) {
        return Response.json(
          { detail: "dashboard operator identity is unavailable" },
          { status: 503 },
        );
      }
      headers.set("x-revenueguard-operator-id", operatorId);
    }
  }
  let body: string | undefined;
  if (options.mutation) {
    const contentType = request.headers.get("content-type")?.toLowerCase();
    if (!contentType?.includes("application/json")) {
      return Response.json({ detail: "application/json is required" }, { status: 415 });
    }
    body = await request.text();
    if (new TextEncoder().encode(body).byteLength > 16_384) {
      return Response.json({ detail: "request body is too large" }, { status: 413 });
    }
    headers.set("content-type", "application/json");
  }
  try {
    const response = await fetch(url, {
      method: options.mutation ? "POST" : "GET",
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      headers,
      body,
      signal: AbortSignal.timeout(10_000),
    });
    const responseType = response.headers.get("content-type")?.toLowerCase();
    if (!responseType?.includes("application/json")) {
      return Response.json(
        { detail: "RevenueGuard returned an invalid response" },
        { status: 502 },
      );
    }
    return new Response(await response.text(), {
      status: response.status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch {
    return Response.json({ detail: "RevenueGuard API is unavailable" }, { status: 503 });
  }
}
