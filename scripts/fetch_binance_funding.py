"""Fetch paginated Binance USD-M perpetual funding history."""
import argparse
import gzip
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"


def timestamp_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    start, end = timestamp_ms(args.start), timestamp_ms(args.end)
    cursor, rows = start, {}
    while cursor <= end:
        query = urllib.parse.urlencode({
            "symbol": args.symbol, "startTime": cursor,
            "endTime": end, "limit": 1000,
        })
        error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(f"{ENDPOINT}?{query}", timeout=30) as response:
                    batch = json.load(response)
                break
            except OSError as exc:
                error = exc
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        else:
            raise error
        if not batch:
            break
        for item in batch:
            ts = int(item["fundingTime"])
            rows[ts] = {"ts": ts, "funding_rate": float(item["fundingRate"])}
        next_cursor = int(batch[-1]["fundingTime"]) + 1
        if next_cursor <= cursor:
            raise RuntimeError("Funding pagination did not advance")
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(.1)
    ordered = [rows[key] for key in sorted(rows) if key <= end]
    output = {"metadata": {"source": ENDPOINT, "symbol": args.symbol, "start": args.start, "end": args.end}, "rows": ordered}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as target:
        json.dump(output, target, separators=(",", ":"))
    print(json.dumps({"out": str(out), "rows": len(ordered), "first_ts": ordered[0]["ts"], "last_ts": ordered[-1]["ts"]}))


if __name__ == "__main__":
    main()
