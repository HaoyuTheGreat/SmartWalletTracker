import Link from "next/link";

/**
 * Explorer page — placeholder for Day 5.
 *
 * Day 5 will fill this with:
 *   - dashboard stats header (calls GET /api/stats/dashboard)
 *   - classification filter tabs (smart_candidate / market_maker / proxy_bot / ...)
 *   - sortable wallet table (calls GET /api/wallets?tag=&sort=&limit=)
 *   - floating "Ask AI" button that opens a chat dialog (Day 6)
 */
export default function ExplorePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-black px-6 text-center text-zinc-100">
      <h1 className="text-4xl font-semibold">Explorer</h1>
      <p className="max-w-md text-zinc-400">
        Coming on Day 5 — dashboard, classification filters, sortable wallet
        table, and an AI chat panel.
      </p>
      <Link
        href="/"
        className="text-cyan-400 underline hover:text-cyan-300"
      >
        ← Back to home
      </Link>
    </main>
  );
}
