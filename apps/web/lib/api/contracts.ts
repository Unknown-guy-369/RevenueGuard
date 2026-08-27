export type Environment = "TEST" | "STAGING" | "PRODUCTION";
export type DataStatus = "AUTHORITATIVE" | "PROVISIONAL" | "DEGRADED" | "PARTIAL";
export type DataClassification = "PRODUCTION" | "SYNTHETIC" | "TEST";
export type ReportStatus = "PROVISIONAL" | "FINAL";
export type BatchStatus = "OPEN" | "CLOSED";
export type OutcomeKind =
  "RECOVERED" | "ACTIVE" | "DEFERRED" | "UNCERTAIN" | "TERMINAL_UNRECOVERED";
export type CaseState =
  | "DETECTED"
  | "DIAGNOSING"
  | "DECISION_PENDING"
  | "POLICY_CHECK"
  | "READY"
  | "EXECUTING"
  | "VERIFYING"
  | "UNKNOWN"
  | "DEFERRED"
  | "ESCALATED"
  | "RECOVERED"
  | "STOPPED";
export type ActionStatus = "PENDING" | "SUCCEEDED" | "FAILED" | "UNKNOWN";
export type ReviewStatus = "REQUESTED" | "APPROVED" | "REJECTED" | "EXPIRED";
export type EvidenceSource =
  "SIGNED_WEBHOOK" | "PROVIDER_LOOKUP" | "PROVIDER_RESPONSE" | "SIMULATOR" | "NONE";

export interface CurrencyMeta {
  code: string;
  symbol: string;
  minor_unit_exponent: number;
}

export interface Money {
  amount_minor: number;
  currency_code: string;
}

export interface DateRange {
  from: string;
  to: string;
}

export interface AuthoritativeContext {
  merchant_id: string;
  merchant_display_name: string;
  environment: Environment;
  currency: CurrencyMeta;
  date_range: DateRange;
  timezone: string;
  as_of: string;
  data_status: DataStatus;
  data_classification: DataClassification;
}

export interface ApiEnvelope<T> {
  context: AuthoritativeContext;
  result: T;
}

export interface EvidenceReference {
  reference: string;
  label: string;
  source: EvidenceSource;
  observed_at: string;
}

export interface OutcomeSegment {
  kind: OutcomeKind;
  amount: Money;
  case_count: number;
  percentage_basis_points: number;
  width_basis_points: number;
  evidence: EvidenceReference[];
  population_href: string;
}

export interface ExcludedPopulation {
  amount: Money;
  case_count: number;
  reason: string;
  population_href: string;
}

export interface OutcomeIdentity {
  eligible_at_risk: Money;
  segments: OutcomeSegment[];
  excluded: ExcludedPopulation;
  report_status: ReportStatus;
  batch_status: BatchStatus;
  identity_status: "VERIFIED" | "UNVERIFIED";
  identity_evidence: EvidenceReference[];
}

export interface StrategySummary {
  strategy_id: string;
  strategy_name: string;
  classification: DataClassification;
  eligible_at_risk: Money;
  verified_recovered: Money;
  recovery_rate_basis_points: number;
  confidence_interval_basis_points: {
    lower: number;
    upper: number;
  } | null;
  cost: Money | null;
  net_recovered: Money | null;
  report_version: string;
}

export interface AttentionSummary {
  unknown_cases: number;
  deferred_cases: number;
  escalated_cases: number;
  pending_reviews: number;
  open_incidents: number;
}

export interface IntegritySummary {
  duplicate_effects_prevented: number;
  unverified_money_counted_minor: number;
  currency_code: string;
}

export interface MerchantOverview {
  selected_batch: BatchSummary | null;
  outcome_identity: OutcomeIdentity | null;
  attention: AttentionSummary;
  integrity: IntegritySummary;
  recent_strategies: StrategySummary[];
}

export interface BatchSummary {
  batch_id: string;
  name: string;
  status: BatchStatus;
  report_status: ReportStatus;
  report_version: string;
  manifest_digest: string;
  opened_at: string;
  closed_at: string | null;
  eligible_at_risk: Money;
  case_count: number;
}

export interface BatchAdjustment {
  adjustment_id: string;
  reason: string;
  amount: Money;
  case_count_delta: number;
  evidence: EvidenceReference[];
  recorded_at: string;
}

export interface BatchReport {
  batch: BatchSummary;
  outcome_identity: OutcomeIdentity;
  adjustments: BatchAdjustment[];
  strategies: StrategySummary[];
  generated_at: string;
}

export interface DiagnosisSummary {
  code: string;
  summary: string;
  confidence_basis_points: number | null;
  evidence: EvidenceReference[];
}

