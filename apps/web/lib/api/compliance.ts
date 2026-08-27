import "server-only";

import { apiGet, type ApiResult } from "./client";
import {
  apiEnvelopeValidator,
  cursorPageValidator,
  isAuditEntry,
  isComplianceSummary,
  isIncidentSummary,
  isMerchantSettings,
  isPolicyVersion,
  isReportVersion,
  isReviewSummary,
  moneyItemsMatchContext,
  type ApiEnvelope,
  type AuditEntry,
  type ComplianceSummary,
  type CursorPage,
  type IncidentSummary,
  type MerchantSettings,
  type PolicyVersion,
  type ReportVersion,
  type ReviewSummary,
} from "./contracts";
import {
  toApiSearchParams,
  type AuditQuery,
  type DateQuery,
  type IncidentListQuery,
  type PolicyListQuery,
  type ReportListQuery,
  type ReviewListQuery,
} from "./query";

const complianceSummaryValidator = apiEnvelopeValidator(isComplianceSummary);
const reviewPageValidator = apiEnvelopeValidator(
  cursorPageValidator(isReviewSummary),
  moneyItemsMatchContext<ReviewSummary>((review) => review.proposed_amount),
);
const incidentPageValidator = apiEnvelopeValidator(cursorPageValidator(isIncidentSummary));
const auditPageValidator = apiEnvelopeValidator(cursorPageValidator(isAuditEntry));
const policyPageValidator = apiEnvelopeValidator(cursorPageValidator(isPolicyVersion));
const reportPageValidator = apiEnvelopeValidator(cursorPageValidator(isReportVersion));
const settingsValidator = apiEnvelopeValidator(
  isMerchantSettings,
  (settings, context) =>
    settings.merchant_id === context.merchant_id &&
    settings.environment === context.environment &&
    settings.timezone === context.timezone &&
    settings.currencies.some((currency) => currency.code === context.currency.code),
);

export function getComplianceSummary(
  query: DateQuery,
): Promise<ApiResult<ApiEnvelope<ComplianceSummary>>> {
  return apiGet({
    path: "/api/v1/workspace/compliance",
    query: toApiSearchParams(query),
    validator: complianceSummaryValidator,
  });
}

export function listReviews(
  query: ReviewListQuery,
): Promise<ApiResult<ApiEnvelope<CursorPage<ReviewSummary>>>> {
  return apiGet({
    path: "/api/v1/workspace/reviews",
    query: toApiSearchParams(query),
    validator: reviewPageValidator,
  });
}

export function listIncidents(
  query: IncidentListQuery,
): Promise<ApiResult<ApiEnvelope<CursorPage<IncidentSummary>>>> {
  return apiGet({
    path: "/api/v1/workspace/incidents",
    query: toApiSearchParams(query),
    validator: incidentPageValidator,
  });
}

export function getAuditTrail(
  query: AuditQuery,
): Promise<ApiResult<ApiEnvelope<CursorPage<AuditEntry>>>> {
  return apiGet({
    path: "/api/v1/workspace/audit",
    query: toApiSearchParams(query),
    validator: auditPageValidator,
  });
}

export function listPolicies(
  query: PolicyListQuery,
): Promise<ApiResult<ApiEnvelope<CursorPage<PolicyVersion>>>> {
  return apiGet({
    path: "/api/v1/workspace/policies",
    query: toApiSearchParams(query),
    validator: policyPageValidator,
  });
}

export function listReports(
  query: ReportListQuery,
): Promise<ApiResult<ApiEnvelope<CursorPage<ReportVersion>>>> {
  return apiGet({
    path: "/api/v1/workspace/reports",
    query: toApiSearchParams(query),
    validator: reportPageValidator,
  });
}

export function getMerchantSettings(): Promise<ApiResult<ApiEnvelope<MerchantSettings>>> {
  return apiGet({
    path: "/api/v1/workspace/settings",
    validator: settingsValidator,
  });
}
