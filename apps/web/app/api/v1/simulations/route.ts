import { proxyApi } from "@/lib/api/proxy";

export function POST(request: Request) {
  return proxyApi(request, {
    backendPath: "/api/v1/simulations",
    authenticated: true,
    mutation: true,
  });
}
