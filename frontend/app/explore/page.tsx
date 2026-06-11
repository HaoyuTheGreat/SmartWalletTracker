"use client";

/**
 * Explorer page — paginated wallet list with tag filter + sort, in the same
 * quant-terminal chrome as the landing page.
 *
 * Client component: the whole view is interactive. Changing tag, sort, or
 * page triggers a fresh GET /api/wallets call. Tag/sort changes reset to
 * page 1, otherwise the user could end up on page 9 of a 3-page result set.
 *
 * `result` is stamped with the params it was fetched for, so we can derive
 * `loading` = "settled fetch params don't match current params yet" — avoids
 * synchronous setState inside the effect, which would cause cascading renders.
 */

import { useEffect, useState } from "react";

import { ChatPanel } from "@/components/ChatPanel";
import { FilterTabs } from "@/components/FilterTabs";
import { SiteHeader } from "@/components/SiteHeader";
import { WalletPagination } from "@/components/WalletPagination";
import { WalletTable, type SortField } from "@/components/WalletTable";
import { getWallets } from "@/lib/api";
import type { WalletSummary } from "@/lib/types";

const PAGE_SIZE = 50;

interface FetchResult {
  tag: string | null;
  sort: SortField;
  page: number;
  wallets?: WalletSummary[];
  total?: number;
  error?: string;
}

export default function ExplorePage() {
  const [tag, setTag] = useState<string | null>(null);
  const [sort, setSort] = useState<SortField>("total_pnl_sol");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<FetchResult | null>(null);
  const [chatOpen, setChatOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getWallets({
      tag: tag ?? undefined,
      sort,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    })
      .then((res) => {
        if (!cancelled)
          setResult({ tag, sort, page, wallets: res.rows, total: res.total });
      })
      .catch((err: Error) => {
        if (!cancelled) setResult({ tag, sort, page, error: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [tag, sort, page]);

  const isFresh =
    result != null &&
    result.tag === tag &&
    result.sort === sort &&
    result.page === page;
  const loading = !isFresh;
  const wallets = isFresh ? (result.wallets ?? []) : [];
  const total = isFresh ? (result.total ?? 0) : 0;
  const error = isFresh ? (result.error ?? null) : null;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Reset to page 1 whenever the filter changes, otherwise the user can land
  // on a page that doesn't exist for the new tag (e.g. page 5 with only 30
  // matching rows). Sort changes don't change row count, so they don't need
  // a reset — but we reset anyway for consistency.
  const handleTagChange = (next: string | null) => {
    setTag(next);
    setPage(1);
  };
  const handleSortChange = (next: SortField) => {
    setSort(next);
    setPage(1);
  };

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      <SiteHeader active="explore" />

      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-10">
        {/* Page header */}
        <div className="flex items-end justify-between">
          <div>
            <div className="font-mono text-[11px] uppercase tracking-[0.25em] text-zinc-500">
              Wallet Explorer
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight">
              Daily-classified Solana wallets
            </h1>
          </div>
          <div className="font-mono text-xs text-zinc-500">
            {loading ? "…" : `${total.toLocaleString("en-US")} wallets`}
          </div>
        </div>

        <FilterTabs value={tag} onChange={handleTagChange} />

        {error ? (
          <div className="rounded-md border border-red-900/50 bg-red-950/20 p-4 font-mono text-sm text-red-300">
            failed to load wallets: {error}
          </div>
        ) : (
          <>
            <WalletTable
              wallets={wallets}
              sort={sort}
              onSortChange={handleSortChange}
              loading={loading}
            />
            <div className="flex items-center justify-between font-mono text-xs text-zinc-500">
              <span>
                {total > 0
                  ? `page ${page} / ${totalPages}`
                  : "—"}
              </span>
              <WalletPagination
                page={page}
                totalPages={totalPages}
                onPageChange={setPage}
              />
            </div>
          </>
        )}
      </div>

      {/* Floating chat trigger — hidden while the drawer is open */}
      {!chatOpen && (
        <button
          type="button"
          onClick={() => setChatOpen(true)}
          className="fixed bottom-6 right-6 z-40 rounded-md border border-emerald-500/60 bg-zinc-950/90 px-4 py-2.5 font-mono text-sm text-emerald-400 shadow-lg backdrop-blur transition-colors hover:bg-emerald-500/10"
        >
          &gt;_ ASK AI
        </button>
      )}

      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
    </main>
  );
}
