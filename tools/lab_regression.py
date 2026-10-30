"""Lab regression check: rescore every saved Strategy Lab rule on the saved bar table with the current code.
Every result must be identical unless a change was meant to alter it. Read-only; prints a report.

    python3 tools/lab_regression.py [data_dir]
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab import factory as F  # noqa: E402


def main(data_dir="data"):
    lab = Path(data_dir) / "lab"
    st = json.loads((lab / "factory.json").read_text())
    book = F.Book(pd.read_parquet(lab / "factory_bars.parquet"))
    changed = []
    for k, r in st.get("rules", {}).items():
        s = F.validate({**r, "source": r.get("source")}) or r
        new = F.score(book, {**s, "id": k})
        for p in ("train", "valid", "hold"):
            if (r[p].get("n"), r[p].get("total")) != (new[p].get("n"), new[p].get("total")):
                changed.append((k, p, r[p].get("n"), new[p].get("n"), r[p].get("total"), new[p].get("total")))
                break
    n = len(st.get("rules", {}))
    print(f"{n - len(changed)} of {n} saved rules give identical results with this code.")
    for c in changed[:20]:
        print("  changed:", c)
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
