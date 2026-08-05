"""CLI quản lý Champion–Challenger.

Usage:
    python scripts/champion_challenger.py status
    python scripts/champion_challenger.py set-challenger <version>
    python scripts/champion_challenger.py promote
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import champion_challenger as cc  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        champion = cc.get_champion_version()
        challenger = cc.get_challenger_version()
        print(f"champion   = {champion.version if champion else '(chưa có)'}")
        print(f"challenger = {challenger.version if challenger else '(chưa có)'}")
    elif cmd == "set-challenger":
        if len(sys.argv) < 3:
            print("Cần truyền version, vd: set-challenger 2")
            sys.exit(1)
        cc.set_challenger(sys.argv[2])
        print(f"Đã set challenger = version {sys.argv[2]}")
    elif cmd == "promote":
        version = cc.promote_challenger_to_champion()
        print(f"Đã promote challenger -> champion (version {version})")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
