"""Deterministic accounting tests for fresh-forward Paper helpers."""
import pandas as pd

from scripts.run_funding_crowding_forward_paper import realized_funding


def test_realized_funding_excludes_entry_and_includes_exit_settlement():
    index = pd.to_datetime([
        "2025-01-01 00:00Z", "2025-01-01 08:00Z", "2025-01-01 16:00Z",
    ])
    funding = pd.Series([.001, .002, -.001], index=index)
    assert realized_funding(funding, index[0], index[2], "LONG") == -.001
    assert realized_funding(funding, index[0], index[2], "SHORT") == .001


if __name__ == "__main__":
    test_realized_funding_excludes_entry_and_includes_exit_settlement()
    print("PASS test_realized_funding_excludes_entry_and_includes_exit_settlement")
