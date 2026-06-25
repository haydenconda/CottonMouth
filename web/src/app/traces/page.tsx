"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchTraces, fetchAgents, type TraceRun, type AgentSummary } from "@/lib/api";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import { StatusBadge } from "@/components/status-badge";

export default function TracesPage() {
  const [runs, setRuns] = useState<TraceRun[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [agentFilter, setAgentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        const [tracesRes, agentsRes] = await Promise.all([
          fetchTraces({
            limit: 50,
            agent_name: agentFilter || undefined,
            status: statusFilter || undefined,
          }),
          fetchAgents(),
        ]);
        setRuns(tracesRes.runs);
        setAgents(agentsRes.agents);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch traces");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [agentFilter, statusFilter]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-zinc-900">Traces</h1>
        <p className="text-sm text-zinc-500">All agent runs and their execution traces</p>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <select
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-sm text-zinc-700 outline-none focus:border-emerald-500/50"
        >
          <option value="">All Agents</option>
          {agents.map((a) => (
            <option key={a.agent_name} value={a.agent_name}>
              {a.agent_name}
            </option>
          ))}
        </select>

        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-sm text-zinc-700 outline-none focus:border-emerald-500/50"
        >
          <option value="">All Statuses</option>
          <option value="completed">Completed</option>
          <option value="running">Running</option>
          <option value="failed">Failed</option>
          <option value="timeout">Timeout</option>
        </select>

        {(agentFilter || statusFilter) && (
          <button
            onClick={() => {
              setAgentFilter("");
              setStatusFilter("");
            }}
            className="text-xs text-zinc-500 hover:text-zinc-700 transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-xs text-zinc-500">
                <th className="px-4 py-2.5 text-left font-medium">Trace ID</th>
                <th className="px-4 py-2.5 text-left font-medium">Agent</th>
                <th className="px-4 py-2.5 text-left font-medium">Status</th>
                <th className="px-4 py-2.5 text-left font-medium">Spans</th>
                <th className="px-4 py-2.5 text-left font-medium">Duration</th>
                <th className="px-4 py-2.5 text-left font-medium">Cost</th>
                <th className="px-4 py-2.5 text-left font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200">
              {loading &&
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-zinc-200">
                    <td className="px-4 py-3">
                      <div className="skeleton h-3.5 w-20" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="skeleton h-3.5 w-24" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="skeleton h-5 w-16 rounded" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="skeleton h-3.5 w-6" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="skeleton h-3.5 w-14" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="skeleton h-3.5 w-12" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="skeleton h-3.5 w-10" />
                    </td>
                  </tr>
                ))}
              {!loading && runs.length === 0 && (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-12 text-center text-zinc-400"
                  >
                    No traces found
                  </td>
                </tr>
              )}
              {!loading &&
                runs.map((run) => (
                  <tr
                    key={run.trace_id}
                    className="hover:bg-zinc-100 transition-colors cursor-pointer group"
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
                      <Link
                        href={`/traces/${run.trace_id}`}
                        className="text-zinc-800 group-hover:text-emerald-600 transition-colors"
                      >
                        {run.agent_name}
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
