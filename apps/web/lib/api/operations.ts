import "server-only";

import { apiGet, type ApiResult } from "./client";
import {
  apiEnvelopeValidator,
  cursorPageValidator,
  isCaseDetail,
  isCaseSummary,
  isOperationsHealth,
  moneyItemsMatchContext,
  moneyMatchesContext,
  type ApiEnvelope,
  type CaseDetail,
  type CaseSummary,
  type CursorPage,
  type OperationsHealth,
} from "./contracts";
import { encodeResourceId, toApiSearchParams, type CaseListQuery } from "./query";

const casePageValidator = apiEnvelopeValidator(
  cursorPageValidator(isCaseSummary),
  moneyItemsMatchContext<CaseSummary>((recoveryCase) => recoveryCase.exposure),
);
const caseDetailValidator = apiEnvelopeValidator(isCaseDetail, (detail, context) =>
  moneyMatchesContext(detail.case.exposure, context),
);
const healthValidator = apiEnvelopeValidator(isOperationsHealth);

export function listCases(
  query: CaseListQuery,
): Promise<ApiResult<ApiEnvelope<CursorPage<CaseSummary>>>> {
  return apiGet({
    path: "/api/v1/workspace/cases",
    query: toApiSearchParams(query),
    validator: casePageValidator,
  });
}

export function getCase(caseId: string): Promise<ApiResult<ApiEnvelope<CaseDetail>>> {
  const encodedId = encodeResourceId(caseId);
  if (encodedId === null) {
    return Promise.resolve({
      kind: "invalid-contract",
      message: "The recovery case reference is invalid.",
    });
  }

  return apiGet({
    path: `/api/v1/workspace/cases/${encodedId}`,
    validator: caseDetailValidator,
  });
}

export function getOperationsHealth(): Promise<ApiResult<ApiEnvelope<OperationsHealth>>> {
  return apiGet({
    path: "/api/v1/workspace/operations/health",
    validator: healthValidator,
  });
}

export function getOperationsStreamPath(): string {
  return "/api/v1/workspace/stream";
}
