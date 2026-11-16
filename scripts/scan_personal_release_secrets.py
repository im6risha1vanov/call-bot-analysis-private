#!/usr/bin/env python3
"""Read-only, redacted high-confidence scan of working tree and Git patch history.

Reports only source, rule and path. This is not a complete secret audit.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

RULES = {
    'telegram_token': re.compile(rb'\b\d{7,12}:[A-Za-z0-9_-]{30,}\b'),
    'openai_key': re.compile(rb'\bsk-[A-Za-z0-9_-]{20,}\b'),
    'groq_key': re.compile(rb'\bgsk_[A-Za-z0-9]{20,}\b'),
    'github_token': re.compile(rb'\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'aws_access_key': re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'credential_dsn': re.compile(rb'\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://[^\s/:@]{2,}:[^\s/@]{4,}@[^\s/]+', re.I),
}
SYNTHETIC = re.compile(rb'(?:example|synthetic|dummy|fake|placeholder|your[_ -]?|<[^>]+>|\*{3,}|xxx+|test[_ -]?(?:key|token|password)|localhost|127\.0\.0\.1)', re.I)


def rules(data):
    found = set()
    for name, pattern in RULES.items():
        for match in pattern.finditer(data):
            if not SYNTHETIC.search(match.group(0)):
                found.add(name)
    return found


def safe(value):
    return value.decode('utf-8', errors='backslashreplace').replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t')


def scan(root):
    results = set()
    listing = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root)
    for name in listing.split(b'\0'):
        if not name:
            continue
        path = root / name.decode('utf-8', 'surrogateescape')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b'\0' in data:
            continue
        for rule in rules(data):
            results.add(('working-tree', safe(name), rule))
    # Added lines cover content introduced at any point; never print matching text.
    proc = subprocess.Popen(['git', 'log', '--all', '--format=COMMIT:%H', '-m', '-p', '--no-ext-diff', '--no-color', '--no-renames', '--'], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    commit = None; name = None
    for line in proc.stdout:
        if re.fullmatch(rb'COMMIT:[0-9a-f]{40}\r?\n', line):
            commit = line[7:47].decode('ascii'); name = None
        elif line.startswith(b'+++ b/'):
            name = line[6:].rstrip(b'\r\n')
        elif line.startswith(b'+++ /dev/null'):
            name = None
        elif line.startswith(b'+') and name is not None and commit:
            for rule in rules(line[1:]):
                results.add((commit, safe(name), rule))
    if proc.wait() != 0:
        raise RuntimeError('Git history scan failed')
    return sorted(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    args = parser.parse_args()
    findings = scan(args.repository.resolve())
    for source, path, rule in findings:
        print(f'{source}\t{path}\t{rule}')
    print(f'Findings: {len(findings)}; high-confidence patterns only, not exhaustive.', file=sys.stderr)
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
