import "server-only";

import { createHmac, createHash, timingSafeEqual } from "node:crypto";

import { cookies } from "next/headers";

export const dashboardSessionCookie = "revenueguard_dashboard_session";
const sessionLifetimeSeconds = 8 * 60 * 60;

type DashboardSession = {
  merchant_id: string;
  expires_at: number;
};

type AuthConfiguration = {
  access_key: string;
  merchant_id: string;
  session_secret: string;
};

function configuration(): AuthConfiguration | null {
  const accessKey = process.env.REVENUEGUARD_DASHBOARD_OPERATOR_ACCESS_KEY?.trim();
  const merchantId = process.env.REVENUEGUARD_DASHBOARD_MERCHANT_ID?.trim();
  const sessionSecret = process.env.REVENUEGUARD_DASHBOARD_SESSION_SECRET?.trim();
  if (!accessKey || !merchantId || !sessionSecret || sessionSecret.length < 32) return null;
  return {
    access_key: accessKey,
    merchant_id: merchantId,
    session_secret: sessionSecret,
  };
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value).digest();
}

export function dashboardAuthConfigured(): boolean {
  return configuration() !== null;
}

export function verifyOperatorAccessKey(candidate: string): boolean {
  const configured = configuration();
  if (configured === null || !candidate || candidate.length > 512) return false;
  return timingSafeEqual(digest(candidate), digest(configured.access_key));
}

export function createDashboardSession(now: Date = new Date()): string | null {
  const configured = configuration();
  if (configured === null) return null;
  const session: DashboardSession = {
    merchant_id: configured.merchant_id,
    expires_at: Math.floor(now.getTime() / 1000) + sessionLifetimeSeconds,
  };
  const payload = Buffer.from(JSON.stringify(session)).toString("base64url");
  const signature = createHmac("sha256", configured.session_secret)
    .update(payload)
    .digest("base64url");
  return `${payload}.${signature}`;
}

export function verifyDashboardSession(
  value: string,
  now: Date = new Date(),
): DashboardSession | null {
  const configured = configuration();
  if (configured === null || value.length > 2_048) return null;
  const [payload, signature, extra] = value.split(".");
  if (!payload || !signature || extra !== undefined) return null;
  const expected = createHmac("sha256", configured.session_secret).update(payload).digest();
  let supplied: Buffer;
  try {
    supplied = Buffer.from(signature, "base64url");
  } catch {
    return null;
  }
  if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const merchantId = Reflect.get(parsed, "merchant_id");
  const expiresAt = Reflect.get(parsed, "expires_at");
  if (
    merchantId !== configured.merchant_id ||
    typeof expiresAt !== "number" ||
    !Number.isSafeInteger(expiresAt) ||
    expiresAt <= Math.floor(now.getTime() / 1000)
  ) {
    return null;
  }
  return { merchant_id: merchantId, expires_at: expiresAt };
}

export async function getDashboardSession(): Promise<DashboardSession | null> {
  const value = (await cookies()).get(dashboardSessionCookie)?.value;
  return value ? verifyDashboardSession(value) : null;
}

export const dashboardSessionMaxAge = sessionLifetimeSeconds;
