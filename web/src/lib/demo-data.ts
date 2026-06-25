// ---------------------------------------------------------------------------
// Demo data layer — realistic sample data for standalone/Vercel deployment
// ---------------------------------------------------------------------------

import type {
  AgentEvent,
  EventsResponse,
  TraceRun,
  TracesResponse,
  TraceDetail,
  Span,
  AgentSummary,
  AgentsResponse,
  AgentDetail,
  SearchResponse,
  InvestigateCreateResponse,
  InvestigateStatusResponse,
  HealthResponse,
} from "./api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return an ISO string for `minutesAgo` minutes before now. */
function ago(minutesAgo: number): string {
  return new Date(Date.now() - minutesAgo * 60_000).toISOString();
}

function uuid(seed: string): string {
  // Deterministic-looking but unique-enough IDs for demo purposes
  const hash = seed
    .split("")
    .reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0);
  const hex = Math.abs(hash).toString(16).padStart(8, "0");
  return `${hex}-${hex.slice(0, 4)}-4${hex.slice(1, 4)}-a${hex.slice(1, 4)}-${hex}${hex.slice(0, 4)}`;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

const AGENT_NAMES = [
  "support-bot",
  "code-reviewer",
  "data-pipeline",
  "ticket-triager",
  "doc-generator",
] as const;

type AgentName = (typeof AGENT_NAMES)[number];

const AGENT_STATS: Record<AgentName, AgentSummary> = {
  "support-bot": {
    agent_name: "support-bot",
    total_runs: 1_247,
    avg_duration_ms: 3_420,
    total_cost_usd: 18.73,
    avg_cost_usd: 0.015,
    error_count: 23,
    error_rate: 0.018,
  },
  "code-reviewer": {
    agent_name: "code-reviewer",
    total_runs: 892,
    avg_duration_ms: 12_800,
    total_cost_usd: 134.56,
    avg_cost_usd: 0.151,
    error_count: 41,
    error_rate: 0.046,
  },
  "data-pipeline": {
    agent_name: "data-pipeline",
    total_runs: 3_560,
    avg_duration_ms: 8_200,
    total_cost_usd: 42.31,
    avg_cost_usd: 0.012,
    error_count: 312,
    error_rate: 0.088,
  },
  "ticket-triager": {
    agent_name: "ticket-triager",
    total_runs: 5_891,
    avg_duration_ms: 1_850,
    total_cost_usd: 29.46,
    avg_cost_usd: 0.005,
    error_count: 47,
    error_rate: 0.008,
  },
  "doc-generator": {
    agent_name: "doc-generator",
    total_runs: 456,
    avg_duration_ms: 22_400,
    total_cost_usd: 89.12,
    avg_cost_usd: 0.195,
    error_count: 18,
    error_rate: 0.039,
  },
};

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

