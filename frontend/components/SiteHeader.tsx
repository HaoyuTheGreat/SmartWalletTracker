import Link from "next/link";

/**
 * SiteHeader — shared top bar (wordmark + nav) for all pages, so the
 * terminal chrome stays identical between the landing page and the explorer.
 */

const GITHUB_URL = "https://github.com/HaoyuTheGreat/SmartWalletTracker";

export function SiteHeader({ active }: { active?: "explore" }) {
  return (
    <header className="border-b border-zinc-800/80">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Link
          href="/"
          className="font-mono text-sm tracking-widest text-zinc-100"
        >
          SWT<span className="animate-pulse text-emerald-400">_</span>
        </Link>
        <nav className="flex items-center gap-6 text-sm">
          <Link
            href="/explore"
            className={
              active === "explore"
                ? "text-zinc-100"
                : "text-zinc-400 transition-colors hover:text-zinc-100"
            }
          >
            Explorer
          </Link>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-zinc-400 transition-colors hover:text-zinc-100"
          >
            GitHub ↗
          </a>
        </nav>
      </div>
    </header>
  );
}
