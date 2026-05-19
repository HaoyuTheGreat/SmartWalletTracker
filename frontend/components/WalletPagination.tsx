"use client";

/**
 * WalletPagination — controlled prev/next + numeric page picker.
 *
 * Window logic: always show first page, last page, and ±1 around the current
 * page; collapse runs of skipped pages into a single ellipsis. Mirrors the
 * pattern shadcn's own example uses.
 *
 * Uses `<a href="#" onClick>` (not `<button>`) because the underlying shadcn
 * `PaginationLink` is hard-coded to render an anchor via `asChild`. The
 * `preventDefault` keeps the URL clean.
 */

import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

export interface WalletPaginationProps {
  page: number; // 1-indexed
  totalPages: number;
  onPageChange: (next: number) => void;
}

export function WalletPagination({
  page,
  totalPages,
  onPageChange,
}: WalletPaginationProps) {
  if (totalPages <= 1) return null;

  const pages = buildPageList(page, totalPages);

  const go = (n: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    if (n >= 1 && n <= totalPages && n !== page) onPageChange(n);
  };

  return (
    <Pagination>
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious
            href="#"
            onClick={go(page - 1)}
            aria-disabled={page === 1}
            className={page === 1 ? "pointer-events-none opacity-50" : ""}
          />
        </PaginationItem>
        {pages.map((p, i) =>
          p === "ellipsis" ? (
            <PaginationItem key={`e-${i}`}>
              <PaginationEllipsis />
            </PaginationItem>
          ) : (
            <PaginationItem key={p}>
              <PaginationLink
                href="#"
                isActive={p === page}
                onClick={go(p)}
              >
                {p}
              </PaginationLink>
            </PaginationItem>
          ),
        )}
        <PaginationItem>
          <PaginationNext
            href="#"
            onClick={go(page + 1)}
            aria-disabled={page === totalPages}
            className={
              page === totalPages ? "pointer-events-none opacity-50" : ""
            }
          />
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  );
}

function buildPageList(
  page: number,
  total: number,
): Array<number | "ellipsis"> {
  // Small totals: show every page, no ellipsis.
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const out: Array<number | "ellipsis"> = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(total - 1, page + 1);
  if (start > 2) out.push("ellipsis");
  for (let i = start; i <= end; i++) out.push(i);
  if (end < total - 1) out.push("ellipsis");
  out.push(total);
  return out;
}
