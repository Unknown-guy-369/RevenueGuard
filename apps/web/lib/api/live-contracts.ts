export type DashboardContext = {
  schema_version: "1.0";
  merchant_id: string;
  merchant_display_name: string;
  environment: "TEST";
  data_classification: "TEST";
  as_of: string;
};

export type CurrencyTotals = {
  currency: string;
  revenue_at_risk_minor: number;
  verified_recovered_minor: number;
};

export type LiveCaseSummary = {
  case_id: string;
  state: string;
  state_version: number;
  workflow_type: string;
  subject_type: string;
  subject_reference_masked: string;
  customer_reference_masked: string | null;
  revenue_at_risk_minor: number;
  currency: string;
  diagnosis: string | null;
  diagnosis_confidence_basis_points: number | null;
  retry_count: number;
  contact_count: number;
  classification: "TEST" | "SYNTHETIC";
  updated_at: string;
};

export type DashboardOverview = {
  context: DashboardContext;
  currency_totals: CurrencyTotals[];
  counts: {
    active_cases: number;
    recovered_cases: number;
    stopped_cases: number;
    unknown_cases: number;
    deferred_cases: number;
    escalated_cases: number;
    pending_reviews: number;
    pending_actions: number;
    decision_receipts: number;
    model_succeeded: number;
    model_fallback: number;
  };
  recent_cases: LiveCaseSummary[];
};

export type LiveCaseList = {
  context: DashboardContext;
  cases: LiveCaseSummary[];
  total: number;
};

export type LiveCaseDetail = {
  context: DashboardContext;
  case: LiveCaseSummary;
  transitions: Array<{
    transition_id: string;
    from_state: string;
    to_state: string;
    reason_code: string;
    reason_detail: string | null;
    actor_reference_masked: string;
    correlation_id: string;
    policy_version: string;
    authoritative_evidence_reference: string | null;
    occurred_at: string;
    case_version: number;
  }>;
  decisions: Array<{
    decision_id: string;
    selected_action_type: string;
    explanation: string;
    policy_result: string;
    policy_reason_codes: string[];
    policy_version: string;
    resulting_state: string;
    resulting_action_id: string | null;
    model_prediction_ids: string[];
    created_at: string;
  }>;
  predictions: Array<{
    prediction_id: string;
    node: string;
    status: "SUCCEEDED" | "FALLBACK";
    model_version: string;
    prompt_version: string;
    latency_ms: number;
    input_tokens: number;
    output_tokens: number;
    failure_code: string | null;
    created_at: string;
  }>;
  actions: Array<{
    action_id: string;
    action_type: string;
    target_reference_masked: string;
    logical_attempt: number;
    idempotency_key: string;
    status: string;
    attempt_count: number;
    max_attempts: number;
    policy_version: string;
    authorized_at: string;
    unknown_since: string | null;
    last_error_code: string | null;
    payment_link_url: string | null;
  }>;
  outcomes: Array<{
    outcome_id: string;
    action_id: string;
    status: string;
    is_authoritative: boolean;
    recovered_amount_minor: number;
    currency: string;
    evidence_source: string;
    evidence_reference: string | null;
    provider_reference_masked: string | null;
    reason_code: string | null;
    observed_at: string;
    verified_at: string | null;
  }>;
  reviews: Array<{
    review_id: string;
    status: string;
    proposed_action_type: string;
    reason_code: string;
    risk_detail: string;
    policy_version: string;
    requested_at: string;
    expires_at: string;
    reviewed_at: string | null;
    reviewer_reference_masked: string | null;
    rationale: string | null;
  }>;
};

export type OperationsHealth = {
  context: DashboardContext;
  status: "HEALTHY" | "DEGRADED";
  pending_events: number;
  dead_letter_events: number;
  pending_actions: number;
  unknown_actions: number;
};

type RecordValue = Record<string, unknown>;

function record(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function nullableText(value: unknown): value is string | null {
  return value === null || text(value);
}

function nullableRazorpayPaymentLink(value: unknown): value is string | null {
  if (value === null) return true;
  if (!text(value)) return false;
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      (url.hostname === "rzp.io" || url.hostname === "www.rzp.io") &&
      url.pathname.length > 1 &&
      (url.port === "" || url.port === "443")
    );
  } catch {
    return false;
  }
}

function count(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(text);
}

export function isDashboardContext(value: unknown): value is DashboardContext {
  return (
    record(value) &&
    value.schema_version === "1.0" &&
    text(value.merchant_id) &&
    text(value.merchant_display_name) &&
    value.environment === "TEST" &&
    value.data_classification === "TEST" &&
    timestamp(value.as_of)
  );
}

export function isLiveCaseSummary(value: unknown): value is LiveCaseSummary {
  return (
    record(value) &&
    text(value.case_id) &&
    text(value.state) &&
    count(value.state_version) &&
    value.state_version > 0 &&
    text(value.workflow_type) &&
    text(value.subject_type) &&
    text(value.subject_reference_masked) &&
    nullableText(value.customer_reference_masked) &&
    count(value.revenue_at_risk_minor) &&
    typeof value.currency === "string" &&
    /^[A-Z]{3}$/.test(value.currency) &&
    nullableText(value.diagnosis) &&
    (value.diagnosis_confidence_basis_points === null ||
      (count(value.diagnosis_confidence_basis_points) &&
        value.diagnosis_confidence_basis_points <= 10_000)) &&
    count(value.retry_count) &&
    count(value.contact_count) &&
    (value.classification === "TEST" || value.classification === "SYNTHETIC") &&
    timestamp(value.updated_at)
  );
}

