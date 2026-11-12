"""Manu (legacy app) and Tanu never touch each other's code (Mandeep, 13 Nov 2026: "manu ka or tanu ka kahi clash ya
overlap na ho"). Checked on every release by reading the import statements of every Python file."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TANU = ROOT / "tanu"
LEGACY_PKGS = {"live", "lab", "webapp", "radar", "tools", "research", "app", "run_all", "predict_today"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.add(n.module.split(".")[0])
    return out


def test_tanu_never_imports_the_legacy_app():
    bad = {}
    for f in TANU.rglob("*.py"):
        hit = _imports(f) & LEGACY_PKGS
        if hit:
            bad[str(f.relative_to(ROOT))] = sorted(hit)
    assert not bad, f"Tanu imports legacy code: {bad}"


def test_legacy_app_never_imports_tanu():
    bad = []
    for f in ROOT.rglob("*.py"):
        rel = f.relative_to(ROOT)
        if rel.parts[0] in ("tanu", ".venv", ".git") or "site-packages" in rel.parts:
            continue
        if "tanu" in _imports(f):
            bad.append(str(rel))
    assert not bad, f"Legacy code imports Tanu: {bad}"


def test_tanu_writes_only_under_its_own_data_folder():
    from tanu.core import config
    s = config.load(env={})
    assert s.data_path == ROOT / "data" / "tanu"