export interface CaseSummary {
  case_id: string;
  version: number;
  state: CaseState;
  workflow: string;
  subject_reference_masked: string;
  customer_reference_masked: string | null;
  exposure: Money;
  detected_at: string;
  updated_at: string;
  batch_id: string | null;
}

export interface CaseTransition {
  transition_id: string;
  from_state: CaseState | null;
  to_state: CaseState;
  reason: string;
  actor_reference_masked: string;
  authoritative_evidence_reference: string | null;
  correlation_id: string;
  occurred_at: string;
  case_version: number;
}

export interface CaseAction {
  action_id: string;
  action_type: string;
  target_reference_masked: string;
  logical_attempt: number;
  idempotency_key: string;
  status: ActionStatus;
  policy_digest: string;
  action_fingerprint: string;
  requested_at: string;
  completed_at: string | null;
  correlation_id: string;
}

export interface CaseOutcome {
  outcome_id: string;
  action_id: string;
  status: ActionStatus;
  is_authoritative: boolean;
  recovered: Money;
  evidence_source: EvidenceSource;
  evidence_reference: string | null;
  verified_at: string | null;
  provider_reference_masked: string | null;
}

export interface ReviewEvidence {
  review_id: string;
  status: ReviewStatus;
  action_fingerprint: string;
  policy_digest: string;
  policy_version: string;
  requested_at: string;
  expires_at: string;
  reviewed_at: string | null;
  reviewer_reference_masked: string | null;
  rationale: string | null;
  evidence: EvidenceReference[];
}

export interface CaseDetail {
  case: CaseSummary;
  diagnosis: DiagnosisSummary | null;
  transitions: CaseTransition[];
  actions: CaseAction[];
  outcomes: CaseOutcome[];
  reviews: ReviewEvidence[];
}

export interface ReviewSummary extends ReviewEvidence {
  case_id: string;
  proposed_action_type: string;
  proposed_amount: Money | null;
}

export interface IncidentSummary {
  incident_id: string;
  title: string;
  status: "OPEN" | "MITIGATED" | "RESOLVED";
  affected_scope: string;
  affected_case_count: number;
  defer_status: "NOT_DEFERRED" | "DEFERRED" | "RELEASED";
  evidence: EvidenceReference[];
  opened_at: string;
  resolved_at: string | null;
}

export interface AuditEntry {
  entry_id: string;
  event_type: string;
  actor_reference_masked: string;
  reason: string;
  correlation_id: string;
  entity_type: string;
  entity_reference: string;
  entity_version: number;
  previous_entry_digest: string | null;
  entry_digest: string;
  evidence: EvidenceReference[];
  occurred_at: string;
}

export interface PolicyVersion {
  policy_id: string;
  name: string;
  version: string;
  digest: string;
  status: "ACTIVE" | "SCHEDULED" | "SUPERSEDED" | "RETIRED";
  effective_from: string;
  effective_until: string | null;
  approved_by_reference_masked: string;
}

export interface ReportVersion {
  report_id: string;
  report_type: string;
  version: string;
  classification: DataClassification;
  status: "GENERATING" | "READY" | "FAILED";
  report_status: ReportStatus;
  generated_at: string | null;
  evidence: EvidenceReference[];
  adjustment_count: number;
}

export interface MerchantSettings {
  merchant_id: string;
  merchant_display_name: string;
  environment: Environment;
  timezone: string;
  currencies: CurrencyMeta[];
  role: string;
  data_access_summary: string;
  sensitive_data_masking: "ENFORCED";
}

export interface ComplianceSummary {
  pending_reviews: number;
  open_incidents: number;
  active_policy_version: string | null;
  latest_audit_entry_at: string | null;
}

export interface OperationsHealth {
  status: "HEALTHY" | "DEGRADED" | "UNAVAILABLE";
  case_processing: "HEALTHY" | "DEGRADED" | "UNAVAILABLE";
  verification: "HEALTHY" | "DEGRADED" | "UNAVAILABLE";
  stream_available: boolean;
  as_of: string;
}

export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  previous_cursor: string | null;
  page_size: number;
}

export interface StreamHint {
  entity_type: "CASE" | "BATCH" | "REVIEW" | "INCIDENT" | "POLICY" | "REPORT";
  entity_id: string;
  version: number;
}

export interface HealthStatus {
  status: "ok";
  service: string;
  version: string;
}

export type ContractValidator<T> = (value: unknown) => value is T;

type UnknownRecord = Record<string, unknown>;

