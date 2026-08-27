import "server-only";

import { apiGet, type ApiResult } from "./client";
import {
  apiEnvelopeValidator,
  cursorPageValidator,
  isBatchReport,
  isBatchSummary,
  isMerchantOverview,
  moneyItemsMatchContext,
  moneyMatchesContext,
  type ApiEnvelope,
  type BatchReport,
  type BatchSummary,
  type CursorPage,
  type MerchantOverview,
} from "./contracts";
import {
  encodeResourceId,
  toApiSearchParams,
  type BatchListQuery,
  type OverviewQuery,
} from "./query";

const overviewValidator = apiEnvelopeValidator(
  isMerchantOverview,
  (overview, context) =>
    overview.integrity.currency_code === context.currency.code &&
    (overview.selected_batch === null ||
      moneyMatchesContext(overview.selected_batch.eligible_at_risk, context)) &&
    (overview.outcome_identity === null ||
      moneyMatchesContext(overview.outcome_identity.eligible_at_risk, context)) &&
    overview.recent_strategies.every((strategy) =>
      moneyMatchesContext(strategy.eligible_at_risk, context),
    ),
);
const batchPageValidator = apiEnvelopeValidator(
  cursorPageValidator(isBatchSummary),
  moneyItemsMatchContext<BatchSummary>((batch) => batch.eligible_at_risk),
);
const batchReportValidator = apiEnvelopeValidator(isBatchReport, (report, context) =>
  moneyMatchesContext(report.batch.eligible_at_risk, context),
);

export function getMerchantOverview(
  query: OverviewQuery,
): Promise<ApiResult<ApiEnvelope<MerchantOverview>>> {
  return apiGet({
    path: "/api/v1/workspace/overview",
    query: toApiSearchParams(query),
    validator: overviewValidator,
  });
}

export function listBatches(
  query: BatchListQuery,
): Promise<ApiResult<ApiEnvelope<CursorPage<BatchSummary>>>> {
  return apiGet({
    path: "/api/v1/workspace/batches",
    query: toApiSearchParams(query),
    validator: batchPageValidator,
  });
}

export function getBatchReport(batchId: string): Promise<ApiResult<ApiEnvelope<BatchReport>>> {
  const encodedId = encodeResourceId(batchId);
  if (encodedId === null) {
    return Promise.resolve({
      kind: "invalid-contract",
      message: "The batch reference is invalid.",
    });
  }

  return apiGet({
    path: `/api/v1/workspace/batches/${encodedId}`,
    validator: batchReportValidator,
  });
}
