"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchPolicies,
  fetchPermissionAudit,
  fetchGateway,
  type PolicyDoc,
  type AgentPolicy,
  type GatewayPolicy,
  type GatewayReconcile,
  type GatewayAgentReconcile,
  type PermissionAudit,
} from "@/lib/api";
import { timeAgo } from "@/lib/utils";
import {
  ShieldCheck,
  ShieldX,
  FolderLock,
  TerminalSquare,
  Globe,
  Wrench,
  ShieldAlert,
  Cpu,
  AlertTriangle,
} from "lucide-react";

const CATEGORY_META: Record<
  string,
  { icon: typeof FolderLock; label: string }
> = {
  filesystem: { icon: FolderLock, label: "Filesystem" },
  command: { icon: TerminalSquare, label: "Commands" },
  network: { icon: Globe, label: "Network" },
};

function ToolBadge({ effect }: { effect: string }) {
  const styles =
    effect === "allow"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700"
      : effect === "deny"
      ? "border-red-500/30 bg-red-500/10 text-red-600"
      : "border-amber-500/30 bg-amber-500/10 text-amber-600";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium border ${styles}`}>
      {effect}
    </span>
  );
}

function GatewayAccessSection({ gw }: { gw: GatewayAgentReconcile }) {
  const declaredNotExposed = gw.drift?.declared_not_exposed ?? [];
  const usedNotDeclared = gw.drift?.used_not_declared ?? [];
  const hasDrift = declaredNotExposed.length > 0 || usedNotDeclared.length > 0;
  return (
    <div className="px-4 py-3 border-b border-zinc-200 bg-violet-50/30">
      <div className="mb-2 flex items-center gap-1.5 text-xs text-zinc-500">
        <Cpu className="h-3.5 w-3.5" />
        Model access via gateway
        <span className="ml-auto rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
          enforced by LiteLLM
        </span>
      </div>
      <div className="space-y-2 text-xs">
        <div>
          <span className="text-zinc-400">Declared</span>
          <div className="mt-1 flex flex-wrap gap-1">
            {gw.declared_models.length ? (
              gw.declared_models.map((m) => (
                <code key={m} className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-700">
                  {m}
                </code>
              ))
            ) : (
              <span className="text-zinc-400">none</span>
            )}
          </div>
        </div>
        <div>
          <span className="text-zinc-400">
            Observed ({gw.observed_calls} calls · ${gw.observed_cost_usd.toFixed(4)})
          </span>
          <div className="mt-1 flex flex-wrap gap-1">
            {gw.observed_models.length ? (
              gw.observed_models.map((m) => (
                <code key={m} className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-700">
                  {m.split("/").pop()}
                </code>
              ))
            ) : (
              <span className="text-zinc-400">no calls in window</span>
            )}
          </div>
        </div>
        {hasDrift ? (
          <div className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-amber-700">
            <div className="flex items-center gap-1.5 font-medium">
              <AlertTriangle className="h-3.5 w-3.5" />
              Drift detected
            </div>
            {declaredNotExposed.length > 0 && (
              <p className="mt-1 text-[11px]">
                Declared but not exposed by gateway: {declaredNotExposed.join(", ")}
              </p>
            )}
            {usedNotDeclared.length > 0 && (
              <p className="mt-1 text-[11px]">
                Used but not declared: {usedNotDeclared.join(", ")}
              </p>
            )}
          </div>
        ) : (
          gw.declared_models.length > 0 && (
            <p className="flex items-center gap-1.5 text-[11px] text-emerald-600">
              <ShieldCheck className="h-3.5 w-3.5" /> Declared access matches gateway &amp; usage
            </p>
          )
        )}
      </div>
    </div>
  );
}

function PolicyCard({
  name,
  policy,
  gateway,
}: {
  name: string;
  policy: AgentPolicy;
  gateway?: GatewayAgentReconcile;
}) {
  const tools = policy.tools ?? [];
  const rules = policy.rules ?? [];
  return (
    <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
      <div className="border-b border-zinc-200 px-4 py-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-emerald-600" />
          <h3 className="text-sm font-semibold text-zinc-900">
            {policy.display_name ?? name}
          </h3>
          <div className="ml-auto flex items-center gap-2">
            {policy.mode && <ModeBadge mode={policy.mode} />}
            <code className="text-[11px] text-zinc-400">{name}</code>
          </div>
        </div>
        {policy.description && (
          <p className="mt-1.5 text-xs text-zinc-500">{policy.description}</p>
        )}
        {policy.enforcement && (
          <p className="mt-1 text-[11px] text-zinc-400">
            Enforced by: {policy.enforcement}
            {policy.default_effect && (
              <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-zinc-600">
                default: {policy.default_effect}
              </span>
            )}
          </p>
        )}
      </div>

      {/* Model access via the gateway (reconciled live) */}
      {gateway && <GatewayAccessSection gw={gateway} />}

      {/* Tools granted */}
      {tools.length > 0 && (
        <div className="px-4 py-3 border-b border-zinc-200">
          <div className="mb-2 flex items-center gap-1.5 text-xs text-zinc-500">
            <Wrench className="h-3.5 w-3.5" />
            Tools granted
          </div>
          <div className="space-y-1.5">
            {tools.map((t) => (
              <div key={t.name} className="flex items-center gap-2 text-xs">
                <code className="text-zinc-700">{t.name}</code>
                <ToolBadge effect={t.effect} />
                {t.scope && <span className="text-zinc-400">· {t.scope}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Rules by category */}
      <div className="divide-y divide-zinc-200">
        {rules.map((rule) => {
          const meta = CATEGORY_META[rule.category] ?? {
            icon: ShieldCheck,
            label: rule.category,
          };
          const Icon = meta.icon;
          const isDeny = rule.effect === "deny";
          return (
            <div key={rule.id} className="px-4 py-3">
              <div className="flex items-center gap-1.5 text-xs">
                <Icon className="h-3.5 w-3.5 text-zinc-500" />
                <span className="text-zinc-600">{meta.label}</span>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium border ${
                    isDeny
                      ? "border-red-500/30 bg-red-500/10 text-red-600"
                      : "border-emerald-500/30 bg-emerald-500/10 text-emerald-700"
                  }`}
                >
                  {rule.effect}
                </span>
              </div>
              {rule.description && (
                <p className="mt-1 text-[11px] text-zinc-400">{rule.description}</p>
              )}
              <div className="mt-1.5 flex flex-wrap gap-1">
                {rule.values.map((v) => (
                  <code
                    key={v}
                    className={`rounded px-1.5 py-0.5 text-[11px] ${
                      isDeny
                        ? "bg-red-500/5 text-red-600/90"
                        : "bg-zinc-100 text-zinc-700"
                    }`}
                  >
                    {v}
                  </code>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function GatewayPolicyCard({
  policy,
  snap,
}: {
  policy: GatewayPolicy;
  snap: GatewayReconcile | null;
}) {
  const reachable = snap?.reachable;
  const statusLabel = !snap?.enabled
    ? "not configured"
    : reachable
    ? "reachable"
    : "unreachable";
  const statusCls = !snap?.enabled
    ? "bg-zinc-100 text-zinc-500"
    : reachable
    ? "bg-emerald-100 text-emerald-700"
    : "bg-red-100 text-red-600";
  return (
    <div className="rounded-lg border border-emerald-200 bg-white overflow-hidden lg:col-span-2">
      <div className="border-b border-zinc-200 bg-emerald-50/50 px-4 py-3">
        <div className="flex items-center gap-2">
          <Globe className="h-4 w-4 text-emerald-600" />
          <h3 className="text-sm font-semibold text-zinc-900">
            {policy.display_name ?? "LiteLLM Gateway"}
          </h3>
          <span className={`ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium ${statusCls}`}>
            {statusLabel}
          </span>
        </div>
        {policy.description && (
          <p className="mt-1.5 text-xs text-zinc-500">{policy.description}</p>
        )}
        {snap?.endpoint && (
          <p className="mt-1 text-[11px] text-zinc-400">
            Endpoint: <code>{snap.endpoint}</code> · model access, budgets &amp; rate
            limits enforced by LiteLLM (CottonMouth observes &amp; reconciles)
          </p>
        )}
      </div>
      <div className="px-4 py-3">
        <div className="mb-2 flex items-center gap-1.5 text-xs text-zinc-500">
          <Cpu className="h-3.5 w-3.5" />
          Models exposed by the gateway
          <span className="text-zinc-400">(live from /v1/models)</span>
        </div>
        <div className="flex flex-wrap gap-1">
          {snap?.available_models?.length ? (
            snap.available_models.map((m) => (
              <code key={m} className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-700">
                {m}
              </code>
            ))
          ) : (
            <span className="text-xs text-zinc-400">
              {snap?.enabled ? "gateway not reachable" : "gateway not configured"}
            </span>
          )}
        </div>
        <p className="mt-2 text-[11px] text-zinc-400">
          {snap?.db_backed
            ? "Gateway is DB-backed: per-agent virtual-key budgets and spend are tracked."
            : "Per-agent budgets/spend require enabling the gateway's database (virtual keys). Model access + drift shown below per agent."}
        </p>
      </div>
    </div>
  );
}

function ModeBadge({ mode }: { mode?: string }) {
  const isMonitor = mode === "monitor";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium border ${
        isMonitor
          ? "border-sky-500/30 bg-sky-500/10 text-sky-700"
          : "border-zinc-300 bg-zinc-100 text-zinc-600"
      }`}
      title={
        isMonitor
          ? "Shadow mode: verdicts are recorded but actions are NOT blocked"
          : "Enforce mode: denied actions are blocked"
      }
    >
      {isMonitor ? "monitor" : "enforce"}
    </span>
  );
}

function AuditPanel({ audit }: { audit: PermissionAudit }) {
  const { summary } = audit;
  const compliancePct = (summary.compliance_rate ?? 1 - summary.deny_rate) * 100;
  const complianceCls =
    compliancePct >= 98
      ? "text-emerald-600"
      : compliancePct >= 90
      ? "text-amber-600"
      : "text-red-600";
  return (
    <div className="space-y-4">
      {/* Summary stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          {
            label: "Compliance",
            value: `${compliancePct.toFixed(1)}%`,
            cls: complianceCls,
            hint: `${summary.allowed}/${summary.total} checks allowed`,
          },
          { label: "Checks", value: summary.total, cls: "text-zinc-900" },
          {
            label: "Blocked (enforced)",
            value: summary.enforced_denied ?? summary.denied,
            cls: "text-red-600",
          },
          {
            label: "Would block (monitor)",
            value: summary.monitored_denied ?? 0,
            cls: "text-sky-600",
            hint: "shadow-mode denials — not blocked",
          },
        ].map((s) => (
          <div
            key={s.label}
            className="rounded-lg border border-zinc-200 bg-white px-4 py-3"
          >
            <p className="text-xs text-zinc-500">{s.label}</p>
            <p className={`mt-1 text-2xl font-semibold tabular-nums ${s.cls}`}>
              {s.value}
            </p>
            {s.hint && <p className="mt-0.5 text-[11px] text-zinc-400">{s.hint}</p>}
          </div>
        ))}
      </div>

      {/* Compliance by agent */}
      {audit.by_agent.length > 0 && (
        <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
          <div className="border-b border-zinc-200 px-4 py-3">
            <h3 className="text-sm font-medium text-zinc-700">Compliance by agent</h3>
          </div>
          <div className="divide-y divide-zinc-200">
            {audit.by_agent.map((a) => {
              const pct = (a.compliance_rate ?? 1) * 100;
              return (
                <div key={a.agent_name} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="w-40 shrink-0 truncate text-xs text-zinc-700">
                    {a.agent_name}
                  </span>
                  <ModeBadge mode={a.mode} />
                  <div className="flex-1 h-2 rounded-full bg-zinc-100 overflow-hidden">
                    <div
                      className={`h-full ${
                        pct >= 98
                          ? "bg-emerald-500/70"
                          : pct >= 90
                          ? "bg-amber-500/70"
                          : "bg-red-500/70"
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="w-14 text-right text-xs tabular-nums text-zinc-600">
                    {pct.toFixed(0)}%
                  </span>
                  <span className="w-28 text-right text-[11px] tabular-nums text-zinc-400">
                    {a.monitored_denied > 0 && `${a.monitored_denied} would-block`}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* By action */}
      {audit.by_action.length > 0 && (
        <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
          <div className="border-b border-zinc-200 px-4 py-3">
            <h3 className="text-sm font-medium text-zinc-700">Checks by action</h3>
          </div>
          <div className="divide-y divide-zinc-200">
            {audit.by_action.map((a) => {
              const total = a.allowed + a.denied;
              const denyPct = total ? (a.denied / total) * 100 : 0;
              return (
                <div key={a.action} className="flex items-center gap-3 px-4 py-2.5">
                  <code className="w-32 shrink-0 text-xs text-zinc-700">
                    {a.action}
                  </code>
                  <div className="flex-1 h-2 rounded-full bg-zinc-100 overflow-hidden flex">
                    <div
                      className="h-full bg-emerald-500/70"
                      style={{ width: `${100 - denyPct}%` }}
                    />
                    <div
                      className="h-full bg-red-500/70"
                      style={{ width: `${denyPct}%` }}
                    />
                  </div>
                  <span className="text-xs text-zinc-500 tabular-nums w-24 text-right">
                    {a.allowed}✓ / {a.denied}✕
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Recent denials */}
      <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
        <div className="border-b border-zinc-200 px-4 py-3 flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-red-600" />
          <h3 className="text-sm font-medium text-zinc-700">Recent denials</h3>
        </div>
        {audit.recent_denials.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-zinc-400">
            No denials recorded — every action stayed within policy.
          </p>
        ) : (
          <div className="divide-y divide-zinc-200">
            {audit.recent_denials.map((d) => (
              <Link
                key={d.span_id}
                href={`/traces/${d.trace_id}`}
                className="flex items-start gap-3 px-4 py-3 hover:bg-zinc-100 transition-colors"
              >
                <ShieldX
                  className={`mt-0.5 h-4 w-4 shrink-0 ${
                    d.would_block ? "text-sky-600" : "text-red-600"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm">
                    <code className="text-zinc-800">{d.action}</code>
                    <span className="truncate text-zinc-500">{d.resource}</span>
                    {d.would_block && (
                      <span className="shrink-0 rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
                        would block
                      </span>
                    )}
                  </div>
                  <p className="truncate text-xs text-red-600/80">{d.policy}</p>
                  <p className="text-[11px] text-zinc-400">{d.agent_name}</p>
                </div>
                <span className="shrink-0 text-[11px] text-zinc-400 tabular-nums">
                  {timeAgo(d.ts)}
                </span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function GovernancePage() {
  const [policies, setPolicies] = useState<PolicyDoc | null>(null);
  const [audit, setAudit] = useState<PermissionAudit | null>(null);
  const [gateway, setGateway] = useState<GatewayReconcile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () =>
      Promise.all([fetchPolicies(), fetchPermissionAudit(), fetchGateway()])
        .then(([p, a, g]) => {
          if (!active) return;
          setPolicies(p);
          setAudit(a);
          setGateway(g);
          setError(null);
        })
        .catch((e) => active && setError(e instanceof Error ? e.message : "Failed to load"));
    load();
    const t = setInterval(load, 10000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, []);

  const agentEntries = policies ? Object.entries(policies.agents) : [];
  const gatewayByAgent = new Map(
    (gateway?.agents ?? []).map((g) => [g.agent_name, g]),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-zinc-900">Governance</h1>
        <p className="text-sm text-zinc-500">
          Policies the agents are bound by, and the live permission audit trail
          {policies?.version && (
            <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-600">
              policy v{policies.version}
            </span>
          )}
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* Live audit */}
      {audit && <AuditPanel audit={audit} />}

      {/* Policy definitions */}
      <div>
        <h2 className="mb-3 text-sm font-medium text-zinc-600">
          Policy definitions
        </h2>
        {agentEntries.length === 0 && !policies?.llm_gateway ? (
          <p className="rounded-lg border border-zinc-200 bg-white px-4 py-8 text-center text-sm text-zinc-400">
            No policies defined.
          </p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {policies?.llm_gateway && (
              <GatewayPolicyCard policy={policies.llm_gateway} snap={gateway} />
            )}
            {agentEntries.map(([name, policy]) => (
              <PolicyCard
                key={name}
                name={name}
                policy={policy}
                gateway={gatewayByAgent.get(name)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
