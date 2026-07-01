import {
  demoHealth,
  demoEvents,
  demoTraces,
  demoTrace,
  demoAgents,
  demoAgent,
  demoSearch,
  demoInvestigateCreate,
  demoInvestigateStatus,
} from "./demo-data";

const RAW_API_URL = process.env.NEXT_PUBLIC_API_URL;

/**
 * "self" => call the web app's own origin and let the server proxy /api/* to
 * the backend (configured via BACKEND_ORIGIN in next.config). This is how the
 * Docker/Kubernetes deployments run: no CORS, no per-environment rebuild.
 */
const SAME_ORIGIN = RAW_API_URL === "self";

const BASE_URL = SAME_ORIGIN ? "" : (RAW_API_URL ?? "http://localhost:8150");

/**
 * Demo mode is active when no API URL is configured (e.g. Vercel deploy
 * without a backend). When true, all fetch functions return realistic
 * hardcoded data without attempting any network requests.
 */
const DEMO_MODE = !SAME_ORIGIN && (!RAW_API_URL || RAW_API_URL === "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface HealthResponse {
  ts: string;
  uptime_seconds: number;
  pid: number;
  watchers: Record<string, unknown>;
}

export interface AgentEvent {
  id: string;
  ts: string;
  source: string;
  severity: "info" | "warning" | "error" | "critical";
  title: string;
  message: string;
  agent?: string;
  action_url?: string;
  metadata?: Record<string, unknown>;
}

/**
 * Parse an event's action_url (e.g. "cottonmouth://trace/<id>/span/<sid>") into
 * the trace/span it points at, so the UI can deep-link into the waterfall.
 */
export function parseEventLink(
  url?: string
): { traceId?: string; spanId?: string } {
  if (!url) return {};
  const m = url.match(/^cottonmouth:\/\/trace\/([^/]+)(?:\/span\/([^/]+))?/);
  if (!m) return {};
  return { traceId: m[1], spanId: m[2] };
}

export interface EventsResponse {
  events: AgentEvent[];
  total: number;
}

export interface TraceRun {
  trace_id: string;
  agent_name: string;
  status: "running" | "completed" | "failed" | "timeout";
  started_at: string;
  ended_at?: string;
  duration_ms: number;
  total_cost_usd: number;
  span_count: number;
}

export interface Span {
  span_id: string;
  parent_span_id?: string;
  name: string;
  type: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  ended_at?: string;
  duration_ms: number;
  cost_usd: number;
  model?: string;
  tokens_in?: number;
  tokens_out?: number;
  input?: unknown;
  output?: unknown;
  error?: string;
  // Tool pillar ("what did it do")
  tool_name?: string;
  tool_input?: unknown;
  tool_output?: unknown;
  // Decision pillar ("why did it do it")
  decision_type?: string;
  options_considered?: Array<Record<string, unknown>>;
  chosen_option?: string;
  reasoning?: string;
  // Permission pillar ("what was it allowed to do")
  permission_result?: "allow" | "deny" | "";
  permission_policy?: string;
  children?: Span[];
  // Integration metadata: source ("litellm"), provider, correlation, call
  // origin (caller/host/pod), and gateway-policy decision.
  metadata?: SpanMetadata;
}

export interface SpanOrigin {
  agent?: string;
  identity?: string;
  provider?: string;
  caller?: string;
  host?: string;
  pod?: string;
  pid?: number;
}

export interface SpanPolicyDecision {
  result?: "allow" | "deny";
  policy?: string;
  message?: string;
  violations?: Array<{
    rule_id: string;
    category: string;
    message: string;
    blocking: boolean;
    phase: string;
  }>;
}

export interface SpanMetadata {
  source?: string;
  provider?: string;
  correlated?: boolean;
  origin?: SpanOrigin;
  policy?: SpanPolicyDecision;
  [key: string]: unknown;
}

export interface TraceDetail {
  trace_id: string;
  agent_name: string;
  status: string;
  started_at: string;
  ended_at?: string;
  total_duration_ms: number;
  total_cost_usd: number;
  span_count: number;
  spans: Span[];
}

export interface TracesResponse {
  runs: TraceRun[];
  total: number;
}

export interface AgentSummary {
  agent_name: string;
  total_runs: number;
  avg_duration_ms: number;
  total_cost_usd: number;
  avg_cost_usd: number;
  error_count: number;
  error_rate: number;
  /** Failures caused by infrastructure (expired creds, throttling), tracked
   * separately from agent-logic errors so they don't skew the error rate. */
  infra_failure_count?: number;
}

export interface AgentsResponse {
  agents: AgentSummary[];
  total: number;
  /** Agents seen only via the LiteLLM gateway (e.g. Cursor agents on a virtual
   * key): standalone llm_call spans with no agent_run, rolled up by identity. */
  gateway_agents?: GatewayAgentSummary[];
  gateway_total?: number;
}

