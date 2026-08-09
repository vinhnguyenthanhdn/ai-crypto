"""Chuyển Binance flow cache thành primary-candle cache cho offline indicators."""
import argparse
import gzip
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with gzip.open(args.flow_cache, "rt", encoding="utf-8") as fh:
        source = json.load(fh)
    required = ("open", "high", "low", "close", "volume")
    primary = [{"ts": row["ts"], **{key: row[key] for key in required}} for row in source["rows"]]
    output = {"metadata": {"source": source["metadata"], "purpose": "primary-only offline research"}, "primary": primary, "tick": []}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"out": str(out), "rows": len(primary)}))


if __name__ == "__main__":
    main()