const DEMO_EVENTS: AgentEvent[] = [
  {
    id: uuid("evt-1"),
    ts: ago(2),
    source: "agent-trace",
    severity: "info",
    title: "support-bot completed ticket #4821",
    message:
      "Resolved customer inquiry about billing discrepancy. Routed to refund workflow.",
  },
  {
    id: uuid("evt-2"),
    ts: ago(5),
    source: "agent-error",
    severity: "error",
    title: "code-reviewer timeout on PR #1337",
    message:
      "LLM call exceeded 30s timeout while analyzing 2,400-line diff in services/auth module.",
  },
  {
    id: uuid("evt-3"),
    ts: ago(8),
    source: "agent-anomaly",
    severity: "warning",
    title: "data-pipeline cost spike detected",
    message:
      "Run cost $0.48 vs 7-day avg $0.012. Token usage 34x above baseline. Possible prompt injection in input payload.",
  },
  {
    id: uuid("evt-4"),
    ts: ago(12),
    source: "agent-trace",
    severity: "info",
    title: "ticket-triager classified 15 tickets",
    message:
      "Batch classification complete. 12 routed to engineering, 2 to support, 1 to billing.",
  },
  {
    id: uuid("evt-5"),
    ts: ago(18),
    source: "agent-error",
    severity: "critical",
    title: "doc-generator failed: API key expired",
    message:
      "OpenAI API key rotation missed. All doc-generator runs failing since 14:22 UTC. 6 runs affected.",
  },
  {
    id: uuid("evt-6"),
    ts: ago(25),
    source: "agent-trace",
    severity: "info",
    title: "code-reviewer approved PR #1335",
    message:
      "No issues found in 340-line React component refactor. Suggested 2 optional style improvements.",
  },
  {
    id: uuid("evt-7"),
    ts: ago(32),
    source: "agent-anomaly",
    severity: "warning",
    title: "support-bot response latency increase",
    message:
      "P95 response time increased from 2.1s to 4.8s over last 30 minutes. Upstream model throttling suspected.",
  },
  {
    id: uuid("evt-8"),
    ts: ago(45),
    source: "agent-trace",
    severity: "info",
    title: "data-pipeline ingested 12,847 records",
    message:
      "Daily ETL batch completed. 12,847 records transformed and loaded to warehouse. 3 records skipped (malformed).",
  },
  {
    id: uuid("evt-9"),
    ts: ago(58),
    source: "agent-error",
    severity: "error",
    title: "ticket-triager misclassification detected",
    message:
      'Ticket #9102 classified as "billing" but customer reported a security vulnerability. Manual override applied.',
  },
  {
    id: uuid("evt-10"),
    ts: ago(72),
    source: "agent-trace",
    severity: "info",
    title: "doc-generator published API reference v2.4",
    message:
      "Generated documentation for 23 endpoints across 4 services. Total: 14,200 words, 47 code examples.",
  },
  {
    id: uuid("evt-11"),
    ts: ago(90),
    source: "agent-anomaly",
    severity: "warning",
    title: "code-reviewer hallucination flagged",
    message:
      "Review of PR #1329 referenced non-existent function validateUserSchema(). Confidence score below threshold.",
  },
  {
    id: uuid("evt-12"),
    ts: ago(120),
    source: "agent-trace",
    severity: "info",
    title: "support-bot escalated ticket #4805",
    message:
      "Customer requesting account deletion. Automatically escalated to compliance team per policy.",
  },
  {
    id: uuid("evt-13"),
    ts: ago(150),
    source: "agent-error",
    severity: "error",
    title: "data-pipeline schema mismatch",
    message:
      'Source table "orders" added column "discount_code" not in transform config. 847 records dropped.',
  },
  {
    id: uuid("evt-14"),
    ts: ago(180),
    source: "agent-trace",
    severity: "info",
    title: "ticket-triager SLA compliance at 99.2%",
    message:
      "Hourly SLA report: 1,247 of 1,257 tickets triaged within 5-minute window. 10 exceeded threshold.",
  },
  {
    id: uuid("evt-15"),
    ts: ago(240),
    source: "agent-anomaly",
    severity: "critical",
    title: "support-bot PII leak prevention triggered",
    message:
      "Output filter blocked response containing customer SSN. Input sanitization rule updated. Incident logged.",
  },
];

// ---------------------------------------------------------------------------
// Trace Runs
// ---------------------------------------------------------------------------

const TRACE_IDS = {
  t1: uuid("trace-support-1"),
  t2: uuid("trace-code-1"),
  t3: uuid("trace-data-1"),
  t4: uuid("trace-ticket-1"),
  t5: uuid("trace-doc-1"),
  t6: uuid("trace-support-2"),
  t7: uuid("trace-code-2"),
  t8: uuid("trace-data-2"),
  t9: uuid("trace-ticket-2"),
  t10: uuid("trace-doc-2"),
};

