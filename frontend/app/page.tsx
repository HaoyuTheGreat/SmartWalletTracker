import Link from "next/link";

import { DigitMeteors } from "@/components/DigitMeteors";
import { Button } from "@/components/ui/button";

/**
 * HomePage — splash screen with layered backgrounds.
 *
 * Layer order (back → front):
 *   -z-20  background: radial-gradient warm spotlight + faint amber grid
 *   -z-10  DigitMeteors canvas animation
 *    z-10  content (heading + CTA)
 */
export default function HomePage() {
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden">
      {/* Background layer: warm radial spotlight at center + faint amber grid.
          `fixed` (not absolute) so it stays put during overscroll, matching
          the canvas which is also fixed. */}
      <div
        className="fixed inset-0 -z-20"
        style={{
          backgroundImage: `
            radial-gradient(ellipse at center, rgba(80, 50, 10, 0.35) 0%, rgba(0, 0, 0, 0.95) 65%),
            linear-gradient(rgba(184, 134, 32, 0.05) 1px, transparent 1px),
            linear-gradient(90deg, rgba(184, 134, 32, 0.05) 1px, transparent 1px)
          `,
          backgroundSize: "100% 100%, 48px 48px, 48px 48px",
        }}
      />

      <DigitMeteors />

      <div className="relative z-10 flex flex-col items-center gap-7 px-6 text-center">
        {/* Eyebrow with live indicator. Numbers are hardcoded for now;
            Day 5 will wire this to GET /api/stats/dashboard. */}
        <div className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.3em] text-zinc-400">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
          </span>
          <span>Live · 1,161 wallets · Updated today</span>
        </div>

        {/* Main heading. The "Explorer" word gets the gradient + occasional
            RGB-split glitch animation (defined in globals.css). */}
        <h1 className="text-6xl font-bold leading-[0.95] tracking-tight text-white md:text-8xl">
          SmartWallets{" "}
          <span className="animate-glitch inline-block bg-linear-to-r from-amber-300 via-yellow-400 to-amber-500 bg-clip-text text-transparent">
            Explorer
          </span>
        </h1>

        {/* Punchy subtitle with real numbers highlighted in amber. */}
        <p className="max-w-2xl text-xl leading-relaxed text-zinc-300 md:text-2xl">
          Cut through{" "}
          <span className="font-semibold text-amber-300">5,000</span>{" "}
          wallet candidates to find the{" "}
          <span className="font-semibold text-amber-300">111</span>{" "}
          trading smart.
        </p>

        {/* Secondary tag line — small, muted, monospace · separators. */}
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-zinc-500">
          Daily-curated · AI-queryable · On-chain data
        </p>

        {/* CTA button with breathing glow */}
        <Button
          asChild
          size="lg"
          className="animate-breathe-glow mt-2 bg-linear-to-br from-amber-400 to-amber-600 px-8 py-6 text-lg font-semibold text-black hover:from-amber-300 hover:to-amber-500"
        >
          <Link href="/explore">Start Explore →</Link>
        </Button>
      </div>
    </main>
  );
}
