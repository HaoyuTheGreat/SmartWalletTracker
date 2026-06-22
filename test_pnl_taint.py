"""Test transfer-taint exclusion in PnL (filter_traders).

No pytest needed — run directly:
    .venv/bin/python test_pnl_taint.py

Pure-function test (calc_performance + transferred_token_mints), no BQ / API.
"""
import filter_traders as ft


def pos(bought, sold, sol_in, sol_out, symbol="X"):
    return {"symbol": symbol, "bought": bought, "sold": sold,
            "sol_in": sol_in, "sol_out": sol_out}


def run():
    # 1. A clean closed position is counted (PnL = sol_out - sol_in).
    perf = ft.calc_performance({"CLEAN": pos(100, 100, 5, 8)})
    assert perf["closed_positions"] == 1, perf
    assert perf["total_pnl_sol"] == 3.0, perf
    print("PASS  clean position counted (PnL +3)")

    # 2. A tainted position is EXCLUDED from PnL/win_rate and NOT counted inflated,
    #    while a clean one alongside it still counts.
    positions = {
        "CLEAN": pos(100, 100, 5, 8),          # +3, clean
        "TAINTED": pos(100, 600, 1, 20),       # sold >> bought, would be +19 + inflated
    }
    perf = ft.calc_performance(positions, tainted_mints={"TAINTED"})
    assert perf["closed_positions"] == 1, perf          # only the clean one
    assert perf["total_pnl_sol"] == 3.0, perf           # tainted +19 excluded
    assert perf["inflated_positions"] == 0, perf        # tainted not counted inflated
    print("PASS  tainted position excluded from PnL + not inflated")

    # 3. A NON-tainted sold>bought position IS inflated (genuine truncation signal
    #    that still drives data_clipped).
    perf = ft.calc_performance({"TRUNC": pos(100, 600, 1, 20)})
    assert perf["inflated_positions"] == 1, perf
    print("PASS  non-tainted sold>bought → inflated (truncation)")

    # 4. transferred_token_mints: catches a token transfer touching the wallet,
    #    ignores SOL/WSOL and transfers that don't involve the wallet.
    transfers = [
        {"tokenTransfers": [{"mint": "TAINTED", "fromUserAccount": "WALLET",
                             "toUserAccount": "OTHER", "tokenAmount": 500}]},
        {"tokenTransfers": [{"mint": ft.WSOL_MINT, "fromUserAccount": "WALLET",
                             "toUserAccount": "X"}]},          # SOL → excluded
        {"tokenTransfers": [{"mint": "UNRELATED", "fromUserAccount": "A",
                             "toUserAccount": "B"}]},          # wallet not involved
    ]
    mints = ft.transferred_token_mints(transfers, "WALLET")
    assert mints == {"TAINTED"}, mints
    print("PASS  transferred_token_mints: token transfer caught, SOL + unrelated skipped")

    print("\nALL TESTS PASSED ✅")


if __name__ == "__main__":
    run()
