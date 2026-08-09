"""Tải funding và futures metrics BTCUSDT từ Binance Public Data có checksum."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


BASE = "https://data.binance.vision/data/futures/um"


def _download_verified(url, cache_dir):
    cache_key = hashlib.sha256(url.encode()).hexdigest()
    payload_path, digest_path = cache_dir / f"{cache_key}.zip", cache_dir / f"{cache_key}.sha256"
    if payload_path.exists() and digest_path.exists():
        payload = payload_path.read_bytes()
        expected = digest_path.read_text(encoding="utf-8").strip()
        if hashlib.sha256(payload).hexdigest() == expected:
            return payload, expected
    last_error = None
    for attempt in range(4):
        try:
            payload = urllib.request.urlopen(url, timeout=60).read()
            checksum = urllib.request.urlopen(url + ".CHECKSUM", timeout=60).read().decode().split()[0]
            break
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(0.5 * (2 ** attempt))
    else:
        raise last_error
    actual = hashlib.sha256(payload).hexdigest()
    if actual != checksum:
        raise ValueError(f"Checksum mismatch cho {url}: {actual} != {checksum}")
    payload_path.write_bytes(payload)
    digest_path.write_text(actual, encoding="utf-8")
    return payload, actual


def _month_stamps(start, end):
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        if next_month - timedelta(days=1) < end:
            yield cursor.strftime("%Y-%m")
        cursor = next_month


def _daily_stamps(start, end):
    cursor = start
    while cursor <= end:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def _request(spec, cache_dir):
    kind, cadence, stamp, url = spec
    try:
        payload, digest = _download_verified(url, cache_dir)
        return spec, payload, digest, None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return spec, None, None, 404
        raise


def _read_zip(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as raw:
            yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--archive-cache", default="data/backtests/binance_derivatives_archives")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    specs = []
    for stamp in _month_stamps(start, end):
        name = f"{args.symbol}-fundingRate-{stamp}.zip"
        specs.append(("funding", "monthly", stamp, f"{BASE}/monthly/fundingRate/{args.symbol}/{name}"))
    current_month = date(end.year, end.month, 1)
    for stamp in _daily_stamps(max(start, current_month), end):
        name = f"{args.symbol}-fundingRate-{stamp}.zip"
        specs.append(("funding", "daily", stamp, f"{BASE}/daily/fundingRate/{args.symbol}/{name}"))
    for stamp in _daily_stamps(start, end):
        name = f"{args.symbol}-metrics-{stamp}.zip"
        specs.append(("metrics", "daily", stamp, f"{BASE}/daily/metrics/{args.symbol}/{name}"))

    cache_dir = Path(args.archive_cache)
    cache_dir.mkdir(parents=True, exist_ok=True)
    responses = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_request, spec, cache_dir) for spec in specs]
        for future in as_completed(futures):
            responses.append(future.result())

    funding, metrics, sources = {}, {}, []
    for spec, payload, digest, error in sorted(responses, key=lambda item: (item[0][0], item[0][2])):
        kind, cadence, stamp, url = spec
        if error:
            sources.append({"kind": kind, "url": url, "missing": True, "http_status": error})
            continue
        count = 0
        for row in _read_zip(payload):
            if kind == "funding":
                ts = int(row["calc_time"])
                day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                if start <= day <= end:
                    funding[ts] = {
                        "ts": ts,
                        "funding_interval_hours": int(row["funding_interval_hours"]),
                        "funding_rate": float(row["last_funding_rate"]),
                    }
                    count += 1
            else:
                ts = int(datetime.strptime(row["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
                day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                if start <= day <= end:
                    try:
                        metrics[ts] = {
                            "ts": ts,
                            "open_interest": float(row["sum_open_interest"]),
                            "open_interest_value": float(row["sum_open_interest_value"]),
                            "top_accounts_long_short": float(row["count_toptrader_long_short_ratio"]),
                            "top_positions_long_short": float(row["sum_toptrader_long_short_ratio"]),
                            "global_long_short": float(row["count_long_short_ratio"]),
                            "taker_long_short": float(row["sum_taker_long_short_vol_ratio"]),
                        }
                    except (TypeError, ValueError):
                        continue
                    count += 1
        sources.append({"kind": kind, "cadence": cadence, "url": url, "sha256": digest, "rows_in_range": count})

    output = {
        "metadata": {"source": "Binance Public Data", "symbol": args.symbol, "start": args.start, "end": args.end, "archives": sources},
        "funding": [funding[key] for key in sorted(funding)],
        "metrics": [metrics[key] for key in sorted(metrics)],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"out": str(out), "funding_rows": len(funding), "metric_rows": len(metrics), "archives": len(sources), "missing": sum(1 for item in sources if item.get("missing"))}, indent=2))


if __name__ == "__main__":
    main()
