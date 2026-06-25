"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  fetchEvents,
  subscribeEvents,
  parseEventLink,
  type AgentEvent,
} from "@/lib/api";
import { timeAgo } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";
import {
  Activity,
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Info,
  Radio,
  ShieldX,
  Unplug,
  Zap,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Source metadata — icon + friendly label for each event source
// ---------------------------------------------------------------------------

const SOURCE_META: Record<string, { label: string; icon: typeof Activity }> = {
  "agent-trace": { label: "Trace", icon: GitBranch },
  "agent-error": { label: "Error", icon: AlertTriangle },
  "agent-anomaly": { label: "Anomaly", icon: Zap },
  "agent-permission": { label: "Permission", icon: ShieldX },
  "agent-infra": { label: "Infra", icon: Unplug },
};

function sourceMeta(source: string) {
  return SOURCE_META[source] ?? { label: source, icon: Activity };
}

// Pull human-friendly metrics out of the free-text message (cost / tokens /
// duration / counts) so the detail view can show them as chips.
function extractMetrics(message: string): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = [];
  const cost = message.match(/\$([0-9.]+)/);
  if (cost) out.push({ label: "Cost", value: `$${cost[1]}` });
  const tokens = message.match(/([\d,]+)\s*tokens/i);
  if (tokens) out.push({ label: "Tokens", value: tokens[1] });
  const ms = message.match(/(\d+)\s*ms/i);
  if (ms) out.push({ label: "Duration", value: `${(Number(ms[1]) / 1000).toFixed(1)}s` });
  const llm = message.match(/(\d+)\s*LLM/i);
  if (llm) out.push({ label: "LLM calls", value: llm[1] });
  const tools = message.match(/(\d+)\s*tools?/i);
  if (tools) out.push({ label: "Tools", value: tools[1] });
  return out;
}

// ---------------------------------------------------------------------------
// Event row (expandable)
// ---------------------------------------------------------------------------

