import type { BatchStatus, CaseState, ReportStatus, ReviewStatus } from "./contracts";

export type SearchParamValue = string | string[] | undefined;
export type SearchParams = Record<string, SearchParamValue>;

export interface DateQuery {
  from?: string;
  to?: string;
  currency?: string;
}

export interface OverviewQuery extends DateQuery {
  batch?: string;
}

export interface BatchListQuery extends DateQuery {
  status?: BatchStatus;
  report_status?: ReportStatus;
  cursor?: string;
  page_size?: number;
}

export interface CaseListQuery extends DateQuery {
  state?: CaseState[];
  workflow?: string;
  batch?: string;
  cursor?: string;
  page_size?: number;
}

export interface ReviewListQuery extends DateQuery {
  status?: ReviewStatus[];
  cursor?: string;
  page_size?: number;
}

export interface IncidentListQuery extends DateQuery {
  status?: Array<"OPEN" | "MITIGATED" | "RESOLVED">;
  cursor?: string;
  page_size?: number;
}

export interface AuditQuery extends DateQuery {
  event_type?: string;
  entity_type?: string;
  correlation_id?: string;
  cursor?: string;
  page_size?: number;
}

export interface PolicyListQuery {
  status?: Array<"ACTIVE" | "SCHEDULED" | "SUPERSEDED" | "RETIRED">;
  cursor?: string;
  page_size?: number;
}

export interface ReportListQuery extends DateQuery {
  classification?: Array<"PRODUCTION" | "SYNTHETIC" | "TEST">;
  status?: Array<"GENERATING" | "READY" | "FAILED">;
  cursor?: string;
  page_size?: number;
}

const caseStates = [
  "DETECTED",
  "DIAGNOSING",
  "DECISION_PENDING",
  "POLICY_CHECK",
  "READY",
  "EXECUTING",
  "VERIFYING",
  "UNKNOWN",
  "DEFERRED",
  "ESCALATED",
  "RECOVERED",
  "STOPPED",
] as const;
const batchStatuses = ["OPEN", "CLOSED"] as const;
const reportStatuses = ["PROVISIONAL", "FINAL"] as const;
const reviewStatuses = ["REQUESTED", "APPROVED", "REJECTED", "EXPIRED"] as const;
const incidentStatuses = ["OPEN", "MITIGATED", "RESOLVED"] as const;
const policyStatuses = ["ACTIVE", "SCHEDULED", "SUPERSEDED", "RETIRED"] as const;
const classifications = ["PRODUCTION", "SYNTHETIC", "TEST"] as const;
const generationStatuses = ["GENERATING", "READY", "FAILED"] as const;

