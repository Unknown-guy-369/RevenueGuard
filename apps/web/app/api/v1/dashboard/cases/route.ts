import { proxyApi } from "@/lib/api/proxy";

export function GET(request: Request) {
  return proxyApi(request, {
    backendPath: "/api/v1/dashboard/cases",
    authenticated: true,
    includeQuery: true,
  });
}