const environments = ["TEST", "STAGING", "PRODUCTION"] as const;
const dataStatuses = ["AUTHORITATIVE", "PROVISIONAL", "DEGRADED", "PARTIAL"] as const;
const dataClassifications = ["PRODUCTION", "SYNTHETIC", "TEST"] as const;
const reportStatuses = ["PROVISIONAL", "FINAL"] as const;
const batchStatuses = ["OPEN", "CLOSED"] as const;
const outcomeKinds = [
  "RECOVERED",
  "ACTIVE",
  "DEFERRED",
  "UNCERTAIN",
  "TERMINAL_UNRECOVERED",
] as const;
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
const actionStatuses = ["PENDING", "SUCCEEDED", "FAILED", "UNKNOWN"] as const;
const reviewStatuses = ["REQUESTED", "APPROVED", "REJECTED", "EXPIRED"] as const;
const evidenceSources = [
  "SIGNED_WEBHOOK",
  "PROVIDER_LOOKUP",
  "PROVIDER_RESPONSE",
  "SIMULATOR",
  "NONE",
] as const;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasKeys(
  value: UnknownRecord,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.hasOwn(value, key)) &&
    Object.keys(value).every((key) => allowed.has(key))
  );
}

function isEnum<T extends string>(value: unknown, choices: readonly T[]): value is T {
  return typeof value === "string" && choices.includes(value as T);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isNullable<T>(value: unknown, validator: ContractValidator<T>): value is T | null {
  return value === null || validator(value);
}

function isArrayOf<T>(value: unknown, validator: ContractValidator<T>): value is T[] {
  return Array.isArray(value) && value.every(validator);
}

function isSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return isSafeInteger(value) && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return isSafeInteger(value) && value > 0;
}

function isBasisPoints(value: unknown): value is number {
  return isNonNegativeInteger(value) && value <= 10_000;
}

function isIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().startsWith(value);
}

function isCurrencyCode(value: unknown): value is string {
  return typeof value === "string" && /^[A-Z]{3}$/.test(value);
}

function hasControlCharacters(value: string): boolean {
  return [...value].some((character) => character.charCodeAt(0) < 32);
}

function isInternalHref(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !hasControlCharacters(value)
  );
}

function sameCurrency(money: Money, currencyCode: string): boolean {
  return money.currency_code === currencyCode;
}

export function isCurrencyMeta(value: unknown): value is CurrencyMeta {
  return (
    isRecord(value) &&
    hasKeys(value, ["code", "symbol", "minor_unit_exponent"]) &&
    isCurrencyCode(value.code) &&
    typeof value.symbol === "string" &&
    value.symbol.length <= 8 &&
    isNonNegativeInteger(value.minor_unit_exponent) &&
    value.minor_unit_exponent <= 6
  );
}

export function isMoney(value: unknown): value is Money {
  return (
    isRecord(value) &&
    hasKeys(value, ["amount_minor", "currency_code"]) &&
    isSafeInteger(value.amount_minor) &&
    isCurrencyCode(value.currency_code)
  );
}

function isNonNegativeMoney(value: unknown): value is Money {
  return isMoney(value) && value.amount_minor >= 0;
}

export function isDateRange(value: unknown): value is DateRange {
  return (
    isRecord(value) &&
    hasKeys(value, ["from", "to"]) &&
    isIsoDate(value.from) &&
    isIsoDate(value.to) &&
    value.from <= value.to
  );
}

export function isAuthoritativeContext(value: unknown): value is AuthoritativeContext {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "merchant_id",
      "merchant_display_name",
      "environment",
      "currency",
      "date_range",
      "timezone",
      "as_of",
      "data_status",
      "data_classification",
    ]) &&
    isNonEmptyString(value.merchant_id) &&
    isNonEmptyString(value.merchant_display_name) &&
    isEnum(value.environment, environments) &&
    isCurrencyMeta(value.currency) &&
    isDateRange(value.date_range) &&
    isNonEmptyString(value.timezone) &&
    isIsoTimestamp(value.as_of) &&
    isEnum(value.data_status, dataStatuses) &&
    isEnum(value.data_classification, dataClassifications)
  );
}

export function isEvidenceReference(value: unknown): value is EvidenceReference {
  return (
    isRecord(value) &&
    hasKeys(value, ["reference", "label", "source", "observed_at"]) &&
    isNonEmptyString(value.reference) &&
    isNonEmptyString(value.label) &&
    isEnum(value.source, evidenceSources) &&
    isIsoTimestamp(value.observed_at)
  );
}

export function isOutcomeSegment(value: unknown): value is OutcomeSegment {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "kind",
      "amount",
      "case_count",
      "percentage_basis_points",
      "width_basis_points",
      "evidence",
      "population_href",
    ]) &&
    isEnum(value.kind, outcomeKinds) &&
    isNonNegativeMoney(value.amount) &&
    isNonNegativeInteger(value.case_count) &&
    isBasisPoints(value.percentage_basis_points) &&
    isBasisPoints(value.width_basis_points) &&
    isArrayOf(value.evidence, isEvidenceReference) &&
    isInternalHref(value.population_href)
  );
}

