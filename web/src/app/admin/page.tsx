"use client";

import { useEffect, useState } from "react";
import {
  fetchUsers,
  createUser,
  updateUser,
  deleteUser,
  fetchApiKeys,
  createApiKey,
  deleteApiKey,
  ApiError,
  type AdminUser,
  type ApiKey,
  type Role,
} from "@/lib/api";
import { Users, KeyRound, Trash2, Ban, CheckCircle2, Copy, Check } from "lucide-react";

const ROLES: Role[] = ["viewer", "operator", "admin"];

function RoleBadge({ role }: { role: Role }) {
  const styles =
    role === "admin"
      ? "border-red-500/30 bg-red-500/10 text-red-600"
      : role === "operator"
      ? "border-sky-500/30 bg-sky-500/10 text-sky-700"
      : "border-zinc-300 bg-zinc-100 text-zinc-600";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium border ${styles}`}>
      {role}
    </span>
  );
}

function RoleSelect({
  value,
  onChange,
  disabled,
}: {
  value: Role;
  onChange: (r: Role) => void;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as Role)}
      className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-700 outline-none focus:border-emerald-500 disabled:opacity-50"
    >
      {ROLES.map((r) => (
        <option key={r} value={r}>
          {r}
        </option>
      ))}
    </select>
  );
}

function UsersPanel() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role });
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const { users } = await fetchUsers();
      setUsers(users);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load users");
    }
  }

  useEffect(() => {
    // One-shot mount fetch; `load` is intentionally hoisted (not redefined
    // inline) so mutation handlers below can call it again to refresh.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      await createUser(form);
      setForm({ username: "", password: "", role: "viewer" });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create user");
    } finally {
      setCreating(false);
    }
  }

  async function handleRoleChange(u: AdminUser, role: Role) {
    try {
      await updateUser(u.id, { role });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update role");
    }
  }

  async function handleToggleDisabled(u: AdminUser) {
    try {
      await updateUser(u.id, { disabled: !u.disabled });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update user");
    }
  }

  async function handleDelete(u: AdminUser) {
    if (!confirm(`Delete user "${u.username}"? This can't be undone.`)) return;
    try {
      await deleteUser(u.id);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to delete user");
    }
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3">
        <Users className="h-4 w-4 text-emerald-600" />
        <h3 className="text-sm font-semibold text-zinc-900">Users</h3>
        <span className="ml-auto text-xs text-zinc-400">{users.length} total</span>
      </div>

      {error && (
        <div className="border-b border-red-500/20 bg-red-500/5 px-4 py-2 text-xs text-red-600">
          {error}
        </div>
      )}

      <div className="divide-y divide-zinc-200">
        {users.map((u) => (
          <div key={u.id} className="flex items-center gap-3 px-4 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-zinc-800">{u.username}</p>
              <p className="text-[11px] text-zinc-400">
                created {new Date(u.created_at).toLocaleDateString()}
              </p>
            </div>
            <RoleSelect value={u.role} onChange={(role) => handleRoleChange(u, role)} />
            <button
              onClick={() => handleToggleDisabled(u)}
              title={u.disabled ? "Enable" : "Disable"}
              className={`rounded-md p-1.5 transition-colors ${
                u.disabled
                  ? "text-emerald-500 hover:bg-emerald-50"
                  : "text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700"
              }`}
            >
              {u.disabled ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Ban className="h-3.5 w-3.5" />}
            </button>
            <button
              onClick={() => handleDelete(u)}
              title="Delete user"
              className="rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-600"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>

      <form onSubmit={handleCreate} className="flex items-end gap-2 border-t border-zinc-200 bg-zinc-50 px-4 py-3">
        <div className="flex-1">
          <label className="mb-1 block text-[10px] uppercase tracking-wider text-zinc-400">
            Username
          </label>
          <input
            required
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            className="w-full rounded-md border border-zinc-300 px-2 py-1.5 text-xs outline-none focus:border-emerald-500"
          />
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-[10px] uppercase tracking-wider text-zinc-400">
            Password
          </label>
          <input
            required
            type="password"
            minLength={8}
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            className="w-full rounded-md border border-zinc-300 px-2 py-1.5 text-xs outline-none focus:border-emerald-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-[10px] uppercase tracking-wider text-zinc-400">
            Role
          </label>
          <RoleSelect value={form.role} onChange={(role) => setForm({ ...form, role })} />
        </div>
        <button
          type="submit"
          disabled={creating}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-60"
        >
          {creating ? "Adding…" : "Add user"}
        </button>
      </form>
    </div>
  );
}

function ApiKeysPanel() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", role: "viewer" as Role });
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function load() {
    try {
      const { api_keys } = await fetchApiKeys();
      setKeys(api_keys);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load API keys");
    }
  }

  useEffect(() => {
    // One-shot mount fetch; `load` is intentionally hoisted (not redefined
    // inline) so mutation handlers below can call it again to refresh.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const created = await createApiKey(form);
      setNewKey(created.key);
      setForm({ name: "", role: "viewer" });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create API key");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(k: ApiKey) {
    if (!confirm(`Revoke API key "${k.name}"? Anything using it stops working immediately.`)) return;
    try {
      await deleteApiKey(k.id);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to revoke key");
    }
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3">
        <KeyRound className="h-4 w-4 text-emerald-600" />
        <h3 className="text-sm font-semibold text-zinc-900">API keys</h3>
        <span className="ml-auto text-xs text-zinc-400">
          for the CLI / MCP server / CI — not for browser logins
        </span>
      </div>

      {error && (
        <div className="border-b border-red-500/20 bg-red-500/5 px-4 py-2 text-xs text-red-600">
          {error}
        </div>
      )}

      {newKey && (
        <div className="border-b border-emerald-500/20 bg-emerald-500/5 px-4 py-3">
          <p className="mb-1.5 text-xs font-medium text-emerald-700">
            Copy this key now — it won&apos;t be shown again.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate rounded bg-white px-2 py-1.5 text-[11px] text-zinc-700 border border-emerald-500/20">
              {newKey}
            </code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(newKey);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className="shrink-0 rounded-md border border-emerald-500/30 p-1.5 text-emerald-700 transition-colors hover:bg-emerald-100"
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
            <button
              onClick={() => setNewKey(null)}
              className="shrink-0 text-xs text-emerald-700 underline"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <div className="divide-y divide-zinc-200">
        {keys.length === 0 && (
          <p className="px-4 py-6 text-center text-xs text-zinc-400">No API keys yet.</p>
        )}
        {keys.map((k) => (
          <div key={k.id} className="flex items-center gap-3 px-4 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-zinc-800">{k.name}</p>
              <p className="text-[11px] text-zinc-400">
                created by {k.created_by || "—"} ·{" "}
                {k.last_used_at
                  ? `last used ${new Date(k.last_used_at).toLocaleString()}`
                  : "never used"}
              </p>
            </div>
            <RoleBadge role={k.role} />
            {k.disabled ? (
              <span className="rounded px-1.5 py-0.5 text-[10px] font-medium border border-zinc-300 bg-zinc-100 text-zinc-400">
                revoked
              </span>
            ) : (
              <button
                onClick={() => handleRevoke(k)}
                title="Revoke key"
                className="rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-600"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={handleCreate} className="flex items-end gap-2 border-t border-zinc-200 bg-zinc-50 px-4 py-3">
        <div className="flex-1">
          <label className="mb-1 block text-[10px] uppercase tracking-wider text-zinc-400">
            Name
          </label>
          <input
            required
            placeholder="e.g. cursor-mcp, ci-pipeline"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full rounded-md border border-zinc-300 px-2 py-1.5 text-xs outline-none focus:border-emerald-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-[10px] uppercase tracking-wider text-zinc-400">
            Role
          </label>
          <RoleSelect value={form.role} onChange={(role) => setForm({ ...form, role })} />
        </div>
        <button
          type="submit"
          disabled={creating}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-60"
        >
          {creating ? "Creating…" : "Create key"}
        </button>
      </form>
    </div>
  );
}

export default function AdminPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-zinc-900">Admin</h1>
        <p className="text-sm text-zinc-500">
          Manage who can access CottonMouth and how. Per-agent policy mode is
          on the{" "}
          <a href="/governance" className="text-emerald-600 hover:underline">
            Governance
          </a>{" "}
          page.
        </p>
      </div>
      <UsersPanel />
      <ApiKeysPanel />
    </div>
  );
}
