export type DashboardContext = {
  schema_version: "1.0";
  merchant_id: string;
  merchant_display_name: string;
  environment: "TEST";
  data_classification: "TEST";
  as_of: string;
};

export type BusinessCurrencyTotals = {
  currency: string;
  gross_volume_minor: number;
  collected_minor: number;
  failed_value_minor: number;
  verified_recovered_minor: number;
  payment_count: number;
  successful_payment_count: number;
  failed_payment_count: number;
  success_rate_basis_points: number;
};

export type BusinessOverview = {
  context: DashboardContext;
  since: string;
  currency_totals: BusinessCurrencyTotals[];
  payment_methods: Array<{
    payment_method: string;
    payment_count: number;
    share_basis_points: number;
  }>;
  settlement_data_available: false;
};

export type RevenueSeries = {
  context: DashboardContext;
  since: string;
  points: Array<{
    occurred_on: string;
    currency: string;
    collected_minor: number;
    failed_minor: number;
    verified_recovered_minor: number;
  }>;
};

export type PaymentSummary = {
  payment_id: string;
  provider_reference_masked: string;
  customer_reference_masked: string | null;
  amount_minor: number;
  currency: string;
  status: string;
  payment_method: string | null;
  failure_category: string | null;
  recovery_case_id: string | null;
  recovery_state: string | null;
  classification: "TEST" | "SYNTHETIC";
  occurred_at: string;
};

export type PaymentList = {
  context: DashboardContext;
  payments: PaymentSummary[];
  total: number;
  limit: number;
  offset: number;
};

export type PaymentDetail = {
  context: DashboardContext;
  payment: PaymentSummary;
  order_reference_masked: string | null;
  diagnosis: string | null;
  next_evaluation_at: string | null;
  updated_at: string;
};

export type RecoveryOverview = {
  context: DashboardContext;
  currency_totals: Array<{
    currency: string;
    revenue_at_risk_minor: number;
    verified_gross_recovered_minor: number;
    recovery_cost_minor: number | null;
    verified_net_recovered_minor: number | null;
  }>;
  active_cases: number;
  deferred_cases: number;
  unknown_cases: number;
  pending_reviews: number;
  active_incidents: number;
  cost_data_available: false;
};

export type IncidentList = {
  context: DashboardContext;
  incidents: Array<{
    incident_id: string;
    status: string;
    payment_method: string | null;
    issuer_family: string | null;
    error_family: string | null;
    baseline_failure_rate_basis_points: number;
    current_failure_rate_basis_points: number;
    affected_payments: number;
    paused_cases: number;
    healthy_windows: number;
    threshold_version: string;
    starts_at: string;
    ends_at: string;
    resolved_at: string | null;
  }>;
  total: number;
};

export type ReviewList = {
  context: DashboardContext;
  reviews: Array<{
    review_id: string;
    case_id: string;
    customer_reference_masked: string | null;
    amount_minor: number;
    currency: string;
    proposed_action_type: string;
    diagnosis: string | null;
    confidence_basis_points: number | null;
    reason_code: string;
    risk_detail: string;
    policy_version: string;
    classification: "TEST" | "SYNTHETIC";
    requested_at: string;
    expires_at: string;
  }>;
  total: number;
};

export type SimulationSession = {
  simulation_id: string;
  merchant_display_name: string;
  scenario: string;
  flow_type: string;
  amount_minor: number;
  currency: string;
  status: string;
  classification: "SYNTHETIC";
  checkout_path: string;
  expires_at: string;
};

export type SimulationEvents = {
  simulation_id: string;
  status: string;
  classification: "SYNTHETIC";
  events: Array<{
    event_id: string;
    occurred_at: string;
    category: "INFO" | "SUCCESS" | "WARNING" | "ERROR";
    message: string;
  }>;
};

export async function fetchJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, { cache: "no-store", ...init });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? JSON.stringify(Reflect.get(payload, "detail"))
        : `Request failed with HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload as T;
}