export function isExcludedPopulation(value: unknown): value is ExcludedPopulation {
  return (
    isRecord(value) &&
    hasKeys(value, ["amount", "case_count", "reason", "population_href"]) &&
    isNonNegativeMoney(value.amount) &&
    isNonNegativeInteger(value.case_count) &&
    isNonEmptyString(value.reason) &&
    isInternalHref(value.population_href)
  );
}

export function isOutcomeIdentity(value: unknown): value is OutcomeIdentity {
  if (
    !isRecord(value) ||
    !hasKeys(value, [
      "eligible_at_risk",
      "segments",
      "excluded",
      "report_status",
      "batch_status",
      "identity_status",
      "identity_evidence",
    ]) ||
    !isNonNegativeMoney(value.eligible_at_risk) ||
    !isArrayOf(value.segments, isOutcomeSegment) ||
    !isExcludedPopulation(value.excluded) ||
    !isEnum(value.report_status, reportStatuses) ||
    !isEnum(value.batch_status, batchStatuses) ||
    !isEnum(value.identity_status, ["VERIFIED", "UNVERIFIED"] as const) ||
    !isArrayOf(value.identity_evidence, isEvidenceReference)
  ) {
    return false;
  }

  const currencyCode = value.eligible_at_risk.currency_code;
  const segmentKinds = new Set(value.segments.map((segment) => segment.kind));
  return (
    value.segments.length === outcomeKinds.length &&
    outcomeKinds.every((kind) => segmentKinds.has(kind)) &&
    value.segments.every((segment) => sameCurrency(segment.amount, currencyCode)) &&
    sameCurrency(value.excluded.amount, currencyCode) &&
    (value.identity_status !== "VERIFIED" || value.identity_evidence.length > 0) &&
    (value.report_status !== "FINAL" || value.batch_status === "CLOSED")
  );
}

export function isStrategySummary(value: unknown): value is StrategySummary {
  if (
    !isRecord(value) ||
    !hasKeys(value, [
      "strategy_id",
      "strategy_name",
      "classification",
      "eligible_at_risk",
      "verified_recovered",
      "recovery_rate_basis_points",
      "confidence_interval_basis_points",
      "cost",
      "net_recovered",
      "report_version",
    ]) ||
    !isNonEmptyString(value.strategy_id) ||
    !isNonEmptyString(value.strategy_name) ||
    !isEnum(value.classification, dataClassifications) ||
    !isNonNegativeMoney(value.eligible_at_risk) ||
    !isNonNegativeMoney(value.verified_recovered) ||
    !isBasisPoints(value.recovery_rate_basis_points) ||
    !isNullable(value.cost, isNonNegativeMoney) ||
    !isNullable(value.net_recovered, isMoney) ||
    !isNonEmptyString(value.report_version)
  ) {
    return false;
  }

  const interval = value.confidence_interval_basis_points;
  const validInterval =
    interval === null ||
    (isRecord(interval) &&
      hasKeys(interval, ["lower", "upper"]) &&
      isBasisPoints(interval.lower) &&
      isBasisPoints(interval.upper) &&
      interval.lower <= interval.upper);
  const currencyCode = value.eligible_at_risk.currency_code;
  return (
    validInterval &&
    sameCurrency(value.verified_recovered, currencyCode) &&
    (value.cost === null || sameCurrency(value.cost, currencyCode)) &&
    (value.net_recovered === null || sameCurrency(value.net_recovered, currencyCode))
  );
}

export function isAttentionSummary(value: unknown): value is AttentionSummary {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "unknown_cases",
      "deferred_cases",
      "escalated_cases",
      "pending_reviews",
      "open_incidents",
    ]) &&
    isNonNegativeInteger(value.unknown_cases) &&
    isNonNegativeInteger(value.deferred_cases) &&
    isNonNegativeInteger(value.escalated_cases) &&
    isNonNegativeInteger(value.pending_reviews) &&
    isNonNegativeInteger(value.open_incidents)
  );
}

export function isIntegritySummary(value: unknown): value is IntegritySummary {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "duplicate_effects_prevented",
      "unverified_money_counted_minor",
      "currency_code",
    ]) &&
    isNonNegativeInteger(value.duplicate_effects_prevented) &&
    isNonNegativeInteger(value.unverified_money_counted_minor) &&
    isCurrencyCode(value.currency_code)
  );
}

