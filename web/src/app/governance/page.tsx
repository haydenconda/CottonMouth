"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchPolicies,
  fetchPermissionAudit,
  type PolicyDoc,
  type AgentPolicy,
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

function PolicyCard({
  name,
  policy,
}: {
  name: string;
  policy: AgentPolicy;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
      <div className="border-b border-zinc-200 px-4 py-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-emerald-600" />
          <h3 className="text-sm font-semibold text-zinc-900">
            {policy.display_name ?? name}
          </h3>
          <code className="ml-auto text-[11px] text-zinc-400">{name}</code>
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

      {/* Tools granted */}
      <div className="px-4 py-3 border-b border-zinc-200">
        <div className="mb-2 flex items-center gap-1.5 text-xs text-zinc-500">
          <Wrench className="h-3.5 w-3.5" />
          Tools granted
        </div>
        <div className="space-y-1.5">
          {policy.tools.map((t) => (
            <div key={t.name} className="flex items-center gap-2 text-xs">
              <code className="text-zinc-700">{t.name}</code>
              <ToolBadge effect={t.effect} />
              {t.scope && <span className="text-zinc-400">· {t.scope}</span>}
            </div>
          ))}
        </div>
      </div>

      {/* Rules by category */}
      <div className="divide-y divide-zinc-200">
        {policy.rules.map((rule) => {
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

function AuditPanel({ audit }: { audit: PermissionAudit }) {
  const { summary } = audit;
  return (
    <div className="space-y-4">
      {/* Summary stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { label: "Checks", value: summary.total, cls: "text-zinc-900" },
          { label: "Allowed", value: summary.allowed, cls: "text-emerald-600" },
          { label: "Denied", value: summary.denied, cls: "text-red-600" },
          {
            label: "Deny rate",
            value: `${(summary.deny_rate * 100).toFixed(1)}%`,
            cls: "text-amber-600",
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
          </div>
        ))}
      </div>

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
                <ShieldX className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm">
                    <code className="text-zinc-800">{d.action}</code>
                    <span className="truncate text-zinc-500">{d.resource}</span>
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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () =>
      Promise.all([fetchPolicies(), fetchPermissionAudit()])
        .then(([p, a]) => {
          if (!active) return;
          setPolicies(p);
          setAudit(a);
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
        {agentEntries.length === 0 ? (
          <p className="rounded-lg border border-zinc-200 bg-white px-4 py-8 text-center text-sm text-zinc-400">
            No agent policies defined.
          </p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {agentEntries.map(([name, policy]) => (
              <PolicyCard key={name} name={name} policy={policy} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