/** A gateway-only agent: usage rolled up from its llm_call + MCP tool_call spans. */
export interface GatewayAgentSummary {
  agent_name: string;
  kind: "gateway";
  call_count: number;
  /** MCP tool calls the agent made through the gateway ("what it did"). */
  tool_call_count: number;
  total_cost_usd: number;
  avg_cost_usd: number;
  avg_duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  models: string[];
  /** Distinct MCP tools the agent invoked (e.g. "github/get_file_contents"). */
  tools: string[];
  error_count: number;
  denied_count: number;
  first_seen?: string | null;
  last_seen?: string | null;
}

/** One model call made through the gateway (for the gateway-agent detail). */
export interface GatewayCall {
  trace_id: string;
  span_id: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_ms: number;
  status: string;
  verdict: string;
  started_at: string;
}

/** One MCP tool call made through the gateway (for the gateway-agent detail). */
export interface GatewayToolCall {
  trace_id: string;
  span_id: string;
  tool_name: string;
  server: string;
  input: unknown;
  cost_usd: number;
  duration_ms: number;
  status: string;
  verdict: string;
  started_at: string;
}

export interface GatewayAgentDetail extends GatewayAgentSummary {
  calls: GatewayCall[];
  tool_calls: GatewayToolCall[];
}

/** Type guard: a gateway-only agent detail vs a run-instrumented one. */
export function isGatewayAgent(
  a: AgentDetail | GatewayAgentDetail,
): a is GatewayAgentDetail {
  return (a as GatewayAgentDetail).kind === "gateway";
}

export interface AgentDetail {
  agent_name: string;
  total_runs: number;
  avg_duration_ms: number;
  total_cost_usd: number;
  avg_cost_usd: number;
  error_count: number;
  error_rate: number;
  infra_failure_count?: number;
}

export interface SearchMatch {
  type: string;
  id: string;
  agent_name: string;
  summary: string;
  ts: string;
}

export interface SearchResponse {
  matches: SearchMatch[];
  total: number;
}

export interface InvestigateRequest {
  question: string;
  event_context?: Record<string, unknown>;
}

export interface InvestigateCreateResponse {
  query_id: string;
  session_id: string;
  status: "pending";
}

export interface InvestigateStatusResponse {
  query_id: string;
  status: "pending" | "complete";
  answer?: string;
}

export interface PolicyTool {
  name: string;
  description?: string;
  effect: "allow" | "deny" | "conditional";
  scope?: string;
}

export interface PolicyRule {
  id: string;
  category: "filesystem" | "command" | "network" | string;
  effect: "allow" | "deny";
  description?: string;
  values: string[];
}

export interface GatewayRule {
  id: string;
  category: "provider" | "model" | "tokens" | "identity" | "cost" | string;
  effect: string;
  phase?: "pre" | "post";
  enforce?: "block" | "flag";
  description?: string;
  values?: string[];
  limit?: number;
}

export interface GatewayPolicy {
  display_name?: string;
  description?: string;
  enforcement?: string;
  default_effect?: string;
  rules: GatewayRule[];
}

export interface AgentPolicy {
  display_name?: string;
  description?: string;
  enforcement?: string;
  default_effect?: string;
  /** "enforce" blocks denied actions; "monitor" (shadow) only records them. */
  mode?: "enforce" | "monitor";
  tools?: PolicyTool[];
  rules?: PolicyRule[];
  /** Gateway-only agents declare model access here instead of tools/rules. */
  gateway?: { key_alias?: string; declared_models?: string[] };
}

export interface PolicyDoc {
  version: string;
  updated?: string;
  description?: string;
  agents: Record<string, AgentPolicy>;
  /** Org-wide policies enforced on every LiteLLM gateway call. */
  llm_gateway?: GatewayPolicy;
}

export interface PermissionAudit {
  summary: {
    total: number;
    allowed: number;
    denied: number;
    deny_rate: number;
    /** allowed / total — the headline "% in compliance". */
    compliance_rate: number;
    /** denies that actually blocked the action (enforce mode). */
    enforced_denied: number;
    /** denies recorded in shadow mode — "would have been blocked". */
    monitored_denied: number;
  };
  by_agent: Array<{
    agent_name: string;
    allowed: number;
    denied: number;
    enforced_denied: number;
    monitored_denied: number;
    compliance_rate: number;
    mode: "enforce" | "monitor";
  }>;
  by_action: Array<{ action: string; allowed: number; denied: number }>;
  recent_denials: Array<{
    trace_id: string;
    span_id: string;
    agent_name: string;
    action: string;
    resource: string;
    policy: string;
    mode: "enforce" | "monitor";
    would_block: boolean;
    ts: string;
  }>;
}

// ---------------------------------------------------------------------------
// Fetch helper
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function fetchHealth(): Promise<HealthResponse> {
  if (DEMO_MODE) return demoHealth();
  try {
    return await apiFetch<HealthResponse>("/api/health");
  } catch {
    return demoHealth();
  }
}

export async function fetchEvents(params?: {
  limit?: number;
  source?: string;
  severity?: string;
}): Promise<EventsResponse> {
  if (DEMO_MODE) return demoEvents(params);
  try {
    const sp = new URLSearchParams();
    if (params?.limit) sp.set("limit", String(params.limit));
    if (params?.source) sp.set("source", params.source);
    if (params?.severity) sp.set("severity", params.severity);
    const qs = sp.toString();
    return await apiFetch<EventsResponse>(`/api/events${qs ? `?${qs}` : ""}`);
  } catch {
    return demoEvents(params);
  }
}