export function isBatchSummary(value: unknown): value is BatchSummary {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "batch_id",
      "name",
      "status",
      "report_status",
      "report_version",
      "manifest_digest",
      "opened_at",
      "closed_at",
      "eligible_at_risk",
      "case_count",
    ]) &&
    isNonEmptyString(value.batch_id) &&
    isNonEmptyString(value.name) &&
    isEnum(value.status, batchStatuses) &&
    isEnum(value.report_status, reportStatuses) &&
    isNonEmptyString(value.report_version) &&
    isNonEmptyString(value.manifest_digest) &&
    isIsoTimestamp(value.opened_at) &&
    isNullable(value.closed_at, isIsoTimestamp) &&
    isNonNegativeMoney(value.eligible_at_risk) &&
    isNonNegativeInteger(value.case_count) &&
    (value.status !== "CLOSED" || value.closed_at !== null) &&
    (value.report_status !== "FINAL" || value.status === "CLOSED")
  );
}

export function isMerchantOverview(value: unknown): value is MerchantOverview {
  if (
    !isRecord(value) ||
    !hasKeys(value, [
      "selected_batch",
      "outcome_identity",
      "attention",
      "integrity",
      "recent_strategies",
    ]) ||
    !isNullable(value.selected_batch, isBatchSummary) ||
    !isNullable(value.outcome_identity, isOutcomeIdentity) ||
    !isAttentionSummary(value.attention) ||
    !isIntegritySummary(value.integrity) ||
    !isArrayOf(value.recent_strategies, isStrategySummary)
  ) {
    return false;
  }

  const currencyCodes = [
    value.selected_batch?.eligible_at_risk.currency_code,
    value.outcome_identity?.eligible_at_risk.currency_code,
    value.integrity.currency_code,
    ...value.recent_strategies.map((strategy) => strategy.eligible_at_risk.currency_code),
  ].filter((code): code is string => code !== undefined);
  return new Set(currencyCodes).size <= 1;
}

export function isBatchAdjustment(value: unknown): value is BatchAdjustment {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "adjustment_id",
      "reason",
      "amount",
      "case_count_delta",
      "evidence",
      "recorded_at",
    ]) &&
    isNonEmptyString(value.adjustment_id) &&
    isNonEmptyString(value.reason) &&
    isMoney(value.amount) &&
    isSafeInteger(value.case_count_delta) &&
    isArrayOf(value.evidence, isEvidenceReference) &&
    isIsoTimestamp(value.recorded_at)
  );
}

export function isBatchReport(value: unknown): value is BatchReport {
  if (
    !isRecord(value) ||
    !hasKeys(value, ["batch", "outcome_identity", "adjustments", "strategies", "generated_at"]) ||
    !isBatchSummary(value.batch) ||
    !isOutcomeIdentity(value.outcome_identity) ||
    !isArrayOf(value.adjustments, isBatchAdjustment) ||
    !isArrayOf(value.strategies, isStrategySummary) ||
    !isIsoTimestamp(value.generated_at)
  ) {
    return false;
  }

  const currencyCode = value.batch.eligible_at_risk.currency_code;
  return (
    value.outcome_identity.batch_status === value.batch.status &&
    value.outcome_identity.report_status === value.batch.report_status &&
    value.outcome_identity.eligible_at_risk.amount_minor ===
      value.batch.eligible_at_risk.amount_minor &&
    sameCurrency(value.outcome_identity.eligible_at_risk, currencyCode) &&
    value.adjustments.every((adjustment) => sameCurrency(adjustment.amount, currencyCode)) &&
    value.strategies.every((strategy) => sameCurrency(strategy.eligible_at_risk, currencyCode))
  );
}

export function isDiagnosisSummary(value: unknown): value is DiagnosisSummary {
  return (
    isRecord(value) &&
    hasKeys(value, ["code", "summary", "confidence_basis_points", "evidence"]) &&
    isNonEmptyString(value.code) &&
    isNonEmptyString(value.summary) &&
    isNullable(value.confidence_basis_points, isBasisPoints) &&
    isArrayOf(value.evidence, isEvidenceReference)
  );
}

export function isCaseSummary(value: unknown): value is CaseSummary {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "case_id",
      "version",
      "state",
      "workflow",
      "subject_reference_masked",
      "customer_reference_masked",
      "exposure",
      "detected_at",
      "updated_at",
      "batch_id",
    ]) &&
    isNonEmptyString(value.case_id) &&
    isPositiveInteger(value.version) &&
    isEnum(value.state, caseStates) &&
    isNonEmptyString(value.workflow) &&
    isNonEmptyString(value.subject_reference_masked) &&
    isNullable(value.customer_reference_masked, isNonEmptyString) &&
    isNonNegativeMoney(value.exposure) &&
    isIsoTimestamp(value.detected_at) &&
    isIsoTimestamp(value.updated_at) &&
    isNullable(value.batch_id, isNonEmptyString)
  );
}

