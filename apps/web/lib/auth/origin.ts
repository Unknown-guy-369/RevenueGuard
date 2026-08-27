/** Validate browser navigation metadata without trusting Next's reconstructed request URL host. */
export function isSameOriginFormPost(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host || request.headers.get("sec-fetch-site") !== "same-origin") return false;

  try {
    const parsed = new URL(origin);
    return (
      (parsed.protocol === "https:" || parsed.protocol === "http:") &&
      !parsed.username &&
      !parsed.password &&
      parsed.host === host
    );
  } catch {
    return false;
  }
}
