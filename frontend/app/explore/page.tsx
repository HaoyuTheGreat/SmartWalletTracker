"use client";

/**
 * Explorer page — paginated wallet list with tag filter + sort.
 *
 * Client component: the whole view is interactive. Changing tag, sort, or
 * page triggers a fresh GET /api/wallets call. Tag/sort changes reset to
 * page 1, otherwise the user could end up on page 9 of a 3-page result set.
 *
 * `result` is stamped with the params it was fetched for, so we can derive
 * `loading` = "settled fetch params don't match current params yet" — avoids
 * synchronous setState inside the effect, which would cause cascading renders.
 *
 * Stats cards are intentionally omitted per product decision — the raw
 * numbers are sensitive and the table already conveys scale. Day 6 will add
 * a floating chat button wired to postChat().
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { FilterTabs } from "@/components/FilterTabs";
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
  const wallets = isFresh ? result.wallets ?? [] : [];
  const total = isFresh ? result.total ?? 0 : 0;
  const error = isFresh ? result.error ?? null : null;
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
    <main className="min-h-screen bg-black px-6 py-10 text-zinc-100">
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">Explorer</h1>
            <p className="text-sm text-zinc-400">
              Daily-classified Solana wallets. Filter by tag, sort by any column.
            </p>
          </div>
          <Link
            href="/"
            className="text-sm text-zinc-400 hover:text-amber-300"
          >
            ← Home
          </Link>
        </header>

        <FilterTabs value={tag} onChange={handleTagChange} />

        {error ? (
          <div className="rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">
            Failed to load wallets: {error}
          </div>
        ) : (
          <>
            <WalletTable
              wallets={wallets}
              sort={sort}
              onSortChange={handleSortChange}
              loading={loading}
            />
            <div className="flex items-center justify-between text-sm text-zinc-500">
              <span>
                {total > 0
                  ? `Page ${page} of ${totalPages} · ${total.toLocaleString()} wallets`
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
    </main>
  );
}