export function isCaseTransition(value: unknown): value is CaseTransition {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "transition_id",
      "from_state",
      "to_state",
      "reason",
      "actor_reference_masked",
      "authoritative_evidence_reference",
      "correlation_id",
      "occurred_at",
      "case_version",
    ]) &&
    isNonEmptyString(value.transition_id) &&
    isNullable(value.from_state, (state): state is CaseState => isEnum(state, caseStates)) &&
    isEnum(value.to_state, caseStates) &&
    isNonEmptyString(value.reason) &&
    isNonEmptyString(value.actor_reference_masked) &&
    isNullable(value.authoritative_evidence_reference, isNonEmptyString) &&
    isNonEmptyString(value.correlation_id) &&
    isIsoTimestamp(value.occurred_at) &&
    isPositiveInteger(value.case_version) &&
    (value.to_state !== "RECOVERED" || value.authoritative_evidence_reference !== null)
  );
}

export function isCaseAction(value: unknown): value is CaseAction {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "action_id",
      "action_type",
      "target_reference_masked",
      "logical_attempt",
      "idempotency_key",
      "status",
      "policy_digest",
      "action_fingerprint",
      "requested_at",
      "completed_at",
      "correlation_id",
    ]) &&
    isNonEmptyString(value.action_id) &&
    isNonEmptyString(value.action_type) &&
    isNonEmptyString(value.target_reference_masked) &&
    isPositiveInteger(value.logical_attempt) &&
    isNonEmptyString(value.idempotency_key) &&
    isEnum(value.status, actionStatuses) &&
    isNonEmptyString(value.policy_digest) &&
    isNonEmptyString(value.action_fingerprint) &&
    isIsoTimestamp(value.requested_at) &&
    isNullable(value.completed_at, isIsoTimestamp) &&
    isNonEmptyString(value.correlation_id)
  );
}

export function isCaseOutcome(value: unknown): value is CaseOutcome {
  if (
    !isRecord(value) ||
    !hasKeys(value, [
      "outcome_id",
      "action_id",
      "status",
      "is_authoritative",
      "recovered",
      "evidence_source",
      "evidence_reference",
      "verified_at",
      "provider_reference_masked",
    ]) ||
    !isNonEmptyString(value.outcome_id) ||
    !isNonEmptyString(value.action_id) ||
    !isEnum(value.status, actionStatuses) ||
    typeof value.is_authoritative !== "boolean" ||
    !isNonNegativeMoney(value.recovered) ||
    !isEnum(value.evidence_source, evidenceSources) ||
    !isNullable(value.evidence_reference, isNonEmptyString) ||
    !isNullable(value.verified_at, isIsoTimestamp) ||
    !isNullable(value.provider_reference_masked, isNonEmptyString)
  ) {
    return false;
  }

  if (value.status === "UNKNOWN") {
    return (
      !value.is_authoritative && value.recovered.amount_minor === 0 && value.verified_at === null
    );
  }
  if (value.recovered.amount_minor > 0) {
    return (
      value.status === "SUCCEEDED" &&
      value.is_authoritative &&
      value.evidence_reference !== null &&
      value.verified_at !== null &&
      value.evidence_source !== "NONE"
    );
  }
  return true;
}

export function isReviewEvidence(value: unknown): value is ReviewEvidence {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "review_id",
      "status",
      "action_fingerprint",
      "policy_digest",
      "policy_version",
      "requested_at",
      "expires_at",
      "reviewed_at",
      "reviewer_reference_masked",
      "rationale",
      "evidence",
    ]) &&
    isNonEmptyString(value.review_id) &&
    isEnum(value.status, reviewStatuses) &&
    isNonEmptyString(value.action_fingerprint) &&
    isNonEmptyString(value.policy_digest) &&
    isNonEmptyString(value.policy_version) &&
    isIsoTimestamp(value.requested_at) &&
    isIsoTimestamp(value.expires_at) &&
    isNullable(value.reviewed_at, isIsoTimestamp) &&
    isNullable(value.reviewer_reference_masked, isNonEmptyString) &&
    isNullable(value.rationale, isNonEmptyString) &&
    isArrayOf(value.evidence, isEvidenceReference)
  );
}

export function isCaseDetail(value: unknown): value is CaseDetail {
  if (
    !isRecord(value) ||
    !hasKeys(value, ["case", "diagnosis", "transitions", "actions", "outcomes", "reviews"]) ||
    !isCaseSummary(value.case) ||
    !isNullable(value.diagnosis, isDiagnosisSummary) ||
    !isArrayOf(value.transitions, isCaseTransition) ||
    !isArrayOf(value.actions, isCaseAction) ||
    !isArrayOf(value.outcomes, isCaseOutcome) ||
    !isArrayOf(value.reviews, isReviewEvidence)
  ) {
    return false;
  }

  const currencyCode = value.case.exposure.currency_code;
  return value.outcomes.every((outcome) => sameCurrency(outcome.recovered, currencyCode));
}

