"""Merge nhiều Binance flow cache cùng schema, de-duplicate theo timestamp."""
import argparse
import gzip
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows, sources = {}, []
    for source_path in args.inputs:
        with gzip.open(source_path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        sources.append(data.get("metadata", {}))
        for row in data["rows"]:
            rows[int(row["ts"])] = row
    ordered = [rows[key] for key in sorted(rows)]
    output = {"metadata": {"source": "merged Binance Public Data", "inputs": sources}, "rows": ordered}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"out": str(out), "rows": len(ordered), "first_ts": ordered[0]["ts"], "last_ts": ordered[-1]["ts"]}))


if __name__ == "__main__":
    main()
