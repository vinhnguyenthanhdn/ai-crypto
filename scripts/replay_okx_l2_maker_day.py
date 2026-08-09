"""Replay timestamped directional signals against OKX L2 and public trades.

This is an execution-quality probe, not a strategy backtest.  It measures
post-only fill rate and post-fill markout for signals produced causally by the
slower trend/sentiment engine.
"""
import argparse
import csv
import hashlib
import json
import tarfile
import zipfile
from collections import deque
from pathlib import Path

from src.backtest.l2_maker import AggregateBook, signed_markout_bps


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_trades(path: Path) -> deque:
    with zipfile.ZipFile(path) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as raw:
            rows = csv.DictReader((line.decode("utf-8") for line in raw))
            return deque((int(row["created_time"]), row["side"],
                          float(row["price"]), float(row["size"])) for row in rows)


def parse_signal(value: str) -> dict:
    raw_timestamp, side = value.rsplit(":", 1)
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise argparse.ArgumentTypeError("signal side must be BUY or SELL")
    return {"timestamp_ms": int(raw_timestamp), "side": side}


def summarize(records: list[dict], horizons_ms: tuple[int, ...]) -> dict:
    filled = [record for record in records if record["filled_quantity"] > 0]
    summary = {
        "signals": len(records),
        "any_fill": len(filled),
        "complete_fill": sum(record["complete"] for record in records),
        "fill_rate_pct": 100 * len(filled) / len(records) if records else 0,
    }
    for horizon in horizons_ms:
        values = [record["markout_bps"].get(str(horizon)) for record in filled]
        values = [value for value in values if value is not None]
        summary[f"markout_{horizon // 1000}s_mean_bps"] = (
            sum(values) / len(values) if values else None
        )
    return summary


def grouped_summary(records: list[dict], horizons_ms: tuple[int, ...]) -> dict:
    groups = {}
    for record in records:
        key = f"offset_{record['offset_bps']:g}_queue_{record['queue_multiplier']:g}"
        groups.setdefault(key, []).append(record)
    return {key: summarize(values, horizons_ms) for key, values in groups.items()}


def replay(l2_path: Path, trade_path: Path, signals: list[dict], quantity: float,
           ttl_ms: int, queue_multipliers: tuple[float, ...],
           offsets_bps: tuple[float, ...], horizons_ms: tuple[int, ...]) -> dict:
    trades = load_trades(trade_path)
    pending_signals = deque(sorted(signals, key=lambda item: item["timestamp_ms"]))
    book = AggregateBook()
    active = []
    records = []
    last_trade_ts = -1

    def apply_trade(timestamp_ms: int, side: str, price: float, size: float) -> None:
        nonlocal last_trade_ts
        if timestamp_ms < last_trade_ts:
            raise ValueError("trade archive is not timestamp ordered")
        last_trade_ts = timestamp_ms
        for state in active:
            state["order"].apply_trade(side, price, size, timestamp_ms)

    with tarfile.open(l2_path, "r|gz") as archive:
        member = archive.next()
        if member is None:
            raise ValueError("empty L2 archive")
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("L2 archive member is not readable")
        for raw_line in source:
            event = json.loads(raw_line)
            now = int(event["ts"])
            while trades and trades[0][0] <= now:
                apply_trade(*trades.popleft())

            book.update(event["action"], event["bids"], event["asks"], now)
            if book.best_bid is None or book.best_ask is None:
                continue
            mid = (book.best_bid + book.best_ask) / 2

            while pending_signals and pending_signals[0]["timestamp_ms"] <= now:
                signal = pending_signals.popleft()
                for offset_bps in offsets_bps:
                    for queue_multiplier in queue_multipliers:
                        order = book.post_passive(
                            signal["side"], quantity, now, offset_bps, queue_multiplier
                        )
                        active.append({"signal": signal, "order": order,
                                       "offset_bps": offset_bps,
                                       "queue_multiplier": queue_multiplier,
                                       "markout_bps": {}})

            remaining = []
            for state in active:
                order = state["order"]
                if order.first_fill_at_ms is not None:
                    for horizon in horizons_ms:
                        key = str(horizon)
                        if key not in state["markout_bps"] and now >= order.first_fill_at_ms + horizon:
                            state["markout_bps"][key] = signed_markout_bps(
                                order.side, order.average_fill_price, mid
                            )
                expired = now >= order.created_at_ms + ttl_ms
                markouts_done = all(str(horizon) in state["markout_bps"] for horizon in horizons_ms)
                if (expired and order.filled_quantity == 0) or (order.complete and markouts_done):
                    records.append({
                        **state["signal"], "posted_at_ms": order.created_at_ms,
                        "limit_price": order.price, "initial_quantity": order.quantity,
                        "offset_bps": state["offset_bps"],
                        "queue_multiplier": state["queue_multiplier"],
                        "filled_quantity": order.filled_quantity, "complete": order.complete,
                        "first_fill_at_ms": order.first_fill_at_ms,
                        "last_fill_at_ms": order.last_fill_at_ms,
                        "markout_bps": state["markout_bps"],
                    })
                else:
                    remaining.append(state)
            active = remaining

    for state in active:
        order = state["order"]
        records.append({
            **state["signal"], "posted_at_ms": order.created_at_ms,
            "limit_price": order.price, "initial_quantity": order.quantity,
            "offset_bps": state["offset_bps"],
            "queue_multiplier": state["queue_multiplier"],
            "filled_quantity": order.filled_quantity, "complete": order.complete,
            "first_fill_at_ms": order.first_fill_at_ms,
            "last_fill_at_ms": order.last_fill_at_ms,
            "markout_bps": state["markout_bps"],
        })
    return {
        "contract": {
            "fill": "behind displayed L2 queue; cancellations do not advance queue",
            "post": "touch_or_more_passive_offset", "quantity_contracts": quantity,
            "ttl_ms": ttl_ms, "queue_multipliers": queue_multipliers,
            "offsets_bps": offsets_bps,
            "markout_horizons_ms": horizons_ms,
        },
        "source": {
            "l2_file": l2_path.name, "l2_sha256": sha256(l2_path),
            "trade_file": trade_path.name, "trade_sha256": sha256(trade_path),
        },
        "summary": grouped_summary(records, horizons_ms), "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2", required=True, type=Path)
    parser.add_argument("--trades", required=True, type=Path)
    parser.add_argument("--signal", required=True, action="append", type=parse_signal)
    parser.add_argument("--quantity", type=float, default=1.0)
    parser.add_argument("--ttl-ms", type=int, default=60_000)
    parser.add_argument("--queue-multiplier", type=float, action="append")
    parser.add_argument("--offset-bps", type=float, action="append")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = replay(args.l2, args.trades, args.signal, args.quantity, args.ttl_ms,
                    tuple(args.queue_multiplier or (1.0,)),
                    tuple(args.offset_bps or (0.0,)),
                    (60_000, 300_000, 900_000, 3_600_000))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