export function isReviewSummary(value: unknown): value is ReviewSummary {
  return (
    isRecord(value) &&
    isReviewEvidenceRecord(value) &&
    hasKeys(value, [
      "review_id",
      "status",
      "action_fingerprint",
      "policy_digest",
      "policy_version",
      "requested_at",
      "expires_at",
      "reviewed_at",
      "reviewer_reference_masked",
      "rationale",
      "evidence",
      "case_id",
      "proposed_action_type",
      "proposed_amount",
    ]) &&
    isNonEmptyString(value.case_id) &&
    isNonEmptyString(value.proposed_action_type) &&
    isNullable(value.proposed_amount, isNonNegativeMoney)
  );
}

function isReviewEvidenceRecord(value: UnknownRecord): boolean {
  const reviewOnly: UnknownRecord = {
    review_id: value.review_id,
    status: value.status,
    action_fingerprint: value.action_fingerprint,
    policy_digest: value.policy_digest,
    policy_version: value.policy_version,
    requested_at: value.requested_at,
    expires_at: value.expires_at,
    reviewed_at: value.reviewed_at,
    reviewer_reference_masked: value.reviewer_reference_masked,
    rationale: value.rationale,
    evidence: value.evidence,
  };
  return isReviewEvidence(reviewOnly);
}

export function isIncidentSummary(value: unknown): value is IncidentSummary {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "incident_id",
      "title",
      "status",
      "affected_scope",
      "affected_case_count",
      "defer_status",
      "evidence",
      "opened_at",
      "resolved_at",
    ]) &&
    isNonEmptyString(value.incident_id) &&
    isNonEmptyString(value.title) &&
    isEnum(value.status, ["OPEN", "MITIGATED", "RESOLVED"] as const) &&
    isNonEmptyString(value.affected_scope) &&
    isNonNegativeInteger(value.affected_case_count) &&
    isEnum(value.defer_status, ["NOT_DEFERRED", "DEFERRED", "RELEASED"] as const) &&
    isArrayOf(value.evidence, isEvidenceReference) &&
    isIsoTimestamp(value.opened_at) &&
    isNullable(value.resolved_at, isIsoTimestamp)
  );
}

export function isAuditEntry(value: unknown): value is AuditEntry {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "entry_id",
      "event_type",
      "actor_reference_masked",
      "reason",
      "correlation_id",
      "entity_type",
      "entity_reference",
      "entity_version",
      "previous_entry_digest",
      "entry_digest",
      "evidence",
      "occurred_at",
    ]) &&
    isNonEmptyString(value.entry_id) &&
    isNonEmptyString(value.event_type) &&
    isNonEmptyString(value.actor_reference_masked) &&
    isNonEmptyString(value.reason) &&
    isNonEmptyString(value.correlation_id) &&
    isNonEmptyString(value.entity_type) &&
    isNonEmptyString(value.entity_reference) &&
    isPositiveInteger(value.entity_version) &&
    isNullable(value.previous_entry_digest, isNonEmptyString) &&
    isNonEmptyString(value.entry_digest) &&
    isArrayOf(value.evidence, isEvidenceReference) &&
    isIsoTimestamp(value.occurred_at)
  );
}

export function isPolicyVersion(value: unknown): value is PolicyVersion {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "policy_id",
      "name",
      "version",
      "digest",
      "status",
      "effective_from",
      "effective_until",
      "approved_by_reference_masked",
    ]) &&
    isNonEmptyString(value.policy_id) &&
    isNonEmptyString(value.name) &&
    isNonEmptyString(value.version) &&
    isNonEmptyString(value.digest) &&
    isEnum(value.status, ["ACTIVE", "SCHEDULED", "SUPERSEDED", "RETIRED"] as const) &&
    isIsoTimestamp(value.effective_from) &&
    isNullable(value.effective_until, isIsoTimestamp) &&
    isNonEmptyString(value.approved_by_reference_masked)
  );
}

export function isReportVersion(value: unknown): value is ReportVersion {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "report_id",
      "report_type",
      "version",
      "classification",
      "status",
      "report_status",
      "generated_at",
      "evidence",
      "adjustment_count",
    ]) &&
    isNonEmptyString(value.report_id) &&
    isNonEmptyString(value.report_type) &&
    isNonEmptyString(value.version) &&
    isEnum(value.classification, dataClassifications) &&
    isEnum(value.status, ["GENERATING", "READY", "FAILED"] as const) &&
    isEnum(value.report_status, reportStatuses) &&
    isNullable(value.generated_at, isIsoTimestamp) &&
    isArrayOf(value.evidence, isEvidenceReference) &&
    isNonNegativeInteger(value.adjustment_count)
  );
}

