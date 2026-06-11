import Link from "next/link";

import { Funnel } from "@/components/Funnel";
import { SiteHeader } from "@/components/SiteHeader";
import type { DashboardStats } from "@/lib/types";

/**
 * HomePage — quant-terminal style landing.
 *
 * Server component: stats are fetched server-side from the FastAPI service
 * with ISR (1h revalidate), so visitors get real numbers pre-rendered with
 * zero client-side loading state and no CORS involvement. If the API is
 * unreachable (cold start timeout, outage) we fall back to a recent static
 * snapshot and mark the live-dot grey — the homepage never blanks.
 */

const GITHUB_URL = "https://github.com/HaoyuTheGreat/SmartWalletTracker";
// Earliest swap in the warehouse — fixed historical fact, used for the
// "history depth" stat without needing an extra API field.
const DATA_START_ISO = "2022-05-08";

// Recent real snapshot, used only when the stats API is unreachable.
const FALLBACK: DashboardStats = {
  candidates_scanned: 5018,
  wallets_tracked: 1461,
  wallets_classified: 1351,
  smart_candidates: 165,
  total_swaps: 219397,
  unique_signatures: 219120,
  raw_data_size_mb: 3705,
  latest_classification_at: null,
  latest_swap_at: null,
};

async function getStats(): Promise<{ stats: DashboardStats; live: boolean }> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (base) {
    try {
      const res = await fetch(`${base}/api/stats/dashboard`, {
        next: { revalidate: 3600 },
      });
      if (res.ok) {
        // Merge over the fallback so a missing field (e.g. an older API
        // revision still deploying) degrades to a snapshot value instead of
        // crashing the server render with undefined.
        const json = (await res.json()) as Partial<DashboardStats>;
        return { stats: { ...FALLBACK, ...json }, live: true };
      }
    } catch {
      // unreachable — fall through to snapshot
    }
  }
  return { stats: FALLBACK, live: false };
}

function hoursSince(iso: string | null): number | null {
  if (!iso) return null;
  return (Date.now() - new Date(iso).getTime()) / 36e5;
}

function relTime(iso: string | null): string {
  const h = hoursSince(iso);
  if (h == null) return "—";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m ago`;
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function historyDepth(): string {
  const years =
    (Date.now() - new Date(DATA_START_ISO).getTime()) / (365.25 * 24 * 36e5);
  return `${Math.floor(years)}+ yrs`;
}

export default async function HomePage() {
  const { stats, live } = await getStats();

  const classifiedHours = hoursSince(stats.latest_classification_at);
  // The pipeline runs daily — anything older than 26h means a run was missed.
  const stale = classifiedHours != null && classifiedHours > 26;

  const gb =
    stats.raw_data_size_mb != null
      ? `${(stats.raw_data_size_mb / 1024).toFixed(1)} GB`
      : "—";

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      <SiteHeader />

      {/* Hero */}
      <section className="mx-auto max-w-5xl px-6 pb-14 pt-20">
        <div className="flex items-center gap-2.5 font-mono text-[11px] uppercase tracking-[0.25em] text-zinc-500">
          <span className="relative flex h-2 w-2">
            {live && !stale && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            )}
            <span
              className={`relative inline-flex h-2 w-2 rounded-full ${
                !live ? "bg-zinc-600" : stale ? "bg-amber-400" : "bg-emerald-500"
              }`}
            />
          </span>
          <span>
            {!live ? "offline snapshot" : stale ? "pipeline delayed" : "live"} ·
            Solana smart-money intelligence
          </span>
        </div>

        <h1 className="mt-6 max-w-2xl text-5xl font-semibold leading-[1.05] tracking-tight md:text-6xl">
          Tracking the wallets that actually win.
        </h1>

        <p className="mt-5 max-w-xl leading-relaxed text-zinc-400">
          An autonomous pipeline scans thousands of Solana wallets every day
          and surfaces the few with consistently profitable trading history.
        </p>
      </section>

      {/* Funnel */}
      <section className="mx-auto max-w-5xl px-6 pb-14">
        <div className="mb-5 font-mono text-[11px] uppercase tracking-[0.25em] text-zinc-500">
          The funnel
        </div>
        <Funnel
          scanned={stats.candidates_scanned}
          tracked={stats.wallets_tracked}
          smart={stats.smart_candidates}
        />
      </section>

      {/* Stat strip */}
      <section className="mx-auto max-w-5xl px-6 pb-14">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-zinc-800 bg-zinc-800 md:grid-cols-4">
          <StatCell
            value={stats.total_swaps.toLocaleString("en-US")}
            label="swap transactions"
          />
          <StatCell value={gb} label="raw on-chain data" />
          <StatCell value={historyDepth()} label="history depth" />
          <StatCell
            value={relTime(stats.latest_classification_at)}
            label="last classified"
            warn={stale}
          />
        </div>
      </section>

      {/* CTAs */}
      <section className="mx-auto flex max-w-5xl items-center gap-4 px-6 pb-24">
        <Link
          href="/explore"
          className="rounded-md bg-emerald-500 px-6 py-3 font-medium text-zinc-950 transition-colors hover:bg-emerald-400"
        >
          Open Explorer →
        </Link>
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-md border border-zinc-700 px-6 py-3 text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100"
        >
          View source ↗
        </a>
      </section>

      {/* Footer */}
      <footer className="border-t border-zinc-800/80">
        <div className="mx-auto max-w-5xl px-6 py-5 font-mono text-xs text-zinc-600">
          Data: Helius · Dune · Binance — refreshed daily at 13:00 UTC
        </div>
      </footer>
    </main>
  );
}

function StatCell({
  value,
  label,
  warn = false,
}: {
  value: string;
  label: string;
  warn?: boolean;
}) {
  return (
    <div className="bg-zinc-950 p-5">
      <div
        className={`font-mono text-xl ${warn ? "text-amber-400" : "text-zinc-100"}`}
      >
        {value}
      </div>
      <div className="mt-1 text-[11px] uppercase tracking-[0.15em] text-zinc-500">
        {label}
      </div>
    </div>
  );
}
