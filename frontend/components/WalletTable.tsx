"use client";

/**
 * WalletTable — sortable table of wallets returned by GET /api/wallets.
 *
 * Sort is controlled by the parent and sent to the API, not done in the
 * browser — that keeps pagination/limit semantics correct as the dataset
 * grows. Clicking a sortable header calls `onSortChange`.
 *
 * Privacy: only the leading 8 chars of `wallet_id` are rendered; the full
 * address is never put into the DOM so a screen-share can't leak it.
 */

import { ArrowDown } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { WalletSummary } from "@/lib/types";

export type SortField =
  | "total_pnl_sol"
  | "win_rate"
  | "total_swaps"
  | "classified_at";

interface Column {
  field: SortField | null; // null = not sortable
  label: string;
  align?: "right";
}

const COLUMNS: Column[] = [
  { field: null, label: "Wallet" },
  { field: null, label: "Tags" },
  { field: "win_rate", label: "Win Rate", align: "right" },
  { field: "total_pnl_sol", label: "PnL (SOL)", align: "right" },
  { field: "total_swaps", label: "Swaps", align: "right" },
  { field: "classified_at", label: "Classified", align: "right" },
];

// Per-tag color coding (border/bg/text) + short terminal-style labels.
const TAG_STYLES: Record<string, string> = {
  smart_candidate: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  market_maker: "border-sky-500/40 bg-sky-500/10 text-sky-400",
  proxy_bot: "border-violet-500/40 bg-violet-500/10 text-violet-400",
  high_frequency: "border-orange-500/40 bg-orange-500/10 text-orange-400",
  insufficient_data: "border-zinc-600/50 bg-zinc-700/20 text-zinc-400",
  data_clipped: "border-amber-500/40 bg-amber-500/10 text-amber-400",
};

const TAG_LABELS: Record<string, string> = {
  smart_candidate: "smart",
  market_maker: "mm",
  proxy_bot: "proxy",
  high_frequency: "hi-freq",
  insufficient_data: "low data",
  data_clipped: "clipped",
};

const SKELETON_ROWS = 10;

export interface WalletTableProps {
  wallets: WalletSummary[];
  sort: SortField;
  onSortChange: (next: SortField) => void;
  loading?: boolean;
}

export function WalletTable({
  wallets,
  sort,
  onSortChange,
  loading,
}: WalletTableProps) {
  return (
    <div className="overflow-hidden rounded-md border border-zinc-800">
      <Table>
        <TableHeader>
          <TableRow className="border-zinc-800 hover:bg-transparent">
            {COLUMNS.map((col) => {
              const sortable = col.field != null;
              const active = col.field === sort;
              return (
                <TableHead
                  key={col.label}
                  className={cn(
                    "h-11 font-mono text-[11px] uppercase tracking-wider text-zinc-500",
                    col.align === "right" && "text-right",
                    sortable &&
                      "cursor-pointer select-none transition-colors hover:text-zinc-200",
                    active && "text-zinc-200",
                  )}
                  onClick={
                    sortable
                      ? () => onSortChange(col.field as SortField)
                      : undefined
                  }
                >
                  <span
                    className={cn(
                      "inline-flex items-center gap-1",
                      col.align === "right" && "justify-end",
                    )}
                  >
                    {col.label}
                    {active && <ArrowDown className="size-3" />}
                  </span>
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading && wallets.length === 0 ? (
            <SkeletonRows />
          ) : wallets.length === 0 ? (
            <TableRow className="border-zinc-800/60 hover:bg-transparent">
              <TableCell
                colSpan={COLUMNS.length}
                className="py-10 text-center font-mono text-sm text-zinc-500"
              >
                no wallets match this filter
              </TableCell>
            </TableRow>
          ) : (
            wallets.map((w) => (
              <TableRow
                key={w.wallet_id}
                className="border-zinc-800/60 transition-colors hover:bg-zinc-900/60"
              >
                <TableCell className="font-mono text-zinc-200">
                  {w.wallet_id.slice(0, 8)}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {w.tags.map((t) => (
                      <span
                        key={t}
                        className={cn(
                          "rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide",
                          TAG_STYLES[t] ??
                            "border-zinc-700 bg-zinc-800/40 text-zinc-400",
                        )}
                      >
                        {TAG_LABELS[t] ?? t}
                      </span>
                    ))}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <WinRate value={w.win_rate} />
                </TableCell>
                <TableCell
                  className={cn(
                    "text-right font-mono",
                    w.total_pnl_sol == null
                      ? "text-zinc-500"
                      : w.total_pnl_sol < 0
                        ? "text-red-400"
                        : "text-emerald-400",
                  )}
                >
                  {formatSigned(w.total_pnl_sol)}
                </TableCell>
                <TableCell className="text-right font-mono text-zinc-300">
                  {formatNumber(w.total_swaps, 0)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs text-zinc-500">
                  {formatDate(w.classified_at)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

/** Win rate as number + mini progress bar (pipeline stores 0-100, not 0-1). */
function WinRate({ value }: { value: number | null }) {
  if (value == null) {
    return <span className="font-mono text-zinc-500">—</span>;
  }
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <span className="inline-flex items-center justify-end gap-2">
      <span className="h-1 w-12 overflow-hidden rounded-full bg-zinc-800">
        <span
          className={cn(
            "block h-full rounded-full",
            clamped >= 50 ? "bg-emerald-500/80" : "bg-zinc-500",
          )}
          style={{ width: `${clamped}%` }}
        />
      </span>
      <span className="font-mono text-zinc-200">{clamped.toFixed(1)}%</span>
    </span>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: SKELETON_ROWS }, (_, i) => (
        <TableRow
          key={i}
          className="animate-pulse border-zinc-800/60 hover:bg-transparent"
        >
          <TableCell>
            <div className="h-4 w-20 rounded bg-zinc-800/80" />
          </TableCell>
          <TableCell>
            <div className="h-4 w-28 rounded bg-zinc-800/60" />
          </TableCell>
          <TableCell className="text-right">
            <div className="ml-auto h-4 w-24 rounded bg-zinc-800/60" />
          </TableCell>
          <TableCell className="text-right">
            <div className="ml-auto h-4 w-20 rounded bg-zinc-800/60" />
          </TableCell>
          <TableCell className="text-right">
            <div className="ml-auto h-4 w-12 rounded bg-zinc-800/60" />
          </TableCell>
          <TableCell className="text-right">
            <div className="ml-auto h-4 w-14 rounded bg-zinc-800/60" />
          </TableCell>
        </TableRow>
      ))}
    </>
  );
}

/** PnL with explicit sign — "+12,426.17" / "-340.50" — terminal convention. */
function formatSigned(v: number | null): string {
  if (v == null) return "—";
  const abs = Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return v < 0 ? `-${abs}` : `+${abs}`;
}

function formatNumber(v: number | null, digits: number): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}
