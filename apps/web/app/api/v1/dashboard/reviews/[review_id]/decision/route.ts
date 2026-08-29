import { proxyApi } from "@/lib/api/proxy";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ review_id: string }> },
) {
  const { review_id: reviewId } = await params;
  return proxyApi(request, {
    backendPath: `/api/v1/dashboard/reviews/${encodeURIComponent(reviewId)}/decision`,
    authenticated: true,
    mutation: true,
    requireOperator: true,
  });
}
