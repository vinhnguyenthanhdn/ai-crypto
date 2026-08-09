#!/usr/bin/env python3
"""Plot the gross-edge distribution across every rejected strategy configuration.

Gross edge is the per-trade return *before* any fees or slippage:

    gross = mean_net_return_pct + round_trip_cost_pct

If a family of strategies were merely being killed by trading costs, its gross
edge would sit clearly above zero. This chart tests that directly, and is the
single most important result in the repository.

Reads the result artifacts in ``data/backtests/`` and writes a PNG. Artifacts are
not committed; regenerate them with the ``scripts/discover_*.py`` and
``scripts/analyze_*.py`` harness, or run this against your own results.

Usage:
    .venv/bin/python scripts/plot_gross_edge.py
    .venv/bin/python scripts/plot_gross_edge.py --out docs/assets/gross-edge.png
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "data" / "backtests"
DEFAULT_OUT = REPO_ROOT / "docs" / "assets" / "gross-edge.png"

# Round-trip cost assumed by the research harness when these artifacts were produced.
# See docs/execution-cost.md for the measured values that supersede this assumption.
ASSUMED_ROUND_TRIP_COST_PCT = 0.30

# Families whose artifacts report per-trade expectancy under `mean_net_return_pct`.
FAMILIES = {
    "historical_taker_flow_ablation_180d": "Taker flow",
    "derivatives_context_ablation_180d": "Derivatives context",
    "bottom_entry_rules_180d": "Bottom entry rules",
    "liquidity_sweep_entry_180d": "Liquidity sweep",
    "choch_entry_180d": "CHOCH entry",
    "trailing_breakout_entries_180d": "Trailing breakout",
    "wide_horizon_entries_180d": "Wide horizon",
    "triple_barrier_entry_model_180d": "Triple barrier model",
    "multivariate_entry_model_180d": "Multivariate model",
}


def collect_expectancies(node, out: list[float]) -> None:
    """Walk an artifact and collect every reported per-trade net expectancy."""
    if isinstance(node, dict):
        value = node.get("mean_net_return_pct")
        if isinstance(value, (int, float)):
            out.append(float(value))
        for child in node.values():
            collect_expectancies(child, out)
    elif isinstance(node, list):
        for child in node:
            collect_expectancies(child, out)


def load_family(artifact_dir: Path, stem: str) -> np.ndarray:
    path = artifact_dir / f"{stem}.json"
    if not path.exists():
        return np.array([])
    with path.open() as handle:
        payload = json.load(handle)
    values: list[float] = []
    collect_expectancies(payload, values)
    return np.asarray(values, dtype=float) + ASSUMED_ROUND_TRIP_COST_PCT


def build_figure(per_family: dict[str, np.ndarray], out_path: Path) -> None:
    combined = np.concatenate([v for v in per_family.values() if v.size])
    median = float(np.median(combined))

    fig, (ax_hist, ax_family) = plt.subplots(
        1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.35, 1]}
    )
    fig.patch.set_facecolor("white")

    clipped = np.clip(combined, -1.5, 1.5)
    ax_hist.hist(clipped, bins=90, color="#4C78A8", edgecolor="white", linewidth=0.3)
    ax_hist.axvline(0, color="#333333", linewidth=1.4, zorder=3)
    ax_hist.axvline(
        median,
        color="#E45756",
        linewidth=1.8,
        linestyle="--",
        zorder=4,
        label=f"median {median:+.3f}%",
    )
    ax_hist.set_title(
        f"Gross edge before any fees\n{combined.size:,} configurations, "
        f"{len(per_family)} strategy families",
        fontsize=12,
        fontweight="bold",
        loc="left",
    )
    ax_hist.set_xlabel("Return per trade before costs (%)")
    ax_hist.set_ylabel("Configurations")
    ax_hist.legend(frameon=False, fontsize=10)
    ax_hist.spines[["top", "right"]].set_visible(False)

    names = list(per_family)
    medians = [float(np.median(per_family[n])) for n in names]
    order = np.argsort(medians)
    names = [names[i] for i in order]
    medians = [medians[i] for i in order]
    colors = ["#E45756" if m <= 0 else "#54A24B" for m in medians]

    ax_family.barh(names, medians, color=colors, height=0.62, zorder=3)
    ax_family.axvline(0, color="#333333", linewidth=1.2, zorder=2)
    ax_family.axvline(
        ASSUMED_ROUND_TRIP_COST_PCT,
        color="#E45756",
        linewidth=2.0,
        zorder=4,
        label=f"round-trip cost to clear ({ASSUMED_ROUND_TRIP_COST_PCT:.2f}%)",
    )
    ax_family.axvspan(
        0, ASSUMED_ROUND_TRIP_COST_PCT, color="#E45756", alpha=0.07, zorder=1
    )
    # Same scale as the cost hurdle, so the comparison is honest rather than
    # a zoomed-in view that makes ~zero bars look meaningful.
    ax_family.set_xlim(-0.34, 0.34)
    ax_family.set_title(
        "Median gross edge by family, against the cost it must beat",
        fontsize=12,
        fontweight="bold",
        loc="left",
    )
    ax_family.set_xlabel("Return per trade before costs (%)")
    ax_family.tick_params(axis="y", labelsize=9)
    ax_family.legend(frameon=False, fontsize=9.5, loc="lower right")
    ax_family.spines[["top", "right", "left"]].set_visible(False)

    fig.text(
        0.5,
        -0.02,
        "Every family sits on zero, nowhere near the cost line. Trading costs are "
        "not what killed these strategies — there was no edge to begin with.",
        ha="center",
        fontsize=10.5,
        style="italic",
        color="#444444",
    )

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"wrote {out_path}")
    print(f"configurations: {combined.size:,}")
    print(f"median gross edge: {median:+.4f}%")
    print(f"share positive: {(combined > 0).mean() * 100:.1f}%")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    per_family: dict[str, np.ndarray] = {}
    for stem, label in FAMILIES.items():
        values = load_family(args.artifact_dir, stem)
        if values.size:
            per_family[label] = values
        else:
            print(f"skipped (artifact missing): {stem}")

    if not per_family:
        print(
            f"No artifacts found in {args.artifact_dir}. "
            "Result artifacts are not committed; regenerate them with the "
            "scripts/discover_*.py and scripts/analyze_*.py harness."
        )
        return 1

    build_figure(per_family, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
