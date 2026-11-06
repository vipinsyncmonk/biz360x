"""Fail if the screen -> endpoint map in docs/API_GUIDE.md names an operation
that is not in api/openapi.yaml.

Doc entries look like `GET /obligations`, `/copilot/threads`, `POST …/issue`.
- The business prefix /entities/{entityId} is implied, so a doc path matches an
  OpenAPI path by suffix.
- A leading … means "under the previous full path", so only the tail is checked.
- {parameter} names are ignored; only the shape matters.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))
doc = (ROOT / "docs" / "API_GUIDE.md").read_text(encoding="utf-8")

METHODS = {"get", "post", "put", "patch", "delete"}
norm = lambda p: re.sub(r"\{[^}]+\}", "{}", p).rstrip("/")
ops = {}  # normalised path -> set of methods
for path, item in spec["paths"].items():
    ops.setdefault(norm(path), set()).update(m for m in item if m in METHODS)

def seg_match(spec_segs, want, tail_only):
    """doc segments match a suffix of the spec path; a {} in the spec matches any literal."""
    if len(want) > len(spec_segs):
        return False
    tail = spec_segs[len(spec_segs) - len(want):]
    if not all(t == w or t == "{}" or w == "{}" for t, w in zip(tail, want)):
        return False
    head = spec_segs[: len(spec_segs) - len(want)]
    return tail_only or head in ([], ["entities", "{}"])


section = doc.split("## 5. Screen", 1)[1].split("\n## ", 1)[0]
problems = []
checked = 0
for line in section.splitlines():
    if not line.startswith("|") or line.startswith("|---") or line.startswith("| Screen"):
        continue
    for token in re.findall(r"`([^`]+)`", line):
        m = re.match(r"^(?:(GET|POST|PUT|PATCH|DELETE)\s+)?(…)?(/[^\s?(]*)", token)
        if not m:
            continue
        method, ellipsis, path = m.group(1), m.group(2), norm(m.group(3))
        if path in ("", "/"):
            continue
        checked += 1
        want = [seg for seg in path.split("/") if seg]
        hits = [p for p in ops if seg_match([s for s in p.split("/") if s], want, tail_only=bool(ellipsis))]
        if method:
            hits = [p for p in hits if method.lower() in ops[p]]
        if not hits:
            problems.append(f"{token!r}  (row: {line.split('|')[1].strip()})")

if problems:
    print("API_GUIDE.md screen map references operations missing from openapi.yaml:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print(f"docs map OK: {checked} references match the contract")
