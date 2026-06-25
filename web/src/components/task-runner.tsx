"use client";

import { useState } from "react";
import Link from "next/link";
import { runAgentTask, type AgentRunResponse } from "@/lib/api";
import { formatCost } from "@/lib/utils";
import { Send, Loader2, Sparkles } from "lucide-react";

const SUGGESTIONS = [
  "Create a file plan.txt with three project milestones, then read it back.",
  "List the files in the workspace and tell me how many there are.",
  "Fetch https://api.github.com/zen and share the wisdom.",
  "Write a haiku about observability to haiku.txt, then print it with cat.",
];

/**
 * Drives the live agent on demand. Submits a natural-language task, waits for
 * the agent to finish, and links to the resulting trace so you can inspect the
 * decisions, tool calls, cost, and permission checks it produced.
 */
export function TaskRunner({ onComplete }: { onComplete?: () => void }) {
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AgentRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(value: string) {
    const t = value.trim();
    if (!t || running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await runAgentTask(t);
      if (res.error) {
        setError(res.error);
      } else {
        setResult(res);
        onComplete?.();
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to reach the agent"
      );
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="mb-3 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-emerald-600" />
        <h2 className="text-sm font-medium text-zinc-700">Run a live agent task</h2>
        <span className="text-xs text-zinc-400">
          executes on Bedrock, fully traced
        </span>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(task);
        }}
        className="flex gap-2"
      >
        <input
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="Ask the agent to do something..."
          disabled={running}
          className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 placeholder:text-zinc-400 focus:border-emerald-500/50 focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={running || !task.trim()}
          className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
          {running ? "Running" : "Run"}
        </button>
      </form>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => {
              setTask(s);
              submit(s);
            }}
            disabled={running}
            className="rounded border border-zinc-300 bg-zinc-100 px-2 py-1 text-[11px] text-zinc-600 hover:border-zinc-300 hover:text-zinc-800 disabled:opacity-50"
          >
            {s.length > 52 ? s.slice(0, 52) + "…" : s}
          </button>
        ))}
      </div>

      {error && (
        <div className="mt-3 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-3 rounded border border-emerald-500/20 bg-emerald-500/5 px-3 py-2.5 text-xs">
          <div className="mb-1 flex flex-wrap items-center gap-3 text-zinc-600">
            <span className="font-medium text-emerald-600">
              {result.status}
            </span>
            <span>{result.tool_runs ?? 0} actions</span>
            <span>{result.denials ?? 0} denied</span>
            <span>{formatCost(result.cost)}</span>
            <Link
              href={`/traces/${result.trace_id}`}
              className="ml-auto text-emerald-600 hover:text-emerald-600"
            >
              View trace →
            </Link>
          </div>
          <p className="text-zinc-600 whitespace-pre-wrap">{result.answer}</p>
        </div>
      )}
    </div>
  );
}