export function isDashboardOverview(value: unknown): value is DashboardOverview {
  if (
    !record(value) ||
    !isDashboardContext(value.context) ||
    !Array.isArray(value.currency_totals) ||
    !record(value.counts) ||
    !Array.isArray(value.recent_cases)
  ) {
    return false;
  }
  const totalsValid = value.currency_totals.every(
    (item) =>
      record(item) &&
      typeof item.currency === "string" &&
      /^[A-Z]{3}$/.test(item.currency) &&
      count(item.revenue_at_risk_minor) &&
      count(item.verified_recovered_minor),
  );
  const countKeys = [
    "active_cases",
    "recovered_cases",
    "stopped_cases",
    "unknown_cases",
    "deferred_cases",
    "escalated_cases",
    "pending_reviews",
    "pending_actions",
    "decision_receipts",
    "model_succeeded",
    "model_fallback",
  ];
  const counts = value.counts;
  return (
    totalsValid &&
    countKeys.every((key) => count(counts[key])) &&
    value.recent_cases.every(isLiveCaseSummary)
  );
}

export function isLiveCaseList(value: unknown): value is LiveCaseList {
  return (
    record(value) &&
    isDashboardContext(value.context) &&
    Array.isArray(value.cases) &&
    value.cases.every(isLiveCaseSummary) &&
    count(value.total)
  );
}

export function isOperationsHealth(value: unknown): value is OperationsHealth {
  return (
    record(value) &&
    isDashboardContext(value.context) &&
    (value.status === "HEALTHY" || value.status === "DEGRADED") &&
    count(value.pending_events) &&
    count(value.dead_letter_events) &&
    count(value.pending_actions) &&
    count(value.unknown_actions)
  );
}

export function isLiveCaseDetail(value: unknown): value is LiveCaseDetail {
  if (
    !record(value) ||
    !isDashboardContext(value.context) ||
    !isLiveCaseSummary(value.case) ||
    !Array.isArray(value.transitions) ||
    !Array.isArray(value.decisions) ||
    !Array.isArray(value.predictions) ||
    !Array.isArray(value.actions) ||
    !Array.isArray(value.outcomes) ||
    !Array.isArray(value.reviews)
  ) {
    return false;
  }
  return (
    value.transitions.every(
      (item) =>
        record(item) &&
        text(item.transition_id) &&
        text(item.from_state) &&
        text(item.to_state) &&
        text(item.reason_code) &&
        nullableText(item.reason_detail) &&
        text(item.actor_reference_masked) &&
        text(item.correlation_id) &&
        text(item.policy_version) &&
        nullableText(item.authoritative_evidence_reference) &&
        timestamp(item.occurred_at) &&
        count(item.case_version),
    ) &&
    value.decisions.every(
      (item) =>
        record(item) &&
        text(item.decision_id) &&
        text(item.selected_action_type) &&
        text(item.explanation) &&
        text(item.policy_result) &&
        stringArray(item.policy_reason_codes) &&
        text(item.policy_version) &&
        text(item.resulting_state) &&
        nullableText(item.resulting_action_id) &&
        stringArray(item.model_prediction_ids) &&
        timestamp(item.created_at),
    ) &&
    value.predictions.every(
      (item) =>
        record(item) &&
        text(item.prediction_id) &&
        text(item.node) &&
        (item.status === "SUCCEEDED" || item.status === "FALLBACK") &&
        text(item.model_version) &&
        text(item.prompt_version) &&
        count(item.latency_ms) &&
        count(item.input_tokens) &&
        count(item.output_tokens) &&
        nullableText(item.failure_code) &&
        timestamp(item.created_at),
    ) &&
    value.actions.every(
      (item) =>
        record(item) &&
        text(item.action_id) &&
        text(item.action_type) &&
        text(item.target_reference_masked) &&
        count(item.logical_attempt) &&
        text(item.idempotency_key) &&
        text(item.status) &&
        count(item.attempt_count) &&
        count(item.max_attempts) &&
        text(item.policy_version) &&
        timestamp(item.authorized_at) &&
        (item.unknown_since === null || timestamp(item.unknown_since)) &&
        nullableText(item.last_error_code) &&
        nullableRazorpayPaymentLink(item.payment_link_url),
    ) &&
    value.outcomes.every(
      (item) =>
        record(item) &&
        text(item.outcome_id) &&
        text(item.action_id) &&
        text(item.status) &&
        typeof item.is_authoritative === "boolean" &&
        count(item.recovered_amount_minor) &&
        typeof item.currency === "string" &&
        /^[A-Z]{3}$/.test(item.currency) &&
        text(item.evidence_source) &&
        nullableText(item.evidence_reference) &&
        nullableText(item.provider_reference_masked) &&
        nullableText(item.reason_code) &&
        timestamp(item.observed_at) &&
        (item.verified_at === null || timestamp(item.verified_at)),
    ) &&
    value.reviews.every(
      (item) =>
        record(item) &&
        text(item.review_id) &&
        text(item.status) &&
        text(item.proposed_action_type) &&
        text(item.reason_code) &&
        text(item.risk_detail) &&
        text(item.policy_version) &&
        timestamp(item.requested_at) &&
        timestamp(item.expires_at) &&
        (item.reviewed_at === null || timestamp(item.reviewed_at)) &&
        nullableText(item.reviewer_reference_masked) &&
        nullableText(item.rationale),
    )
  );
}