function first(value: SearchParamValue): string | undefined {
  const candidate = Array.isArray(value) ? value[0] : value;
  if (candidate === undefined) return undefined;
  const trimmed = candidate.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function all(value: SearchParamValue): string[] {
  const values = Array.isArray(value) ? value : value === undefined ? [] : [value];
  return values
    .flatMap((entry) => entry.split(","))
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

function enumValue<T extends string>(
  value: SearchParamValue,
  allowed: readonly T[],
): T | undefined {
  const candidate = first(value);
  return candidate !== undefined && allowed.includes(candidate as T) ? (candidate as T) : undefined;
}

function enumValues<T extends string>(
  value: SearchParamValue,
  allowed: readonly T[],
): T[] | undefined {
  const candidates = [...new Set(all(value))].filter((candidate): candidate is T =>
    allowed.includes(candidate as T),
  );
  return candidates.length > 0 ? candidates : undefined;
}

function isoDate(value: SearchParamValue): string | undefined {
  const candidate = first(value);
  if (candidate === undefined || !/^\d{4}-\d{2}-\d{2}$/.test(candidate)) {
    return undefined;
  }
  const parsed = new Date(`${candidate}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().startsWith(candidate)
    ? candidate
    : undefined;
}

function currency(value: SearchParamValue): string | undefined {
  const candidate = first(value)?.toUpperCase();
  return candidate !== undefined && /^[A-Z]{3}$/.test(candidate) ? candidate : undefined;
}

function opaqueToken(value: SearchParamValue): string | undefined {
  const candidate = first(value);
  return candidate !== undefined &&
    candidate.length <= 512 &&
    /^[A-Za-z0-9._~+/=-]+$/.test(candidate)
    ? candidate
    : undefined;
}

function identifier(value: SearchParamValue): string | undefined {
  const candidate = first(value);
  return candidate !== undefined &&
    candidate.length <= 160 &&
    /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(candidate)
    ? candidate
    : undefined;
}

function pageSize(value: SearchParamValue): number | undefined {
  const candidate = first(value);
  if (candidate === undefined || !/^\d{1,3}$/.test(candidate)) return undefined;
  const parsed = Number(candidate);
  return parsed >= 1 && parsed <= 200 ? parsed : undefined;
}

function plainFilter(value: SearchParamValue): string | undefined {
  const candidate = first(value);
  return candidate !== undefined &&
    candidate.length <= 100 &&
    /^[A-Za-z0-9][A-Za-z0-9 _./:-]*$/.test(candidate)
    ? candidate
    : undefined;
}

function dateQuery(searchParams: SearchParams): DateQuery {
  const from = isoDate(searchParams.from);
  const to = isoDate(searchParams.to);
  const selectedCurrency = currency(searchParams.currency);
  return {
    ...(from !== undefined && { from }),
    ...(to !== undefined && { to }),
    ...(selectedCurrency !== undefined && { currency: selectedCurrency }),
  };
}

function paginationQuery(searchParams: SearchParams): {
  cursor?: string;
  page_size?: number;
} {
  const cursor = opaqueToken(searchParams.cursor);
  const size = pageSize(searchParams.page_size);
  return {
    ...(cursor !== undefined && { cursor }),
    ...(size !== undefined && { page_size: size }),
  };
}

export function parseOverviewQuery(searchParams: SearchParams): OverviewQuery {
  const batch = identifier(searchParams.batch);
  return {
    ...dateQuery(searchParams),
    ...(batch !== undefined && { batch }),
  };
}

export function parseBatchListQuery(searchParams: SearchParams): BatchListQuery {
  const status = enumValue(searchParams.status, batchStatuses);
  const reportStatus = enumValue(searchParams.report_status, reportStatuses);
  return {
    ...dateQuery(searchParams),
    ...paginationQuery(searchParams),
    ...(status !== undefined && { status }),
    ...(reportStatus !== undefined && { report_status: reportStatus }),
  };
}

export function parseCaseListQuery(searchParams: SearchParams): CaseListQuery {
  const state = enumValues(searchParams.state, caseStates);
  const workflow = plainFilter(searchParams.workflow);
  const batch = identifier(searchParams.batch);
  return {
    ...dateQuery(searchParams),
    ...paginationQuery(searchParams),
    ...(state !== undefined && { state }),
    ...(workflow !== undefined && { workflow }),
    ...(batch !== undefined && { batch }),
  };
}

export function parseReviewListQuery(searchParams: SearchParams): ReviewListQuery {
  const status = enumValues(searchParams.status, reviewStatuses);
  return {
    ...dateQuery(searchParams),
    ...paginationQuery(searchParams),
    ...(status !== undefined && { status }),
  };
}

export function parseIncidentListQuery(searchParams: SearchParams): IncidentListQuery {
  const status = enumValues(searchParams.status, incidentStatuses);
  return {
    ...dateQuery(searchParams),
    ...paginationQuery(searchParams),
    ...(status !== undefined && { status }),
  };
}

export function parseAuditQuery(searchParams: SearchParams): AuditQuery {
  const eventType = plainFilter(searchParams.event_type);
  const entityType = plainFilter(searchParams.entity_type);
  const correlationId = identifier(searchParams.correlation_id);
  return {
    ...dateQuery(searchParams),
    ...paginationQuery(searchParams),
    ...(eventType !== undefined && { event_type: eventType }),
    ...(entityType !== undefined && { entity_type: entityType }),
    ...(correlationId !== undefined && { correlation_id: correlationId }),
  };
}

export function parsePolicyListQuery(searchParams: SearchParams): PolicyListQuery {
  const status = enumValues(searchParams.status, policyStatuses);
  return {
    ...paginationQuery(searchParams),
    ...(status !== undefined && { status }),
  };
}

export function parseReportListQuery(searchParams: SearchParams): ReportListQuery {
  const classification = enumValues(searchParams.classification, classifications);
  const status = enumValues(searchParams.status, generationStatuses);
  return {
    ...dateQuery(searchParams),
    ...paginationQuery(searchParams),
    ...(classification !== undefined && { classification }),
    ...(status !== undefined && { status }),
  };
}

export function toApiSearchParams(query: object): URLSearchParams {
  const result = new URLSearchParams();
  for (const [key, value] of Object.entries(query) as Array<[string, unknown]>) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (typeof item === "string") result.append(key, item);
      }
      continue;
    }
    if (typeof value === "string" || typeof value === "number") {
      result.set(key, String(value));
    }
  }
  return result;
}

export function encodeResourceId(value: string): string | null {
  const normalized = value.trim();
  const hasControlCharacters = [...normalized].some((character) => character.charCodeAt(0) < 32);
  return normalized.length > 0 && normalized.length <= 160 && !hasControlCharacters
    ? encodeURIComponent(normalized)
    : null;
}
