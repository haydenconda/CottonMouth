"use client";

import { useEffect, useState, useCallback } from "react";
import {
  fetchAgents,
  fetchEvents,
  fetchTraces,
  subscribeEvents,
  parseEventLink,
  type AgentSummary,
  type AgentEvent,
  type TraceRun,
} from "@/lib/api";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";
import { TaskRunner } from "@/components/task-runner";
import {
  Bot,
  GitBranch,
  DollarSign,
  AlertTriangle,
} from "lucide-react";
import Link from "next/link";

// ---------------------------------------------------------------------------
// Skeleton components
// ---------------------------------------------------------------------------

function StatSkeleton() {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-5">
      <div className="skeleton h-3 w-20 mb-3" />
      <div className="skeleton h-7 w-16" />
    </div>
  );
}

function EventRowSkeleton() {
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-200">
      <div className="skeleton h-2 w-2 rounded-full" />
      <div className="skeleton h-4 w-16 rounded" />
      <div className="flex-1 space-y-1.5">
        <div className="skeleton h-3.5 w-48" />
        <div className="skeleton h-3 w-72" />
      </div>
      <div className="skeleton h-3 w-12" />
    </div>
  );
}

function TraceRowSkeleton() {
  return (
    <tr className="border-b border-zinc-200">
      <td className="px-4 py-3"><div className="skeleton h-3.5 w-24" /></td>
      <td className="px-4 py-3"><div className="skeleton h-5 w-16 rounded" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3.5 w-14" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3.5 w-12" /></td>
      <td className="px-4 py-3"><div className="skeleton h-3.5 w-10" /></td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Stats bar
// ---------------------------------------------------------------------------

function StatsBar({ agents }: { agents: AgentSummary[] }) {
  const totalAgents = agents.length;
  const totalRuns = agents.reduce((s, a) => s + a.total_runs, 0);
  const totalCost = agents.reduce((s, a) => s + a.total_cost_usd, 0);
  const avgCost = totalRuns > 0 ? totalCost / totalRuns : 0;
  const totalErrors = agents.reduce((s, a) => s + a.error_count, 0);
  const errorRate = totalRuns > 0 ? (totalErrors / totalRuns) * 100 : 0;

  const stats = [
    { label: "Agents", value: String(totalAgents), icon: Bot },
    { label: "Total Runs", value: totalRuns.toLocaleString(), icon: GitBranch },
    { label: "Avg Cost", value: formatCost(avgCost), icon: DollarSign },
    {
      label: "Error Rate",
      value: `${errorRate.toFixed(1)}%`,
      icon: AlertTriangle,
      warn: errorRate > 10,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {stats.map((s) => (
        <div
          key={s.label}
          className="rounded-lg border border-zinc-200 bg-white p-5"
        >
          <div className="flex items-center gap-2 text-xs text-zinc-500 mb-2">
            <s.icon className="h-3.5 w-3.5" />
            {s.label}
          </div>
          <p
            className={`text-2xl font-semibold ${
              "warn" in s && s.warn ? "text-red-600" : "text-zinc-900"
            }`}
          >
            {s.value}
          </p>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Events list
// ---------------------------------------------------------------------------

function EventsList({ events }: { events: AgentEvent[] }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
      <div className="border-b border-zinc-200 px-4 py-3 flex items-center justify-between">
        <h2 className="text-sm font-medium text-zinc-700">Recent Events</h2>
        <Link
          href="/events"
          className="text-xs text-emerald-600 hover:text-emerald-600 transition-colors"
        >
          View all
        </Link>
      </div>
      <div className="divide-y divide-zinc-200 max-h-[480px] overflow-y-auto">
        {events.length === 0 && (
          <p className="px-4 py-8 text-center text-sm text-zinc-400">
            No events
          </p>
        )}
        {events.map((evt, i) => {
          const { traceId } = parseEventLink(evt.action_url);
          const href = traceId ? `/traces/${traceId}` : "/events";
          return (
            <Link
              key={evt.id ?? `${evt.ts}-${i}`}
              href={href}
              className="flex items-start gap-3 px-4 py-3 hover:bg-zinc-100 transition-colors"
            >
              <StatusBadge status={evt.severity} dot className="mt-1.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600">
                    {evt.source}
                  </span>
                  <span className="truncate text-sm font-medium text-zinc-800">
                    {evt.title}
                  </span>
                </div>
                <p className="truncate text-xs text-zinc-500">{evt.message}</p>
              </div>
              <span className="shrink-0 text-[11px] text-zinc-400 tabular-nums">
                {timeAgo(evt.ts)}
              </span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Traces table
// ---------------------------------------------------------------------------

function TracesTable({ runs }: { runs: TraceRun[] }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
      <div className="border-b border-zinc-200 px-4 py-3 flex items-center justify-between">
        <h2 className="text-sm font-medium text-zinc-700">Recent Traces</h2>
        <Link
          href="/traces"
          className="text-xs text-emerald-600 hover:text-emerald-600 transition-colors"
        >
          View all
        </Link>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-200 text-xs text-zinc-500">
              <th className="px-4 py-2.5 text-left font-medium">Agent</th>
              <th className="px-4 py-2.5 text-left font-medium">Status</th>
              <th className="px-4 py-2.5 text-left font-medium">Duration</th>
              <th className="px-4 py-2.5 text-left font-medium">Cost</th>
              <th className="px-4 py-2.5 text-left font-medium">Time</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-200">
            {runs.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-zinc-400">
                  No traces
                </td>
              </tr>
            )}
            {runs.map((run) => (
              <tr
                key={run.trace_id}
                className="hover:bg-zinc-100 transition-colors"
              >
                <td className="px-4 py-2.5">
                  <Link
                    href={`/traces/${run.trace_id}`}
                    className="text-zinc-800 hover:text-emerald-600 transition-colors"
                  >
                    {run.agent_name}
                  </Link>
                </td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={run.status} />
                </td>
                <td className="px-4 py-2.5 text-zinc-600 tabular-nums">
                  {formatDuration(run.duration_ms)}
                </td>
                <td className="px-4 py-2.5 text-zinc-600 tabular-nums">
                  {formatCost(run.total_cost_usd)}
                </td>
                <td className="px-4 py-2.5 text-zinc-500 tabular-nums">
                  {timeAgo(run.started_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [runs, setRuns] = useState<TraceRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [agentsRes, eventsRes, tracesRes] = await Promise.all([
        fetchAgents(),
        fetchEvents({ limit: 15 }),
        fetchTraces({ limit: 10 }),
      ]);
      setAgents(agentsRes.agents);
      setEvents(eventsRes.events);
      setRuns(tracesRes.runs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // SSE for live events
  useEffect(() => {
    const unsubscribe = subscribeEvents((evt) => {
      setEvents((prev) => [evt, ...prev].slice(0, 30));
    });
    return unsubscribe;
  }, []);

  if (error && loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-6 py-4 text-sm text-red-600">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-zinc-900">Dashboard</h1>
        <p className="text-sm text-zinc-500">
          Agent observability overview
        </p>
      </div>

      {/* Stats */}
      {loading ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <StatSkeleton key={i} />
          ))}
        </div>
      ) : (
        <StatsBar agents={agents} />
      )}

      {/* Interactive: drive the live agent */}
      <TaskRunner onComplete={loadData} />

      {/* Error banner (non-blocking) */}
      {error && !loading && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-600">
          Some data may be stale: {error}
        </div>
      )}

      {/* Content grid */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Events */}
        {loading ? (
          <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
            <div className="border-b border-zinc-200 px-4 py-3">
              <div className="skeleton h-4 w-28" />
            </div>
            {Array.from({ length: 5 }).map((_, i) => (
              <EventRowSkeleton key={i} />
            ))}
          </div>
        ) : (
          <EventsList events={events} />
        )}

        {/* Traces */}
        {loading ? (
          <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
            <div className="border-b border-zinc-200 px-4 py-3">
              <div className="skeleton h-4 w-28" />
            </div>
            <table className="w-full">
              <tbody>
                {Array.from({ length: 5 }).map((_, i) => (
                  <TraceRowSkeleton key={i} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <TracesTable runs={runs} />
        )}
      </div>
    </div>
  );
}