function EventRow({ event }: { event: AgentEvent }) {
  const [open, setOpen] = useState(false);
  const meta = sourceMeta(event.source);
  const SourceIcon = meta.icon;
  const { traceId, spanId } = parseEventLink(event.action_url);
  const metrics = extractMetrics(event.message);

  return (
    <div className="border-b border-zinc-200 last:border-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-zinc-100 transition-colors"
      >
        <span className="mt-1 w-4 shrink-0 text-zinc-400">
          {open ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </span>
        <StatusBadge status={event.severity} dot className="mt-1.5 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600">
              <SourceIcon className="h-3 w-3" />
              {meta.label}
            </span>
            <span className="truncate text-sm font-medium text-zinc-800">
              {event.title}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-zinc-500">{event.message}</p>
        </div>
        {traceId && (
          <span className="mt-0.5 hidden shrink-0 items-center gap-1 rounded border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-600 sm:inline-flex">
            <GitBranch className="h-3 w-3" />
            trace
          </span>
        )}
        <span className="mt-0.5 shrink-0 text-[11px] text-zinc-400 tabular-nums">
          {timeAgo(event.ts)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 bg-white/40 px-4 py-4 pl-11">
          <p className="text-sm text-zinc-700">{event.message}</p>

          {metrics.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {metrics.map((m) => (
                <span
                  key={m.label}
                  className="rounded border border-zinc-200 bg-white px-2 py-1 text-xs"
                >
                  <span className="text-zinc-500">{m.label}: </span>
                  <span className="text-zinc-800 tabular-nums">{m.value}</span>
                </span>
              ))}
            </div>
          )}

          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-zinc-400">Severity</dt>
              <dd className="mt-0.5">
                <StatusBadge status={event.severity} />
              </dd>
            </div>
            <div>
              <dt className="text-zinc-400">Source</dt>
              <dd className="mt-0.5 text-zinc-700">{event.source}</dd>
            </div>
            {event.agent && (
              <div>
                <dt className="text-zinc-400">Emitter</dt>
                <dd className="mt-0.5 flex items-center gap-1 text-zinc-700">
                  <Bot className="h-3 w-3 text-emerald-600" />
                  {event.agent}
                </dd>
              </div>
            )}
            <div>
              <dt className="text-zinc-400">Timestamp</dt>
              <dd className="mt-0.5 text-zinc-700 tabular-nums">
                {new Date(event.ts).toLocaleString()}
              </dd>
            </div>
          </dl>

          {traceId && (
            <Link
              href={`/traces/${traceId}`}
              className="inline-flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-600 hover:bg-emerald-500/20 transition-colors"
            >
              <GitBranch className="h-3.5 w-3.5" />
              View {spanId ? "span in trace" : "trace"}
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const SEVERITIES = ["all", "critical", "warning", "info"] as const;

export default function EventsPage() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [severity, setSeverity] = useState<string>("all");
  const [source, setSource] = useState<string>("all");

  useEffect(() => {
    let active = true;
    fetchEvents({ limit: 200 })
      .then((res) => {
        if (!active) return;
        setEvents(res.events);
        setError(null);
      })
      .catch((e) =>
        active && setError(e instanceof Error ? e.message : "Failed to load events")
      )
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  // Live updates via SSE — prepend new events as they arrive.
  useEffect(() => {
    return subscribeEvents((evt) =>
      setEvents((prev) => [evt, ...prev].slice(0, 300))
    );
  }, []);

  const sources = useMemo(() => {
    const s = new Set(events.map((e) => e.source).filter(Boolean));
    return ["all", ...Array.from(s).sort()];
  }, [events]);

  const counts = useMemo(() => {
    const c = { total: events.length, critical: 0, warning: 0, info: 0 };
    for (const e of events) {
      if (e.severity === "critical" || e.severity === "error") c.critical++;
      else if (e.severity === "warning") c.warning++;
      else c.info++;
    }
    return c;
  }, [events]);

  const filtered = useMemo(
    () =>
      events.filter((e) => {
        const sevOk =
          severity === "all" ||
          e.severity === severity ||
          (severity === "critical" && e.severity === "error");
        const srcOk = source === "all" || e.source === source;
        return sevOk && srcOk;
      }),
    [events, severity, source]
  );

  const stats = [
    { label: "Total", value: counts.total, cls: "text-zinc-900", icon: Activity },
    { label: "Critical", value: counts.critical, cls: "text-red-600", icon: AlertTriangle },
    { label: "Warnings", value: counts.warning, cls: "text-amber-600", icon: Zap },
    { label: "Info", value: counts.info, cls: "text-emerald-600", icon: Info },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-zinc-900">Events</h1>
          <p className="text-sm text-zinc-500">
            Anomalies, errors, and run signals — click any event to drill in
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-600">
          <Radio className="h-3 w-3 animate-pulse" />
          Live
        </span>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats.map((s) => (
          <div
            key={s.label}
            className="rounded-lg border border-zinc-200 bg-white px-4 py-3"
          >
            <div className="flex items-center gap-1.5 text-xs text-zinc-500">
              <s.icon className="h-3.5 w-3.5" />
              {s.label}
            </div>
            <p className={`mt-1 text-2xl font-semibold tabular-nums ${s.cls}`}>
              {s.value}
            </p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-1.5">
          {SEVERITIES.map((s) => (
            <button
              key={s}
              onClick={() => setSeverity(s)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors ${
                severity === s
                  ? "bg-zinc-200 text-zinc-900"
                  : "border border-zinc-200 text-zinc-600 hover:bg-zinc-100"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-xs text-zinc-700 focus:border-zinc-300 focus:outline-none"
        >
          {sources.map((s) => (
            <option key={s} value={s}>
              {s === "all" ? "All sources" : sourceMeta(s).label}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* List */}
      <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
        {loading ? (
          <div className="divide-y divide-zinc-200">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-3">
                <div className="skeleton h-2 w-2 rounded-full" />
                <div className="flex-1 space-y-1.5">
                  <div className="skeleton h-3.5 w-48" />
                  <div className="skeleton h-3 w-72" />
                </div>
                <div className="skeleton h-3 w-12" />
              </div>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <p className="px-4 py-12 text-center text-sm text-zinc-400">
            No events match these filters.
          </p>
        ) : (
          <div>
            {filtered.map((e, i) => (
              <EventRow key={`${e.ts}-${i}`} event={e} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
