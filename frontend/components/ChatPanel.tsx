"use client";

/**
 * ChatPanel — QuerySmith chat drawer (quant-terminal style).
 *
 * Right-side fixed drawer wired to POST /api/chat. The backend agent runs a
 * multi-turn Claude tool-use loop against BigQuery, so a single answer can
 * take 10-40s — the status line keeps the user informed while it runs.
 *
 * Conversation state lives client-side (the API is stateless): we keep the
 * full transcript in React state + sessionStorage, but only send the last
 * HISTORY_LIMIT messages per request to bound input-token cost.
 *
 * Display metadata (cost / iterations / tokens per assistant reply) is kept
 * alongside each message locally but stripped before sending — the backend
 * schema only knows {role, content}.
 */

import { useEffect, useRef, useState } from "react";

import { postChat } from "@/lib/api";
import type { Message } from "@/lib/types";

interface ChatMessage extends Message {
  meta?: {
    cost_usd: number;
    iterations: number;
    tokens: number;
  };
}

const STORAGE_KEY = "querysmith-chat";
// Only the most recent N messages are sent per request — bounds input tokens
// (and therefore per-request cost) on long conversations.
const HISTORY_LIMIT = 12;

const STARTERS = [
  "Top 10 wallets by total PnL",
  "How many smart-money wallets are there?",
  "Which wallet has the highest win rate?",
];

export function ChatPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  // Lazy initializer restores the transcript from sessionStorage (survives
  // drawer toggling + reloads, clears when the tab closes). The window guard
  // matters: client components still render once on the server, where
  // sessionStorage doesn't exist.
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const saved = sessionStorage.getItem(STORAGE_KEY);
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // storage full / unavailable — transcript just won't persist
    }
  }, [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  async function send(text: string) {
    const question = text.trim();
    if (!question || sending) return;

    const next: ChatMessage[] = [...messages, { role: "user", content: question }];
    setMessages(next);
    setInput("");
    setError(null);
    setSending(true);

    try {
      const res = await postChat({
        messages: next
          .slice(-HISTORY_LIMIT)
          .map(({ role, content }) => ({ role, content })),
      });
      setMessages([
        ...next,
        {
          role: "assistant",
          content: res.message.content,
          meta: {
            cost_usd: res.cost_usd,
            iterations: res.iterations,
            tokens: res.input_tokens + res.output_tokens,
          },
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  }

  function reset() {
    setMessages([]);
    setError(null);
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div>
          <div className="font-mono text-sm tracking-widest text-zinc-100">
            <span className="text-emerald-400">&gt;_</span> QUERYSMITH
          </div>
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-500">
            natural language → SQL
          </div>
        </div>
        <div className="flex items-center gap-3">
          {messages.length > 0 && (
            <button
              type="button"
              onClick={reset}
              className="font-mono text-[11px] uppercase tracking-wider text-zinc-500 transition-colors hover:text-zinc-200"
            >
              reset
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close chat"
            className="font-mono text-lg leading-none text-zinc-500 transition-colors hover:text-zinc-200"
          >
            ×
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !sending && (
          <div className="space-y-3">
            <p className="text-sm leading-relaxed text-zinc-400">
              Ask anything about the wallet dataset — the agent writes and runs
              the SQL for you.
            </p>
            <div className="flex flex-col items-start gap-2">
              {STARTERS.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => send(q)}
                  className="rounded border border-zinc-800 px-3 py-1.5 text-left font-mono text-xs text-zinc-300 transition-colors hover:border-emerald-500/60 hover:text-emerald-400"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-md bg-zinc-800/80 px-3 py-2 text-sm text-zinc-100">
                {m.content}
              </div>
            </div>
          ) : (
            <div key={i} className="space-y-1">
              <div className="max-w-[95%] whitespace-pre-wrap rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2 font-mono text-xs leading-relaxed text-zinc-200">
                {m.content}
              </div>
              {m.meta && (
                <div className="font-mono text-[10px] text-zinc-600">
                  {m.meta.iterations} tool call
                  {m.meta.iterations === 1 ? "" : "s"} · $
                  {m.meta.cost_usd.toFixed(4)} ·{" "}
                  {m.meta.tokens.toLocaleString("en-US")} tokens
                </div>
              )}
            </div>
          ),
        )}

        {sending && (
          <div className="flex items-center gap-2 font-mono text-xs text-zinc-500">
            <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
            querying BigQuery… (can take up to ~30s)
          </div>
        )}

        {error && (
          <div className="rounded-md border border-red-900/50 bg-red-950/20 px-3 py-2 font-mono text-xs text-red-300">
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="flex gap-2 border-t border-zinc-800 p-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={sending}
          placeholder={sending ? "thinking…" : "ask anything…"}
          className="min-w-0 flex-1 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2 font-mono text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500/60 focus:outline-none"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-md bg-emerald-500 px-4 font-mono text-sm font-medium text-zinc-950 transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          →
        </button>
      </form>
    </div>
  );
}
