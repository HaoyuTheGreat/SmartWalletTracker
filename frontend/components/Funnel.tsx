/**
 * Funnel — the homepage signature element. Three bars showing the discovery
 * funnel (scanned → tracked → smart), bar widths on a sqrt scale so the
 * smallest tier stays visible next to a 30× larger top tier.
 *
 * Server-renderable: no hooks, no browser APIs. The one-shot fill animation
 * is pure CSS (`animate-bar-grow` in globals.css) with staggered delays.
 */

interface FunnelProps {
  scanned: number;
  tracked: number;
  smart: number;
}

const fmt = (n: number) => n.toLocaleString("en-US");

function pct(part: number, whole: number): string {
  if (whole <= 0) return "—";
  return `${((part / whole) * 100).toFixed(1)}%`;
}

export function Funnel({ scanned, tracked, smart }: FunnelProps) {
  // sqrt scale: linear would render the smart bar at ~3% width (invisible).
  const maxSqrt = Math.sqrt(Math.max(scanned, 1));
  const width = (n: number) =>
    `${Math.max((Math.sqrt(Math.max(n, 0)) / maxSqrt) * 100, 2)}%`;

  const rows = [
    {
      label: "scanned",
      count: scanned,
      conv: null as string | null,
      accent: false,
    },
    {
      label: "tracked",
      count: tracked,
      conv: pct(tracked, scanned),
      accent: false,
    },
    {
      label: "smart",
      count: smart,
      conv: pct(smart, tracked),
      accent: true,
    },
  ];

  return (
    <div className="flex flex-col gap-3">
      {rows.map((row, i) => (
        <div
          key={row.label}
          className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-4 md:grid-cols-[8rem_1fr_4rem]"
        >
          <div className="text-right font-mono">
            <span
              className={
                row.accent
                  ? "text-lg text-emerald-400"
                  : "text-lg text-zinc-100"
              }
            >
              {fmt(row.count)}
            </span>
            <span className="block text-[10px] uppercase tracking-[0.2em] text-zinc-500">
              {row.label}
            </span>
          </div>

          <div className="relative h-6">
            <div
              className={`animate-bar-grow absolute inset-y-1 left-0 rounded-sm ${
                row.accent ? "bg-emerald-500" : "bg-zinc-700"
              }`}
              style={{ width: width(row.count), animationDelay: `${i * 150}ms` }}
            />
          </div>

          <div className="font-mono text-xs text-zinc-500">{row.conv ?? ""}</div>
        </div>
      ))}

      <div className="mt-1 text-right font-mono text-xs text-zinc-600">
        overall conversion {pct(smart, scanned)}
      </div>
    </div>
  );
}
