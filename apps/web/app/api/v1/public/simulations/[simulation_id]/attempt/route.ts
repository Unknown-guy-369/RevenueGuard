import { proxyApi } from "@/lib/api/proxy";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ simulation_id: string }> },
) {
  const { simulation_id: simulationId } = await params;
  return proxyApi(request, {
    backendPath: `/api/v1/public/simulations/${encodeURIComponent(simulationId)}/attempt`,
    authenticated: false,
    mutation: true,
  });
}
