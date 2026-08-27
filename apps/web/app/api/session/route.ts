import {
  createDashboardSession,
  dashboardAuthConfigured,
  dashboardSessionCookie,
  dashboardSessionMaxAge,
  verifyOperatorAccessKey,
} from "@/lib/auth/session";
import { isSameOriginFormPost } from "@/lib/auth/origin";

export async function POST(request: Request) {
  if (!isSameOriginFormPost(request)) {
    return Response.json({ error: "invalid request origin" }, { status: 403 });
  }
  const publicOrigin = request.headers.get("origin")!;
  if (!dashboardAuthConfigured()) {
    return Response.redirect(new URL("/sign-in?error=configuration", publicOrigin), 303);
  }
  const form = await request.formData();
  const accessKey = form.get("access_key");
  if (typeof accessKey !== "string" || !verifyOperatorAccessKey(accessKey)) {
    return Response.redirect(new URL("/sign-in?error=invalid", publicOrigin), 303);
  }
  const session = createDashboardSession();
  if (session === null) {
    return Response.redirect(new URL("/sign-in?error=configuration", publicOrigin), 303);
  }
  return new Response(null, {
    status: 303,
    headers: {
      Location: new URL("/dashboard", publicOrigin).toString(),
      "Set-Cookie": `${dashboardSessionCookie}=${session}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${dashboardSessionMaxAge}${process.env.NODE_ENV === "production" ? "; Secure" : ""}`,
    },
  });
}
