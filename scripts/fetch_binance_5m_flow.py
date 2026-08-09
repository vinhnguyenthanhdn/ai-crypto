"""Tải Binance public 5m kline và dựng taker-flow snapshot có checksum."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import urllib.request
import urllib.error
import zipfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


BASE = "https://data.binance.vision/data"


def _months(start, end):
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        if month_end < end:
            yield "monthly", cursor.strftime("%Y-%m")
        cursor = next_month


def _daily_after_months(start, end):
    cursor = date(end.year, end.month, 1)
    while cursor <= end:
        if cursor >= start:
            yield "daily", cursor.isoformat()
        cursor += timedelta(days=1)


def _download_verified(url):
    error = None
    for attempt in range(3):
        try:
            payload = urllib.request.urlopen(url, timeout=60).read()
            checksum_text = urllib.request.urlopen(url + ".CHECKSUM", timeout=60).read().decode().strip()
            break
        except (OSError, urllib.error.URLError) as exc:
            error = exc
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    else:  # pragma: no cover - defensive; loop either succeeds or raises.
        raise error
    expected = checksum_text.split()[0]
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum mismatch cho {url}: {actual} != {expected}")
    return payload, actual


def _timestamp_ms(raw):
    value = int(raw)
    return value // 1000 if value > 10**14 else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--market-path", default="spot", choices=("spot", "futures/um"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    archives = list(_months(start, end)) + list(_daily_after_months(start, end))
    rows, sources = {}, []
    for cadence, stamp in archives:
        filename = f"{args.symbol}-{args.interval}-{stamp}.zip"
        url = f"{BASE}/{args.market_path}/{cadence}/klines/{args.symbol}/{args.interval}/{filename}"
        try:
            payload, digest = _download_verified(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            sources.append({"url": url, "missing": True, "http_status": 404})
            continue
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = archive.namelist()[0]
            text = io.TextIOWrapper(archive.open(member), encoding="utf-8")
            count = 0
            for values in csv.reader(text):
                if not values or not values[0].isdigit():
                    continue
                ts = _timestamp_ms(values[0])
                day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                if day < start or day > end:
                    continue
                volume = float(values[5])
                taker_buy = float(values[9])
                rows[ts] = {
                    "ts": ts, "open": float(values[1]), "high": float(values[2]),
                    "low": float(values[3]), "close": float(values[4]),
                    "volume": volume, "taker_buy_base": taker_buy,
                    "trade_count": int(values[8]),
                    "taker_imbalance": (2 * taker_buy - volume) / volume if volume > 0 else 0.0,
                }
                count += 1
        sources.append({"url": url, "sha256": digest, "rows_in_range": count})
    ordered = [rows[key] for key in sorted(rows)]
    output = {
        "metadata": {
            "source": "Binance Public Data", "market_path": args.market_path,
            "symbol": args.symbol,
            "interval": args.interval, "start": args.start, "end": args.end,
            "columns": ["open", "high", "low", "close", "volume", "taker_buy_base", "trade_count", "taker_imbalance"],
            "archives": sources,
        },
        "rows": ordered,
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"out": str(out), "rows": len(ordered), "archives": len(sources), "first_ts": ordered[0]["ts"], "last_ts": ordered[-1]["ts"]}, indent=2))


if __name__ == "__main__":
    main()
