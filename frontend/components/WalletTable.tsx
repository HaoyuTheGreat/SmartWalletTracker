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

import { Badge } from "@/components/ui/badge";
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

const TAG_EMOJI: Record<string, string> = {
  smart_candidate: "🧠",
  market_maker: "🎯",
  proxy_bot: "🤖",
  high_frequency: "⚡",
  insufficient_data: "📉",
  data_clipped: "⚠️",
};

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
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/40">
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
                    "text-zinc-400",
                    col.align === "right" && "text-right",
                    sortable && "cursor-pointer select-none hover:text-zinc-100",
                  )}
                  onClick={
                    sortable ? () => onSortChange(col.field as SortField) : undefined
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
            <TableRow>
              <TableCell
                colSpan={COLUMNS.length}
                className="py-8 text-center text-zinc-500"
              >
                Loading…
              </TableCell>
            </TableRow>
          ) : wallets.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={COLUMNS.length}
                className="py-8 text-center text-zinc-500"
              >
                No wallets match this filter.
              </TableCell>
            </TableRow>
          ) : (
            wallets.map((w) => (
              <TableRow key={w.wallet_id} className="border-zinc-800/60">
                <TableCell className="font-mono text-amber-300">
                  {w.wallet_id.slice(0, 8)}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {w.tags.map((t) => (
                      <Badge key={t} variant="outline" className="text-xs">
                        {TAG_EMOJI[t] ?? ""} {t}
                      </Badge>
                    ))}
                  </div>
                </TableCell>
                <TableCell className="text-right font-mono">
                  {formatPercent(w.win_rate)}
                </TableCell>
                <TableCell
                  className={cn(
                    "text-right font-mono",
                    w.total_pnl_sol != null && w.total_pnl_sol < 0
                      ? "text-red-400"
                      : "text-emerald-400",
                  )}
                >
                  {formatNumber(w.total_pnl_sol, 2)}
                </TableCell>
                <TableCell className="text-right font-mono text-zinc-300">
                  {formatNumber(w.total_swaps, 0)}
                </TableCell>
                <TableCell className="text-right text-zinc-400">
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

function formatPercent(v: number | null): string {
  if (v == null) return "—";
  // Pipeline stores win_rate as a 0-100 percent value (see filter_traders.py),
  // not a 0-1 fraction — so no extra * 100 here.
  return `${v.toFixed(1)}%`;
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
