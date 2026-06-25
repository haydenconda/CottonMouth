"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchTrace, type TraceDetail, type Span } from "@/lib/api";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";
import {
  ChevronRight,
  ChevronDown,
  ArrowLeft,
  Cpu,
  Wrench,
  CircleDot,
  GitBranch,
  ShieldCheck,
  ShieldX,
  Bot,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Span type icons
// ---------------------------------------------------------------------------

function SpanIcon({ span }: { span: Span }) {
  const iconClass = "h-3.5 w-3.5 text-zinc-500";
  switch (span.type) {
    case "agent_run":
      return <Bot className={iconClass} />;
    case "llm_call":
      return <Cpu className="h-3.5 w-3.5 text-violet-600" />;
    case "tool_call":
      return <Wrench className="h-3.5 w-3.5 text-sky-600" />;
    case "decision":
      return <GitBranch className="h-3.5 w-3.5 text-amber-600" />;
    case "permission_check":
      return span.permission_result === "deny" ? (
        <ShieldX className="h-3.5 w-3.5 text-red-600" />
      ) : (
        <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
      );
    default:
      return <CircleDot className={iconClass} />;
  }
}

// ---------------------------------------------------------------------------
// Span row
// ---------------------------------------------------------------------------

function SpanRow({
  span,
  depth,
  maxDuration,
}: {
  span: Span;
  depth: number;
  maxDuration: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const barWidth = maxDuration > 0 ? (span.duration_ms / maxDuration) * 100 : 0;
  const isPermission = span.type === "permission_check";
  const isDecision = span.type === "decision";
  const denied = isPermission && span.permission_result === "deny";
  const toolInputStr =
    span.tool_input && Object.keys(span.tool_input as object).length
      ? JSON.stringify(span.tool_input)
      : "";
  const toolOutput = span.tool_output as { result?: string; error?: string } | undefined;
  const hasDetail =
    span.input != null ||
    span.output != null ||
    !!span.error ||
    !!span.model ||
    span.tokens_in != null ||
    !!span.reasoning ||
    !!span.permission_policy ||
    !!toolInputStr ||
    !!(toolOutput && (toolOutput.result || toolOutput.error));

  return (
    <>
      <div
        className="group flex items-center gap-2 border-b border-zinc-200 px-4 py-2 hover:bg-zinc-100 transition-colors cursor-pointer"
        style={{ paddingLeft: `${16 + depth * 24}px` }}
        onClick={() => hasDetail && setExpanded(!expanded)}
      >
        {/* Expand toggle */}
        <span className="w-4 shrink-0">
          {hasDetail &&
            (expanded ? (
              <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-zinc-500" />
            ))}
        </span>

        {/* Type icon */}
        <SpanIcon span={span} />

        {/* Name */}
        <span className="min-w-0 truncate text-sm text-zinc-700 flex-1">
          {span.name}
          {isDecision && span.chosen_option && (
            <span className="ml-2 text-xs text-amber-600/80">
              → chose {span.chosen_option}
            </span>
          )}
        </span>

        {/* Permission verdict pill (replaces the duration bar for these rows) */}
        {isPermission ? (
          <span
            className={`hidden sm:inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium border w-48 justify-center shrink-0 ${
              denied
                ? "border-red-500/30 bg-red-500/10 text-red-600"
                : "border-emerald-500/30 bg-emerald-500/10 text-emerald-700"
            }`}
          >
            {denied ? "DENIED by policy" : "ALLOWED by policy"}
          </span>
        ) : (
          <div className="hidden sm:flex items-center gap-2 w-48 shrink-0">
            <div className="flex-1 h-1.5 bg-zinc-100 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  span.status === "failed" ? "bg-red-500" : "bg-emerald-500"
                }`}
                style={{ width: `${Math.max(barWidth, 2)}%` }}
              />
            </div>
            <span className="text-xs text-zinc-500 tabular-nums w-16 text-right">
              {formatDuration(span.duration_ms)}
            </span>
          </div>
        )}

        {/* Status */}
        <StatusBadge status={span.status} className="shrink-0" />

        {/* Cost */}
        <span className="text-xs text-zinc-500 tabular-nums w-16 text-right shrink-0">
          {formatCost(span.cost_usd)}
        </span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          className="border-b border-zinc-200 bg-white px-6 py-4 text-xs"
          style={{ paddingLeft: `${40 + depth * 24}px` }}
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 mb-4">
            {span.model && (
              <div>
                <span className="text-zinc-400">Model</span>
                <p className="text-zinc-700 mt-0.5">{span.model}</p>
              </div>
            )}
            {span.tokens_in != null && (
              <div>
                <span className="text-zinc-400">Tokens In</span>
                <p className="text-zinc-700 mt-0.5 tabular-nums">
                  {span.tokens_in.toLocaleString()}
                </p>
              </div>
            )}
            {span.tokens_out != null && (
              <div>
                <span className="text-zinc-400">Tokens Out</span>
                <p className="text-zinc-700 mt-0.5 tabular-nums">
                  {span.tokens_out.toLocaleString()}
                </p>
              </div>
            )}
            <div>
              <span className="text-zinc-400">Duration</span>
              <p className="text-zinc-700 mt-0.5">{formatDuration(span.duration_ms)}</p>
            </div>
          </div>

          {/* Why did it do it? */}
          {span.reasoning && (
            <div className="mb-3">
              <span className="text-amber-600/80 font-medium">Reasoning</span>
              <p className="mt-1 rounded border border-amber-500/20 bg-amber-500/5 p-3 text-zinc-700 whitespace-pre-wrap">
                {span.reasoning}
              </p>
            </div>
          )}

          {isDecision && (span.options_considered?.length ?? 0) > 0 && (
            <div className="mb-3">
              <span className="text-zinc-400">Options considered</span>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {span.options_considered!.map((o, i) => {
                  const label = String(
                    (o as Record<string, unknown>).option ?? JSON.stringify(o)
                  );
                  const chosen = label === span.chosen_option;
                  return (
                    <span
                      key={i}
                      className={`rounded px-2 py-0.5 text-xs border ${
                        chosen
                          ? "border-amber-500/40 bg-amber-500/10 text-amber-600"
                          : "border-zinc-300 bg-zinc-100 text-zinc-600"
                      }`}
                    >
                      {label}
                      {chosen && " ✓"}
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          {/* What was it allowed to do? */}
          {isPermission && span.permission_policy && (
            <div className="mb-3">
              <span className={denied ? "text-red-600 font-medium" : "text-emerald-600 font-medium"}>
                Policy ({denied ? "denied" : "allowed"})
              </span>
              <p
                className={`mt-1 rounded border p-3 whitespace-pre-wrap ${
                  denied
                    ? "border-red-500/20 bg-red-500/5 text-red-600"
                    : "border-emerald-500/20 bg-emerald-500/5 text-emerald-700"
                }`}
              >
                {span.permission_policy}
              </p>
            </div>
          )}

          {/* What did it do? (tool inputs/outputs) */}
          {toolInputStr && (
            <div className="mb-3">
              <span className="text-zinc-400">Tool Input</span>
              <pre className="mt-1 rounded border border-zinc-200 bg-white p-3 text-zinc-600 overflow-x-auto max-h-48 whitespace-pre-wrap">
                {JSON.stringify(span.tool_input, null, 2)}
              </pre>
            </div>
          )}

          {toolOutput && (toolOutput.result || toolOutput.error) && (
            <div className="mb-3">
              <span className="text-zinc-400">Tool Output</span>
              <pre
                className={`mt-1 rounded border p-3 overflow-x-auto max-h-48 whitespace-pre-wrap ${
                  toolOutput.error
                    ? "border-red-500/20 bg-red-500/5 text-red-600"
                    : "border-zinc-200 bg-white text-zinc-600"
                }`}
              >
                {toolOutput.error ?? toolOutput.result}
              </pre>
            </div>
          )}

          {span.error && (
            <div className="mb-3">
              <span className="text-red-600 font-medium">Error</span>
              <pre className="mt-1 rounded border border-red-500/20 bg-red-500/5 p-3 text-red-600 overflow-x-auto whitespace-pre-wrap">
                {span.error}
              </pre>
            </div>
          )}

          {span.input != null && (
            <div className="mb-3">
              <span className="text-zinc-400">Input</span>
              <pre className="mt-1 rounded border border-zinc-200 bg-white p-3 text-zinc-600 overflow-x-auto max-h-48 whitespace-pre-wrap">
                {typeof span.input === "string"
                  ? span.input
                  : JSON.stringify(span.input, null, 2)}
              </pre>
            </div>
          )}

          {span.output != null && (
            <div>
              <span className="text-zinc-400">Output</span>
              <pre className="mt-1 rounded border border-zinc-200 bg-white p-3 text-zinc-600 overflow-x-auto max-h-48 whitespace-pre-wrap">
                {typeof span.output === "string"
                  ? span.output
                  : JSON.stringify(span.output, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Render children */}
      {span.children?.map((child) => (
        <SpanRow
          key={child.span_id}
          span={child}
          depth={depth + 1}
          maxDuration={maxDuration}
        />
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Build span tree
// ---------------------------------------------------------------------------

function buildSpanTree(spans: Span[]): Span[] {
  const map = new Map<string, Span>();
  const roots: Span[] = [];

  for (const span of spans) {
    map.set(span.span_id, { ...span, children: [] });
  }

  for (const span of spans) {
    const node = map.get(span.span_id)!;
    if (span.parent_span_id && map.has(span.parent_span_id)) {
      const parent = map.get(span.parent_span_id)!;
      parent.children = parent.children ?? [];
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function TraceDetailPage() {
  const params = useParams<{ id: string }>();
  const traceId = params.id;

  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!traceId) return;
    setLoading(true);
    fetchTrace(traceId)
      .then((data) => {
        setTrace(data);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to fetch trace");
      })
      .finally(() => setLoading(false));
  }, [traceId]);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-5 w-32" />
        <div className="skeleton h-8 w-64" />
        <div className="rounded-lg border border-zinc-200 bg-white p-6">
          <div className="space-y-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="skeleton h-10 w-full" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error || !trace) {
    return (
      <div className="space-y-4">
        <Link
          href="/traces"
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to traces
        </Link>
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-6 py-4 text-sm text-red-600">
          {error ?? "Trace not found"}
        </div>
      </div>
    );
  }

  const spans = Array.isArray(trace.spans) ? trace.spans : [];
  const spanTree = buildSpanTree(spans);
  const maxDuration = Math.max(...spans.map((s) => s.duration_ms), 1);

  // Four-pillar rollup (Khare's agent-observability-gap framework).
  const llmSpans = spans.filter((s) => s.type === "llm_call");
  const toolSpans = spans.filter((s) => s.type === "tool_call");
  const decisionSpans = spans.filter((s) => s.type === "decision");
  const permSpans = spans.filter((s) => s.type === "permission_check");
  const denied = permSpans.filter((s) => s.permission_result === "deny").length;
  const allowed = permSpans.length - denied;
  const failedTools = toolSpans.filter((s) => s.status === "failed").length;
  const totalTokens = spans.reduce(
    (sum, s) => sum + (s.tokens_in ?? 0) + (s.tokens_out ?? 0),
    0
  );

  const pillars = [
    {
      label: "Actions taken",
      value: `${toolSpans.length}`,
      sub: failedTools ? `${failedTools} failed / retried` : "tool calls",
      icon: <Wrench className="h-4 w-4 text-sky-600" />,
    },
    {
      label: "Decisions",
      value: `${decisionSpans.length}`,
      sub: "choices recorded",
      icon: <GitBranch className="h-4 w-4 text-amber-600" />,
    },
    {
      label: "Cost",
      value: formatCost(trace.total_cost_usd),
      sub: `${llmSpans.length} LLM · ${totalTokens.toLocaleString()} tok`,
      icon: <Cpu className="h-4 w-4 text-violet-600" />,
    },
    {
      label: "Permissions",
      value: `${allowed}✓ / ${denied}✕`,
      sub: denied ? `${denied} denied by policy` : "all allowed",
      icon:
        denied > 0 ? (
          <ShieldX className="h-4 w-4 text-red-600" />
        ) : (
          <ShieldCheck className="h-4 w-4 text-emerald-600" />
        ),
    },
  ];

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        href="/traces"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700 transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to traces
      </Link>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-lg font-semibold text-zinc-900">
              {trace.agent_name}
            </h1>
            <StatusBadge status={trace.status} />
          </div>
          <p className="font-mono text-xs text-zinc-400">{trace.trace_id}</p>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <div>
            <span className="text-zinc-400 text-xs">Duration</span>
            <p className="text-zinc-700 tabular-nums">
              {formatDuration(trace.total_duration_ms)}
            </p>
          </div>
          <div>
            <span className="text-zinc-400 text-xs">Cost</span>
            <p className="text-zinc-700 tabular-nums">
              {formatCost(trace.total_cost_usd)}
            </p>
          </div>
          <div>
            <span className="text-zinc-400 text-xs">Spans</span>
            <p className="text-zinc-700 tabular-nums">{trace.span_count}</p>
          </div>
          {trace.started_at && (
            <div>
              <span className="text-zinc-400 text-xs">Started</span>
              <p className="text-zinc-700">{timeAgo(trace.started_at)}</p>
            </div>
          )}
        </div>
      </div>

      {/* Four-pillar observability rollup */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {pillars.map((p) => (
          <div
            key={p.label}
            className="rounded-lg border border-zinc-200 bg-white px-4 py-3"
          >
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              {p.icon}
              {p.label}
            </div>
            <p className="mt-1 text-xl font-semibold text-zinc-900 tabular-nums">
              {p.value}
            </p>
            <p className="text-xs text-zinc-400">{p.sub}</p>
          </div>
        ))}
      </div>

      {/* Span waterfall */}
      <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
        <div className="border-b border-zinc-200 px-4 py-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-zinc-700">Span Waterfall</h2>
          <span className="text-xs text-zinc-400">
            {trace.span_count} span{trace.span_count !== 1 && "s"}
          </span>
        </div>

        {spanTree.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-zinc-400">
            No spans
          </p>
        ) : (
          <div>
            {spanTree.map((span) => (
              <SpanRow
                key={span.span_id}
                span={span}
                depth={0}
                maxDuration={maxDuration}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
