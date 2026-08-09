"""Side-aware derivatives regime gates cho Fast Donchian candidate."""
import argparse
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.discover_fast_champion import _aggregate, _load  # noqa: E402
from scripts.discover_fast_breakout import _features, _gate, _metrics, _run  # noqa: E402


BASE_COST = 0.14
STRESS_COST = 0.30


def _attach(frame, path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    metrics = pd.DataFrame(raw["metrics"])
    metrics["ts"] = pd.to_datetime(metrics.ts, unit="ms")
    metrics = metrics.set_index("ts").sort_index()
    hourly = metrics.resample("1h", label="left", closed="left").last()
    hourly["oi_change_1h"] = hourly.open_interest.pct_change()
    hourly["oi_change_4h"] = hourly.open_interest.pct_change(4)
    hourly["oi_change_24h"] = hourly.open_interest.pct_change(24)
    hourly["global_change_4h"] = hourly.global_long_short.pct_change(4)
    hourly["top_change_4h"] = hourly.top_positions_long_short.pct_change(4)
    funding = pd.DataFrame(raw["funding"])
    funding["ts"] = pd.to_datetime(funding.ts, unit="ms")
    funding = funding.set_index("ts").sort_index()[["funding_rate"]]
    hourly = pd.merge_asof(
        hourly.reset_index().sort_values("ts"), funding.reset_index().sort_values("ts"),
        on="ts", direction="backward",
    ).set_index("ts")
    return frame.join(hourly[[
        "oi_change_1h", "oi_change_4h", "oi_change_24h", "global_change_4h",
        "top_change_4h", "taker_long_short", "global_long_short",
        "top_positions_long_short", "funding_rate",
    ]], how="left")


def _gates():
    def confirm(row, side): return row.taker_long_short > 1 if side == "LONG" else row.taker_long_short < 1
    def oi4(row, side): return row.oi_change_4h > 0
    def oi24(row, side): return row.oi_change_24h > 0
    def crowd(row, side): return row.global_long_short < 1.5 if side == "LONG" else row.global_long_short > 1.2
    def top_move(row, side): return row.top_change_4h > 0 if side == "LONG" else row.top_change_4h < 0
    def funding(row, side): return row.funding_rate <= .0001 if side == "LONG" else row.funding_rate >= -.0001
    return {
        "none": lambda row, side: True,
        "deriv_taker_confirm": confirm,
        "oi4_expansion": oi4,
        "oi24_expansion": oi24,
        "crowd_not_extreme": crowd,
        "top_position_move": top_move,
        "funding_not_extreme": funding,
        "oi4_taker": lambda row, side: oi4(row, side) and confirm(row, side),
        "oi24_taker": lambda row, side: oi24(row, side) and confirm(row, side),
        "oi4_crowd": lambda row, side: oi4(row, side) and crowd(row, side),
        "taker_funding": lambda row, side: confirm(row, side) and funding(row, side),
        "oi4_top": lambda row, side: oi4(row, side) and top_move(row, side),
    }


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--flow-cache',required=True); parser.add_argument('--context',required=True); parser.add_argument('--out',required=True); args=parser.parse_args()
    frame=_features(_aggregate(_load(args.flow_cache),'1h'),40,10)
    frame=_attach(frame,args.context)
    splits={"train":(pd.Timestamp('2024-08-07'),pd.Timestamp('2025-08-07')),"validation":(pd.Timestamp('2025-08-07'),pd.Timestamp('2026-02-07')),"test":(pd.Timestamp('2026-02-07'),pd.Timestamp('2026-08-07'))}
    candidates=[]
    for name,gate in _gates().items():
        train=_metrics(_run(frame,*splits['train'],2,72,gate,BASE_COST),*splits['train'],True)
        candidates.append({"gate":name,"metrics":{"train":train}})
    eligible=[x for x in candidates if _gate(x['metrics']['train'],True)]
    selected=max(eligible,key=lambda x:x['metrics']['train']['net_return_pct']) if eligible else None; stress={}
    if selected:
        gate=_gates()[selected['gate']]
        for name in ('validation','test'): selected['metrics'][name]=_metrics(_run(frame,*splits[name],2,72,gate,BASE_COST),*splits[name])
        for name,bounds in splits.items(): stress[name]=_metrics(_run(frame,*bounds,2,72,gate,STRESS_COST),*bounds)
    passed=bool(selected and all(_gate(selected['metrics'][n]) for n in ('validation','test')) and all(_gate(stress[n]) for n in splits))
    output={"passed":passed,"contract":{"timeframe":"1h","donchian_entry":40,"donchian_exit":10,"stop_atr":2,"max_hold_hours":72,"base_cost_pct":BASE_COST,"stress_cost_pct":STRESS_COST,"selection":"train_only"},"selected":selected,"cost_stress":stress,"train_eligible_count":len(eligible),"candidates":candidates}
    Path(args.out).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({k:output[k] for k in ('passed','train_eligible_count','selected','cost_stress')},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