const DEMO_TRACE_RUNS: TraceRun[] = [
  {
    trace_id: TRACE_IDS.t1,
    agent_name: "support-bot",
    status: "completed",
    started_at: ago(2),
    ended_at: ago(1),
    duration_ms: 3_210,
    total_cost_usd: 0.014,
    span_count: 5,
  },
  {
    trace_id: TRACE_IDS.t2,
    agent_name: "code-reviewer",
    status: "failed",
    started_at: ago(5),
    ended_at: ago(4),
    duration_ms: 18_400,
    total_cost_usd: 0.32,
    span_count: 7,
  },
  {
    trace_id: TRACE_IDS.t3,
    agent_name: "data-pipeline",
    status: "completed",
    started_at: ago(8),
    ended_at: ago(7),
    duration_ms: 7_850,
    total_cost_usd: 0.011,
    span_count: 4,
  },
  {
    trace_id: TRACE_IDS.t4,
    agent_name: "ticket-triager",
    status: "completed",
    started_at: ago(12),
    ended_at: ago(11),
    duration_ms: 1_920,
    total_cost_usd: 0.005,
    span_count: 3,
  },
  {
    trace_id: TRACE_IDS.t5,
    agent_name: "doc-generator",
    status: "completed",
    started_at: ago(18),
    ended_at: ago(14),
    duration_ms: 24_600,
    total_cost_usd: 0.41,
    span_count: 6,
  },
  {
    trace_id: TRACE_IDS.t6,
    agent_name: "support-bot",
    status: "completed",
    started_at: ago(25),
    ended_at: ago(24),
    duration_ms: 2_890,
    total_cost_usd: 0.013,
    span_count: 5,
  },
  {
    trace_id: TRACE_IDS.t7,
    agent_name: "code-reviewer",
    status: "completed",
    started_at: ago(32),
    ended_at: ago(30),
    duration_ms: 14_200,
    total_cost_usd: 0.28,
    span_count: 7,
  },
  {
    trace_id: TRACE_IDS.t8,
    agent_name: "data-pipeline",
    status: "failed",
    started_at: ago(45),
    ended_at: ago(44),
    duration_ms: 5_100,
    total_cost_usd: 0.008,
    span_count: 4,
  },
  {
    trace_id: TRACE_IDS.t9,
    agent_name: "ticket-triager",
    status: "completed",
    started_at: ago(58),
    ended_at: ago(57),
    duration_ms: 1_650,
    total_cost_usd: 0.004,
    span_count: 3,
  },
  {
    trace_id: TRACE_IDS.t10,
    agent_name: "doc-generator",
    status: "completed",
    started_at: ago(72),
    ended_at: ago(68),
    duration_ms: 21_300,
    total_cost_usd: 0.38,
    span_count: 6,
  },
];

// ---------------------------------------------------------------------------
// Detailed Traces with full span trees
// ---------------------------------------------------------------------------

function buildTraceDetail(
  traceId: string,
  agentName: string,
  status: string,
  startedAgo: number,
  durationMs: number,
  costUsd: number,
  spans: Span[]
): TraceDetail {
  return {
    trace_id: traceId,
    agent_name: agentName,
    status,
    started_at: ago(startedAgo),
    ended_at: ago(startedAgo - durationMs / 60_000),
    total_duration_ms: durationMs,
    total_cost_usd: costUsd,
    span_count: spans.length,
    spans,
  };
}

// Trace 1: support-bot — full conversation handling
const TRACE_DETAIL_1 = buildTraceDetail(
  TRACE_IDS.t1,
  "support-bot",
  "completed",
  2,
  3_210,
  0.014,
  [
    {
      span_id: uuid("s1-root"),
      name: "handle_support_ticket",
      type: "agent_run",
      status: "completed",
      started_at: ago(2),
      ended_at: ago(1),
      duration_ms: 3_210,
      cost_usd: 0.014,
      input: { ticket_id: 4821, channel: "email", priority: "normal" },
      output: { resolution: "refund_initiated", confidence: 0.94 },
    },
    {
      span_id: uuid("s1-classify"),
      parent_span_id: uuid("s1-root"),
      name: "classify_intent",
      type: "llm_call",
      status: "completed",
      started_at: ago(2),
      duration_ms: 820,
      cost_usd: 0.003,
      model: "claude-sonnet-4-20250514",
      tokens_in: 340,
      tokens_out: 45,
      input: "Customer email: 'I was charged twice for my subscription last month...'",
      output: { intent: "billing_dispute", confidence: 0.96 },
    },
    {
      span_id: uuid("s1-lookup"),
      parent_span_id: uuid("s1-root"),
      name: "lookup_billing_history",
      type: "tool_call",
      status: "completed",
      started_at: ago(2),
      duration_ms: 450,
      cost_usd: 0.0,
      input: { customer_id: "cust_8a3f2", lookback_days: 90 },
      output: {
        charges: [
          { date: "2025-05-01", amount: 29.99, status: "completed" },
          { date: "2025-05-01", amount: 29.99, status: "completed" },
          { date: "2025-04-01", amount: 29.99, status: "completed" },
        ],
      },
    },
    {
      span_id: uuid("s1-draft"),
      parent_span_id: uuid("s1-root"),
      name: "draft_response",
      type: "llm_call",
      status: "completed",
      started_at: ago(2),
      duration_ms: 1_140,
      cost_usd: 0.008,
      model: "claude-sonnet-4-20250514",
      tokens_in: 890,
      tokens_out: 210,
      input:
        "Draft a response acknowledging the duplicate charge and initiating a refund...",
      output:
        "Dear Customer, we've identified a duplicate charge of $29.99 on May 1st. A refund has been initiated and should appear within 3-5 business days...",
    },
    {
      span_id: uuid("s1-send"),
      parent_span_id: uuid("s1-root"),
      name: "send_response",
      type: "tool_call",
      status: "completed",
      started_at: ago(1),
      duration_ms: 320,
      cost_usd: 0.0,
      input: { ticket_id: 4821, action: "reply", initiate_refund: true },
      output: { sent: true, refund_id: "ref_x92ka" },
    },
  ]
);

