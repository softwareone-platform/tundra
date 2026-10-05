"""Fail when a translation of the root README was made from a different README than the one in the tree.

Each translation's first line records the sha256 of the README.md it was translated from,
so an edit to README.md that leaves a translation behind shows up as a digest that no longer matches.
The check says nothing about whether the translation means what the English means,
which only a reader can judge.

With `--rev`, the files are read from that commit instead of the working tree,
which is what the pre-push hook needs, because an uncommitted edit is not what gets pushed.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SOURCE = "README.md"
TRANSLATIONS = ("README.zh-TW.md", "README.zh-CN.md")
DIGEST = re.compile(r"source sha256 ([0-9a-f]{64})")

EXIT_OK = 0
EXIT_STALE = 1


def read(path, rev):
    """Return the file's bytes, or None when it does not exist there."""
    if rev is None:
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            return None
        with open(full, "rb") as handle:
            return handle.read()
    result = subprocess.run(["git", "-C", ROOT, "show", "%s:%s" % (rev, path)], capture_output=True)
    return result.stdout if result.returncode == 0 else None


def check(rev):
    source = read(SOURCE, rev)
    if source is None:
        return []
    current = hashlib.sha256(source).hexdigest()
    problems = []
    for name in TRANSLATIONS:
        text = read(name, rev)
        if text is None:
            problems.append("%s is missing" % name)
            continue
        first = text.decode("utf-8", errors="replace").splitlines()[:1]
        match = DIGEST.search(first[0]) if first else None
        if not match:
            problems.append("%s records no source digest on its first line" % name)
        elif match.group(1) != current:
            problems.append("%s was translated from another README.md (records %s)" % (name, match.group(1)[:12]))
    if problems:
        problems.append("re-translate the paragraphs that changed, then record the current digest %s" % current)
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rev", help="check this commit rather than the working tree")
    args = parser.parse_args()
    problems = check(args.rev)
    if problems:
        where = args.rev[:12] if args.rev else "the working tree"
        sys.stderr.write("README translations are stale in %s:\n" % where)
        sys.stderr.write("".join("- %s\n" % p for p in problems))
        return EXIT_STALE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
