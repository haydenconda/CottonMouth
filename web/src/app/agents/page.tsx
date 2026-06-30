"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchAgents,
  type AgentSummary,
  type GatewayAgentSummary,
} from "@/lib/api";
import { formatDuration, formatCost, timeAgo } from "@/lib/utils";
import {
  Bot,
  GitBranch,
  Clock,
  DollarSign,
  AlertTriangle,
  Network,
  Phone,
  Ban,
  Wrench,
} from "lucide-react";

function AgentCardSkeleton() {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-5 space-y-4">
      <div className="flex items-center gap-3">
        <div className="skeleton h-8 w-8 rounded" />
        <div className="skeleton h-4 w-32" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="skeleton h-10 w-full rounded" />
        <div className="skeleton h-10 w-full rounded" />
        <div className="skeleton h-10 w-full rounded" />
        <div className="skeleton h-10 w-full rounded" />
      </div>
    </div>
  );
}

function AgentCard({ agent }: { agent: AgentSummary }) {
  const errorRate = agent.error_rate * 100;

  return (
    <Link
      href={`/agents/${encodeURIComponent(agent.agent_name)}`}
      className="group rounded-lg border border-zinc-200 bg-white p-5 hover:border-zinc-300 transition-colors"
    >
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex h-8 w-8 items-center justify-center rounded bg-emerald-500/10 text-emerald-600">
          <Bot className="h-4 w-4" />
        </div>
        <h3 className="text-sm font-medium text-zinc-800 group-hover:text-emerald-600 transition-colors">
          {agent.agent_name}
        </h3>
        {(agent.infra_failure_count ?? 0) > 0 && (
          <span
            className="ml-auto rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600"
            title="Infrastructure failures (excluded from error rate)"
          >
            {agent.infra_failure_count} infra
          </span>
        )}
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <GitBranch className="h-3 w-3" />
            Runs
          </div>
          <p className="text-sm font-medium text-zinc-700 tabular-nums">
            {agent.total_runs.toLocaleString()}
          </p>
        </div>

        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <Clock className="h-3 w-3" />
            Avg Duration
          </div>
          <p className="text-sm font-medium text-zinc-700 tabular-nums">
            {formatDuration(agent.avg_duration_ms)}
          </p>
        </div>

        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <DollarSign className="h-3 w-3" />
            Avg Cost
          </div>
          <p className="text-sm font-medium text-zinc-700 tabular-nums">
            {formatCost(agent.avg_cost_usd)}
          </p>
        </div>

        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <AlertTriangle className="h-3 w-3" />
            Error Rate
          </div>
          <div className="flex items-center gap-2">
            <p
              className={`text-sm font-medium tabular-nums ${
                errorRate > 10 ? "text-red-600" : "text-zinc-700"
              }`}
            >
              {errorRate.toFixed(1)}%
            </p>
            {/* Mini error bar */}
            <div className="flex-1 h-1 bg-zinc-200 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  errorRate > 10 ? "bg-red-500" : "bg-emerald-500"
                }`}
                style={{ width: `${Math.min(errorRate, 100)}%` }}
              />
            </div>
          </div>
        </div>
      </div>
    </Link>
  );
}

function GatewayAgentCard({ agent }: { agent: GatewayAgentSummary }) {
  return (
    <Link
      href={`/agents/${encodeURIComponent(agent.agent_name)}`}
      className="group rounded-lg border border-zinc-200 bg-white p-5 hover:border-zinc-300 transition-colors"
    >
      <div className="flex items-center gap-3 mb-4">
        <div className="flex h-8 w-8 items-center justify-center rounded bg-sky-500/10 text-sky-600">
          <Network className="h-4 w-4" />
        </div>
        <h3 className="text-sm font-medium text-zinc-800 group-hover:text-sky-600 transition-colors">
          {agent.agent_name}
        </h3>
        <span
          className="ml-auto rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-600"
          title="Seen only via the LiteLLM gateway (no agent_run)"
        >
          via gateway
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <Wrench className="h-3 w-3" />
            Tool Calls
          </div>
          <p className="text-sm font-medium text-zinc-700 tabular-nums">
            {(agent.tool_call_count ?? 0).toLocaleString()}
          </p>
        </div>

        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <Phone className="h-3 w-3" />
            Model Calls
          </div>
          <p className="text-sm font-medium text-zinc-700 tabular-nums">
            {agent.call_count.toLocaleString()}
          </p>
        </div>

        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <DollarSign className="h-3 w-3" />
            Total Cost
          </div>
          <p className="text-sm font-medium text-zinc-700 tabular-nums">
            {formatCost(agent.total_cost_usd)}
          </p>
        </div>

        <div className="rounded bg-zinc-100 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 mb-0.5">
            <Ban className="h-3 w-3" />
            Gateway Denials
          </div>
          <p
            className={`text-sm font-medium tabular-nums ${
              agent.denied_count > 0 ? "text-red-600" : "text-zinc-700"
            }`}
          >
            {agent.denied_count.toLocaleString()}
          </p>
        </div>
      </div>

      {(agent.tools?.length ?? 0) > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <Wrench className="h-3 w-3 text-zinc-400" />
          {agent.tools.slice(0, 6).map((t) => (
            <span
              key={t}
              className="rounded bg-sky-500/10 px-1.5 py-0.5 font-mono text-[10px] text-sky-700"
            >
              {t.split("/").pop()}
            </span>
          ))}
          {agent.tools.length > 6 && (
            <span className="text-[10px] text-zinc-400">
              +{agent.tools.length - 6}
            </span>
          )}
        </div>
      )}

      {agent.models.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Phone className="h-3 w-3 text-zinc-400" />
          {agent.models.map((m) => (
            <span
              key={m}
              className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500"
            >
              {m.split("/").pop()}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 text-[10px] text-zinc-400">
        Last seen {agent.last_seen ? timeAgo(agent.last_seen) : "—"}
      </div>
    </Link>
  );
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [gatewayAgents, setGatewayAgents] = useState<GatewayAgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAgents()
      .then((res) => {
        setAgents(res.agents);
        setGatewayAgents(res.gateway_agents ?? []);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to fetch agents");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-zinc-900">Agents</h1>
        <p className="text-sm text-zinc-500">
          Overview of all registered agents
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* Run-instrumented agents (multi-span agent_run traces) */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {loading &&
          Array.from({ length: 6 }).map((_, i) => (
            <AgentCardSkeleton key={i} />
          ))}
        {!loading &&
          agents.map((agent) => (
            <AgentCard key={agent.agent_name} agent={agent} />
          ))}
        {!loading && agents.length === 0 && !error && (
          <div className="col-span-full rounded-lg border border-zinc-200 bg-white px-6 py-12 text-center text-sm text-zinc-400">
            No agents found
          </div>
        )}
      </div>

      {/* Gateway-only agents (e.g. Cursor agents on a virtual key) */}
      {!loading && gatewayAgents.length > 0 && (
        <div className="space-y-3 pt-2">
          <div className="flex items-center gap-2">
            <Network className="h-4 w-4 text-sky-600" />
            <h2 className="text-sm font-semibold text-zinc-900">
              Gateway agents
            </h2>
            <span className="text-xs text-zinc-500">
              via LiteLLM · model + MCP tool calls (no agent_run traces)
            </span>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {gatewayAgents.map((agent) => (
              <GatewayAgentCard key={agent.agent_name} agent={agent} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