// Trace 2: code-reviewer — failed on large PR
const TRACE_DETAIL_2 = buildTraceDetail(
  TRACE_IDS.t2,
  "code-reviewer",
  "failed",
  5,
  18_400,
  0.32,
  [
    {
      span_id: uuid("s2-root"),
      name: "review_pull_request",
      type: "agent_run",
      status: "failed",
      started_at: ago(5),
      ended_at: ago(4),
      duration_ms: 18_400,
      cost_usd: 0.32,
      error: "LLM call timeout after 30000ms",
      input: { pr_number: 1337, repo: "acme/services", base: "main" },
    },
    {
      span_id: uuid("s2-fetch"),
      parent_span_id: uuid("s2-root"),
      name: "fetch_pr_diff",
      type: "tool_call",
      status: "completed",
      started_at: ago(5),
      duration_ms: 1_200,
      cost_usd: 0.0,
      input: { pr: 1337 },
      output: { files_changed: 24, additions: 1_847, deletions: 553 },
    },
    {
      span_id: uuid("s2-chunk"),
      parent_span_id: uuid("s2-root"),
      name: "chunk_diff_by_file",
      type: "tool_call",
      status: "completed",
      started_at: ago(5),
      duration_ms: 340,
      cost_usd: 0.0,
      input: { strategy: "file_boundary", max_chunk_tokens: 4000 },
      output: { chunks: 8 },
    },
    {
      span_id: uuid("s2-review-1"),
      parent_span_id: uuid("s2-root"),
      name: "review_chunk_1_auth_service",
      type: "llm_call",
      status: "completed",
      started_at: ago(5),
      duration_ms: 4_800,
      cost_usd: 0.09,
      model: "claude-sonnet-4-20250514",
      tokens_in: 3_200,
      tokens_out: 580,
      input: "Review auth service changes for security issues...",
      output: {
        findings: [
          {
            severity: "warning",
            line: 142,
            message: "JWT expiry not validated before refresh",
          },
        ],
      },
    },
    {
      span_id: uuid("s2-review-2"),
      parent_span_id: uuid("s2-root"),
      name: "review_chunk_2_user_model",
      type: "llm_call",
      status: "completed",
      started_at: ago(5),
      duration_ms: 3_600,
      cost_usd: 0.07,
      model: "claude-sonnet-4-20250514",
      tokens_in: 2_800,
      tokens_out: 320,
      output: { findings: [] },
    },
    {
      span_id: uuid("s2-review-3"),
      parent_span_id: uuid("s2-root"),
      name: "review_chunk_3_migration",
      type: "llm_call",
      status: "failed",
      started_at: ago(5),
      duration_ms: 30_000,
      cost_usd: 0.12,
      model: "claude-sonnet-4-20250514",
      tokens_in: 4_000,
      error:
        "TimeoutError: LLM call exceeded maximum duration of 30000ms. The diff chunk contained 2,400 lines of SQL migration code.",
    },
    {
      span_id: uuid("s2-summarize"),
      parent_span_id: uuid("s2-root"),
      name: "summarize_findings",
      type: "llm_call",
      status: "failed",
      started_at: ago(4),
      duration_ms: 0,
      cost_usd: 0.0,
      error: "Skipped: upstream span failed",
    },
  ]
);