export function isMerchantSettings(value: unknown): value is MerchantSettings {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "merchant_id",
      "merchant_display_name",
      "environment",
      "timezone",
      "currencies",
      "role",
      "data_access_summary",
      "sensitive_data_masking",
    ]) &&
    isNonEmptyString(value.merchant_id) &&
    isNonEmptyString(value.merchant_display_name) &&
    isEnum(value.environment, environments) &&
    isNonEmptyString(value.timezone) &&
    isArrayOf(value.currencies, isCurrencyMeta) &&
    value.currencies.length > 0 &&
    new Set(value.currencies.map((currency) => currency.code)).size === value.currencies.length &&
    isNonEmptyString(value.role) &&
    isNonEmptyString(value.data_access_summary) &&
    value.sensitive_data_masking === "ENFORCED"
  );
}

export function isComplianceSummary(value: unknown): value is ComplianceSummary {
  return (
    isRecord(value) &&
    hasKeys(value, [
      "pending_reviews",
      "open_incidents",
      "active_policy_version",
      "latest_audit_entry_at",
    ]) &&
    isNonNegativeInteger(value.pending_reviews) &&
    isNonNegativeInteger(value.open_incidents) &&
    isNullable(value.active_policy_version, isNonEmptyString) &&
    isNullable(value.latest_audit_entry_at, isIsoTimestamp)
  );
}

export function isOperationsHealth(value: unknown): value is OperationsHealth {
  const healthStates = ["HEALTHY", "DEGRADED", "UNAVAILABLE"] as const;
  return (
    isRecord(value) &&
    hasKeys(value, ["status", "case_processing", "verification", "stream_available", "as_of"]) &&
    isEnum(value.status, healthStates) &&
    isEnum(value.case_processing, healthStates) &&
    isEnum(value.verification, healthStates) &&
    typeof value.stream_available === "boolean" &&
    isIsoTimestamp(value.as_of)
  );
}

export function cursorPageValidator<T>(
  itemValidator: ContractValidator<T>,
): ContractValidator<CursorPage<T>> {
  return (value: unknown): value is CursorPage<T> =>
    isRecord(value) &&
    hasKeys(value, ["items", "next_cursor", "previous_cursor", "page_size"]) &&
    isArrayOf(value.items, itemValidator) &&
    isNullable(value.next_cursor, isNonEmptyString) &&
    isNullable(value.previous_cursor, isNonEmptyString) &&
    isPositiveInteger(value.page_size) &&
    value.page_size <= 200;
}

export function isStreamHint(value: unknown): value is StreamHint {
  return (
    isRecord(value) &&
    hasKeys(value, ["entity_type", "entity_id", "version"]) &&
    isEnum(value.entity_type, [
      "CASE",
      "BATCH",
      "REVIEW",
      "INCIDENT",
      "POLICY",
      "REPORT",
    ] as const) &&
    isNonEmptyString(value.entity_id) &&
    isPositiveInteger(value.version)
  );
}

export function isHealthStatus(value: unknown): value is HealthStatus {
  return (
    isRecord(value) &&
    hasKeys(value, ["status", "service", "version"]) &&
    value.status === "ok" &&
    isNonEmptyString(value.service) &&
    isNonEmptyString(value.version)
  );
}

export type EnvelopeConsistency<T> = (result: T, context: AuthoritativeContext) => boolean;

export function moneyMatchesContext(money: Money, context: AuthoritativeContext): boolean {
  return sameCurrency(money, context.currency.code);
}

export function moneyItemsMatchContext<T>(
  selectMoney: (item: T) => Money | null,
): EnvelopeConsistency<CursorPage<T>> {
  return (page, context) =>
    page.items.every((item) => {
      const money = selectMoney(item);
      return money === null || moneyMatchesContext(money, context);
    });
}

export function apiEnvelopeValidator<T>(
  resultValidator: ContractValidator<T>,
  consistencyValidator?: EnvelopeConsistency<T>,
): ContractValidator<ApiEnvelope<T>> {
  return (value: unknown): value is ApiEnvelope<T> => {
    if (
      !isRecord(value) ||
      !hasKeys(value, ["context", "result"]) ||
      !isAuthoritativeContext(value.context) ||
      !resultValidator(value.result)
    ) {
      return false;
    }

    return consistencyValidator?.(value.result, value.context) ?? true;
  };
}
