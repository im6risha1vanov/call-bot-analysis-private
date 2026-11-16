#!/usr/bin/env python3
"""Build a reviewable package from the current checkout; does not deploy."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

from deploy_personal_agent import FILES

root = Path(__file__).resolve().parents[1]
if len(sys.argv) != 2:
    raise SystemExit('Usage: python scripts/package_personal_agent.py OUTPUT_DIR')
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=False)
entries = {}
for name in FILES:
    src = root / name
    if src.is_symlink() or not src.is_file():
        raise SystemExit('Missing or invalid input: ' + name)
    dest = out / 'files' / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    entries[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
manifest = out / 'manifest.json'
manifest.write_text(json.dumps(entries, sort_keys=True, indent=2) + '\n', encoding='utf-8')
print('Manifest SHA-256:', hashlib.sha256(manifest.read_bytes()).hexdigest())
