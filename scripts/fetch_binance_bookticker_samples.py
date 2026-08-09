"""Stream Binance Futures bookTicker archives into compact 1-second samples."""
import argparse
import csv
import gzip
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path


URL = "https://data.binance.vision/data/futures/um/daily/bookTicker/{symbol}/{symbol}-bookTicker-{date}.zip"


def compact(symbol: str, date: str, output_dir: Path) -> dict:
    output = output_dir / f"{symbol.lower()}_bookticker_1s_{date}.json.gz"
    if output.exists():
        with gzip.open(output, "rt", encoding="utf-8") as source:
            cached = json.load(source)
        return {"date": date, "rows": len(cached["rows"]), "cached": True, "out": str(output)}
    url = URL.format(symbol=symbol, date=date)
    with tempfile.TemporaryDirectory(prefix="bookticker-") as directory:
        archive = Path(directory) / "source.zip"
        urllib.request.urlretrieve(url, archive)
        by_second = {}
        with zipfile.ZipFile(archive) as bundle:
            filename = bundle.namelist()[0]
            with bundle.open(filename) as raw:
                reader = csv.DictReader((line.decode("utf-8") for line in raw))
                for row in reader:
                    timestamp = int(row["transaction_time"])
                    by_second[timestamp // 1000] = [
                        timestamp, float(row["best_bid_price"]), float(row["best_bid_qty"]),
                        float(row["best_ask_price"]), float(row["best_ask_qty"]),
                    ]
        payload = {
            "metadata": {"source": url, "sampling": "last update per UTC second"},
            "columns": ["ts", "bid", "bid_qty", "ask", "ask_qty"],
            "rows": [by_second[key] for key in sorted(by_second)],
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(output, "wt", encoding="utf-8") as target:
            json.dump(payload, target, separators=(",", ":"))
    return {"date": date, "rows": len(payload["rows"]), "cached": False, "out": str(output)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--out-dir", default="data/backtests/bookticker")
    args = parser.parse_args()
    for date in args.dates:
        print(json.dumps(compact(args.symbol, date, Path(args.out_dir))), flush=True)


if __name__ == "__main__":
    main()
