"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  fetchAgent,
  fetchTraces,
  type AgentDetail,
  type TraceRun,
} from "@/lib/api";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";
import {
  ArrowLeft,
  GitBranch,
  Clock,
  DollarSign,
  AlertTriangle,
  Activity,
} from "lucide-react";

export default function AgentDetailPage() {
  const params = useParams<{ name: string }>();
  const agentName = decodeURIComponent(params.name);

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [runs, setRuns] = useState<TraceRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agentName) return;
    setLoading(true);
    Promise.all([
      fetchAgent(agentName),
      fetchTraces({ agent_name: agentName, limit: 20 }),
    ])
      .then(([agentRes, tracesRes]) => {
        setAgent(agentRes);
        setRuns(tracesRes.runs);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to fetch agent");
      })
      .finally(() => setLoading(false));
  }, [agentName]);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-5 w-28" />
        <div className="skeleton h-8 w-48" />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-zinc-200 bg-white p-5">
              <div className="skeleton h-3 w-20 mb-3" />
              <div className="skeleton h-7 w-16" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="space-y-4">
        <Link
          href="/agents"
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to agents
        </Link>
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-6 py-4 text-sm text-red-600">
          {error ?? "Agent not found"}
        </div>
      </div>
    );
  }

  const errorRate = agent.error_rate * 100;

  const stats = [
    {
      label: "Total Runs",
      value: agent.total_runs.toLocaleString(),
      icon: GitBranch,
    },
    {
      label: "Avg Duration",
      value: formatDuration(agent.avg_duration_ms),
      icon: Clock,
    },
    {
      label: "Total Cost",
      value: formatCost(agent.total_cost_usd),
      icon: DollarSign,
    },
    {
      label: "Error Rate",
      value: `${errorRate.toFixed(1)}%`,
      icon: AlertTriangle,
      warn: errorRate > 10,
    },
  ];

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        href="/agents"
        className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700 transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to agents
      </Link>

      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-600">
          <Activity className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-zinc-900">
            {agent.agent_name}
          </h1>
          <p className="text-xs text-zinc-500">
            {agent.total_runs} total run{agent.total_runs !== 1 && "s"} /{" "}
            {agent.error_count} error{agent.error_count !== 1 && "s"}
            {(agent.infra_failure_count ?? 0) > 0 && (
              <span className="ml-1 text-amber-600">
                · {agent.infra_failure_count} infra issue
                {agent.infra_failure_count !== 1 && "s"} (excluded)
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Stats */}
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

      {/* Recent runs */}
      <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
        <div className="border-b border-zinc-200 px-4 py-3">
          <h2 className="text-sm font-medium text-zinc-700">Recent Runs</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-xs text-zinc-500">
                <th className="px-4 py-2.5 text-left font-medium">Trace ID</th>
                <th className="px-4 py-2.5 text-left font-medium">Status</th>
                <th className="px-4 py-2.5 text-left font-medium">Spans</th>
                <th className="px-4 py-2.5 text-left font-medium">Duration</th>
                <th className="px-4 py-2.5 text-left font-medium">Cost</th>
                <th className="px-4 py-2.5 text-left font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200">
              {runs.length === 0 && (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-12 text-center text-zinc-400"
                  >
                    No runs found
                  </td>
                </tr>
              )}
              {runs.map((run) => (
                <tr
                  key={run.trace_id}
                  className="hover:bg-zinc-100 transition-colors group"
                >
                  <td className="px-4 py-2.5">
                    <Link
                      href={`/traces/${run.trace_id}`}
                      className="font-mono text-xs text-zinc-500 group-hover:text-emerald-600 transition-colors"
                    >
                      {run.trace_id.slice(0, 8)}...
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-4 py-2.5 text-zinc-600 tabular-nums">
                    {run.span_count}
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
    </div>
  );
}
