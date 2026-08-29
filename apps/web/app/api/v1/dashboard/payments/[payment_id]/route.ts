import { proxyApi } from "@/lib/api/proxy";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ payment_id: string }> },
) {
  const { payment_id: paymentId } = await params;
  return proxyApi(request, {
    backendPath: `/api/v1/dashboard/payments/${encodeURIComponent(paymentId)}`,
    authenticated: true,
  });
}
