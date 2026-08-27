import "server-only";

import { apiGet, type ApiResult } from "./client";
import {
  isDashboardOverview,
  isLiveCaseDetail,
  isLiveCaseList,
  isOperationsHealth,
  type DashboardOverview,
  type LiveCaseDetail,
  type LiveCaseList,
  type OperationsHealth,
} from "./live-contracts";

export function getLiveOverview(): Promise<ApiResult<DashboardOverview>> {
  return apiGet({
    path: "/api/v1/dashboard/overview",
    validator: isDashboardOverview,
  });
}

export function getLiveCases(states: string[] = []): Promise<ApiResult<LiveCaseList>> {
  const query = new URLSearchParams({ limit: "50" });
  for (const state of states) query.append("state", state);
  return apiGet({
    path: "/api/v1/dashboard/cases",
    query,
    validator: isLiveCaseList,
  });
}

export function getLiveCase(caseId: string): Promise<ApiResult<LiveCaseDetail>> {
  const normalized = caseId.trim();
  if (
    !normalized ||
    normalized.length > 128 ||
    [...normalized].some((item) => item.charCodeAt(0) < 32)
  ) {
    return Promise.resolve({
      kind: "invalid-contract",
      message: "The recovery case reference is invalid.",
    });
  }
  return apiGet({
    path: `/api/v1/dashboard/cases/${encodeURIComponent(normalized)}`,
    validator: isLiveCaseDetail,
  });
}

export function getLiveOperationsHealth(): Promise<ApiResult<OperationsHealth>> {
  return apiGet({
    path: "/api/v1/dashboard/health",
    validator: isOperationsHealth,
  });
}
