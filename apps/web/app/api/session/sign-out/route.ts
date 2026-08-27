import { dashboardSessionCookie } from "@/lib/auth/session";
import { isSameOriginFormPost } from "@/lib/auth/origin";

export async function POST(request: Request) {
  if (!isSameOriginFormPost(request)) {
    return Response.json({ error: "invalid request origin" }, { status: 403 });
  }
  const publicOrigin = request.headers.get("origin")!;
  return new Response(null, {
    status: 303,
    headers: {
      Location: new URL("/sign-in", publicOrigin).toString(),
      "Set-Cookie": `${dashboardSessionCookie}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${process.env.NODE_ENV === "production" ? "; Secure" : ""}`,
    },
  });
}