export async function fetchTraces(params?: {
  agent_name?: string;
  status?: string;
  limit?: number;
}): Promise<TracesResponse> {
  if (DEMO_MODE) return demoTraces(params);
  try {
    const sp = new URLSearchParams();
    if (params?.agent_name) sp.set("agent_name", params.agent_name);
    if (params?.status) sp.set("status", params.status);
    if (params?.limit) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return await apiFetch<TracesResponse>(`/api/traces${qs ? `?${qs}` : ""}`);
  } catch {
    return demoTraces(params);
  }
}

export async function fetchTrace(traceId: string): Promise<TraceDetail> {
  if (DEMO_MODE) return demoTrace(traceId);
  try {
    return await apiFetch<TraceDetail>(`/api/traces/${traceId}`);
  } catch {
    return demoTrace(traceId);
  }
}

export async function fetchAgents(): Promise<AgentsResponse> {
  if (DEMO_MODE) return demoAgents();
  try {
    return await apiFetch<AgentsResponse>("/api/agents");
  } catch {
    return demoAgents();
  }
}

export async function fetchAgent(
  name: string,
): Promise<AgentDetail | GatewayAgentDetail> {
  if (DEMO_MODE) return demoAgent(name);
  try {
    return await apiFetch<AgentDetail | GatewayAgentDetail>(
      `/api/agents/${encodeURIComponent(name)}`,
    );
  } catch {
    return demoAgent(name);
  }
}

export async function fetchSearch(params: {
  q: string;
  agent_name?: string;
  status?: string;
}): Promise<SearchResponse> {
  if (DEMO_MODE) return demoSearch(params);
  try {
    const sp = new URLSearchParams();
    sp.set("q", params.q);
    if (params.agent_name) sp.set("agent_name", params.agent_name);
    if (params.status) sp.set("status", params.status);
    return await apiFetch<SearchResponse>(`/api/search?${sp.toString()}`);
  } catch {
    return demoSearch(params);
  }
}

export async function createInvestigation(body: InvestigateRequest): Promise<InvestigateCreateResponse> {
  if (DEMO_MODE) return demoInvestigateCreate();
  try {
    return await apiFetch<InvestigateCreateResponse>("/api/investigate", {
      method: "POST",
      body: JSON.stringify(body),
    });
  } catch {
    return demoInvestigateCreate();
  }
}

export async function fetchPolicies(): Promise<PolicyDoc> {
  return apiFetch<PolicyDoc>("/api/policies");
}

export interface GatewayAgentReconcile {
  agent_name: string;
  display_name: string;
  key_alias: string;
  declared_models: string[];
  observed_models: string[];
  observed_calls: number;
  observed_cost_usd: number;
  drift: {
    declared_not_exposed: string[];
    used_not_declared: string[];
  };
}

export interface GatewayReconcile {
  enabled: boolean;
  reachable: boolean;
  endpoint: string;
  /** True only when the gateway is DB-backed (per-key budgets/spend available). */
  db_backed: boolean;
  available_models: string[];
  agents: GatewayAgentReconcile[];
}

export async function fetchGateway(): Promise<GatewayReconcile | null> {
  try {
    return await apiFetch<GatewayReconcile>("/api/gateway");
  } catch {
    return null;
  }
}

export async function fetchPermissionAudit(): Promise<PermissionAudit> {
  return apiFetch<PermissionAudit>("/api/permissions");
}

export interface AgentRunResponse {
  trace_id: string;
  status: string;
  cost: number;
  denials?: number;
  tool_runs?: number;
  answer: string;
  error?: string;
}

/**
 * Submit a task to the live interactive agent (proxied through the backend to
 * the agent's /run endpoint). Returns once the agent has finished and emitted
 * its trace.
 */
export async function runAgentTask(task: string): Promise<AgentRunResponse> {
  return apiFetch<AgentRunResponse>("/api/agent/run", {
    method: "POST",
    body: JSON.stringify({ task }),
  });
}

export async function fetchInvestigation(id: string): Promise<InvestigateStatusResponse> {
  if (DEMO_MODE) return demoInvestigateStatus(id);
  try {
    return await apiFetch<InvestigateStatusResponse>(`/api/investigate/${id}`);
  } catch {
    return demoInvestigateStatus(id);
  }
}

// ---------------------------------------------------------------------------
// SSE helper
// ---------------------------------------------------------------------------

export function subscribeEvents(
  onEvent: (event: AgentEvent) => void,
  onError?: (err: unknown) => void
): () => void {
  // In demo mode, don't attempt SSE — just return a no-op unsubscribe
  if (DEMO_MODE) return () => {};

  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${BASE_URL}/api/events/stream`, {
        signal: controller.signal,
      });

      if (!res.ok || !res.body) return;

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6)) as AgentEvent;
              onEvent(data);
            } catch {
              // skip malformed lines
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError?.(err);
      }
    }
  })();

  return () => controller.abort();
}
