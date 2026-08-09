"""Deterministic accounting tests for BTC Spot forward Paper helpers."""
from scripts.run_btc_spot_trend_forward_paper import advance_portfolio


def main() -> None:
    result = advance_portfolio(0.5, 100.0, 110.0, 0.8, 250.0, 250.0)
    assert abs(result["gross_return"] - 0.05) < 1e-12
    assert abs(result["turnover"] - 0.3) < 1e-12
    assert abs(result["base_return"] - (0.05 - 0.3 * .0012)) < 1e-12
    assert abs(result["stress_return"] - (0.05 - 0.3 * .0024)) < 1e-12
    assert result["base_equity_usd"] > result["stress_equity_usd"]
    print("PASS test_advance_portfolio_cost_and_exposure")


if __name__ == "__main__":
    main()