// Trace 3: doc-generator — full documentation generation
const TRACE_DETAIL_3 = buildTraceDetail(
  TRACE_IDS.t5,
  "doc-generator",
  "completed",
  18,
  24_600,
  0.41,
  [
    {
      span_id: uuid("s3-root"),
      name: "generate_api_docs",
      type: "agent_run",
      status: "completed",
      started_at: ago(18),
      ended_at: ago(14),
      duration_ms: 24_600,
      cost_usd: 0.41,
      input: { service: "payments-api", version: "2.4", format: "markdown" },
      output: {
        pages_generated: 23,
        word_count: 14_200,
        code_examples: 47,
      },
    },
    {
      span_id: uuid("s3-schema"),
      parent_span_id: uuid("s3-root"),
      name: "parse_openapi_schema",
      type: "tool_call",
      status: "completed",
      started_at: ago(18),
      duration_ms: 890,
      cost_usd: 0.0,
      input: { schema_path: "/specs/payments-api.yaml" },
      output: { endpoints: 23, models: 15, total_params: 87 },
    },
    {
      span_id: uuid("s3-gen-1"),
      parent_span_id: uuid("s3-root"),
      name: "generate_endpoint_docs_batch_1",
      type: "llm_call",
      status: "completed",
      started_at: ago(17),
      duration_ms: 8_200,
      cost_usd: 0.14,
      model: "claude-sonnet-4-20250514",
      tokens_in: 4_500,
      tokens_out: 3_200,
      input: "Generate documentation for POST /payments, GET /payments/:id, ...",
      output: "## Create Payment\n\nCreates a new payment intent...",
    },
    {
      span_id: uuid("s3-gen-2"),
      parent_span_id: uuid("s3-root"),
      name: "generate_endpoint_docs_batch_2",
      type: "llm_call",
      status: "completed",
      started_at: ago(16),
      duration_ms: 7_400,
      cost_usd: 0.12,
      model: "claude-sonnet-4-20250514",
      tokens_in: 3_800,
      tokens_out: 2_900,
    },
    {
      span_id: uuid("s3-examples"),
      parent_span_id: uuid("s3-root"),
      name: "generate_code_examples",
      type: "llm_call",
      status: "completed",
      started_at: ago(15),
      duration_ms: 5_600,
      cost_usd: 0.11,
      model: "claude-sonnet-4-20250514",
      tokens_in: 2_200,
      tokens_out: 4_800,
      input: "Generate curl, Python, and Node.js examples for each endpoint...",
      output: "```bash\ncurl -X POST https://api.acme.com/payments ...\n```",
    },
    {
      span_id: uuid("s3-publish"),
      parent_span_id: uuid("s3-root"),
      name: "publish_to_docs_site",
      type: "tool_call",
      status: "completed",
      started_at: ago(14),
      duration_ms: 1_400,
      cost_usd: 0.0,
      input: { target: "docs.acme.com", version: "2.4", pages: 23 },
      output: { published: true, url: "https://docs.acme.com/payments-api/v2.4" },
    },
  ]
);

// Map of all detailed traces
const TRACE_DETAILS: Record<string, TraceDetail> = {
  [TRACE_IDS.t1]: TRACE_DETAIL_1,
  [TRACE_IDS.t2]: TRACE_DETAIL_2,
  [TRACE_IDS.t5]: TRACE_DETAIL_3,
};

// ---------------------------------------------------------------------------
// Exported demo functions
// ---------------------------------------------------------------------------

export function demoHealth(): HealthResponse {
  return {
    ts: new Date().toISOString(),
    uptime_seconds: 86_400,
    pid: 12345,
    watchers: { events: { active: true }, traces: { active: true } },
  };
}

export function demoEvents(params?: {
  limit?: number;
  source?: string;
  severity?: string;
}): EventsResponse {
  let events = [...DEMO_EVENTS];

  if (params?.source) {
    events = events.filter((e) => e.source === params.source);
  }
  if (params?.severity) {
    events = events.filter((e) => e.severity === params.severity);
  }

  const limit = params?.limit ?? 15;
  events = events.slice(0, limit);

  return { events, total: events.length };
}

export function demoTraces(params?: {
  agent_name?: string;
  status?: string;
  limit?: number;
}): TracesResponse {
  let runs = [...DEMO_TRACE_RUNS];

  if (params?.agent_name) {
    runs = runs.filter((r) => r.agent_name === params.agent_name);
  }
  if (params?.status) {
    runs = runs.filter((r) => r.status === params.status);
  }

  const limit = params?.limit ?? 50;
  runs = runs.slice(0, limit);

  return { runs, total: runs.length };
}

