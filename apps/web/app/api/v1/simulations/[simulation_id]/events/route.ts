import { proxyApi } from "@/lib/api/proxy";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ simulation_id: string }> },
) {
  const { simulation_id: simulationId } = await params;
  return proxyApi(request, {
    backendPath: `/api/v1/simulations/${encodeURIComponent(simulationId)}/events`,
    authenticated: true,
  });
}