export function demoTrace(traceId: string): TraceDetail {
  const detail = TRACE_DETAILS[traceId];
  if (detail) return detail;

  // For trace IDs without a full detail, build a minimal one from the run
  const run = DEMO_TRACE_RUNS.find((r) => r.trace_id === traceId);
  if (run) {
    return {
      trace_id: run.trace_id,
      agent_name: run.agent_name,
      status: run.status,
      started_at: run.started_at,
      ended_at: run.ended_at,
      total_duration_ms: run.duration_ms,
      total_cost_usd: run.total_cost_usd,
      span_count: run.span_count,
      spans: [
        {
          span_id: uuid(`${traceId}-root`),
          name: `${run.agent_name}_run`,
          type: "agent_run",
          status: run.status === "failed" ? "failed" : "completed",
          started_at: run.started_at,
          ended_at: run.ended_at,
          duration_ms: run.duration_ms,
          cost_usd: run.total_cost_usd,
          ...(run.status === "failed"
            ? { error: `${run.agent_name} run failed after ${run.duration_ms}ms` }
            : {}),
        },
        {
          span_id: uuid(`${traceId}-llm`),
          parent_span_id: uuid(`${traceId}-root`),
          name: "llm_inference",
          type: "llm_call",
          status: run.status === "failed" ? "failed" : "completed",
          started_at: run.started_at,
          duration_ms: Math.round(run.duration_ms * 0.6),
          cost_usd: run.total_cost_usd,
          model: "claude-sonnet-4-20250514",
          tokens_in: Math.round(800 + Math.random() * 3000),
          tokens_out: Math.round(200 + Math.random() * 1500),
        },
        {
          span_id: uuid(`${traceId}-tool`),
          parent_span_id: uuid(`${traceId}-root`),
          name: "tool_execution",
          type: "tool_call",
          status: "completed",
          started_at: run.started_at,
          duration_ms: Math.round(run.duration_ms * 0.3),
          cost_usd: 0.0,
        },
      ],
    };
  }

  // Totally unknown trace — return a stub
  return {
    trace_id: traceId,
    agent_name: "unknown",
    status: "completed",
    started_at: ago(10),
    total_duration_ms: 1_000,
    total_cost_usd: 0.01,
    span_count: 1,
    spans: [
      {
        span_id: uuid(`${traceId}-stub`),
        name: "unknown_run",
        type: "agent_run",
        status: "completed",
        started_at: ago(10),
        duration_ms: 1_000,
        cost_usd: 0.01,
      },
    ],
  };
}

export function demoAgents(): AgentsResponse {
  const agents = Object.values(AGENT_STATS);
  return { agents, total: agents.length };
}

export function demoAgent(name: string): AgentDetail {
  const stats = AGENT_STATS[name as AgentName];
  if (stats) {
    return {
      agent_name: stats.agent_name,
      total_runs: stats.total_runs,
      avg_duration_ms: stats.avg_duration_ms,
      total_cost_usd: stats.total_cost_usd,
      avg_cost_usd: stats.avg_cost_usd,
      error_count: stats.error_count,
      error_rate: stats.error_rate,
    };
  }

  // Unknown agent fallback
  return {
    agent_name: name,
    total_runs: 0,
    avg_duration_ms: 0,
    total_cost_usd: 0,
    avg_cost_usd: 0,
    error_count: 0,
    error_rate: 0,
  };
}

export function demoSearch(params: {
  q: string;
  agent_name?: string;
}): SearchResponse {
  const q = params.q.toLowerCase();
  const matches = DEMO_TRACE_RUNS.filter(
    (r) =>
      r.agent_name.includes(q) ||
      r.trace_id.includes(q) ||
      r.status.includes(q)
  )
    .filter((r) => !params.agent_name || r.agent_name === params.agent_name)
    .map((r) => ({
      type: "trace",
      id: r.trace_id,
      agent_name: r.agent_name,
      summary: `${r.agent_name} run (${r.status}) - ${r.duration_ms}ms`,
      ts: r.started_at,
    }));

  return { matches, total: matches.length };
}

export function demoInvestigateCreate(): InvestigateCreateResponse {
  return {
    query_id: uuid("investigate-demo"),
    session_id: uuid("session-demo"),
    status: "pending",
  };
}

export function demoInvestigateStatus(id: string): InvestigateStatusResponse {
  return {
    query_id: id,
    status: "complete",
    answer:
      "Based on the trace data, the code-reviewer agent experienced a timeout on PR #1337 due to an unusually large diff (2,400 lines of SQL migration). The LLM call exceeded the 30-second timeout threshold. Recommendation: increase the timeout for migration-heavy PRs or split large diffs into smaller chunks before review.",
  };
}
