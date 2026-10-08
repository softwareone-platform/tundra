"""Fail when a README translation was made from a different README than the one in the tree.

A README.md is translated when a README.zh-TW.md or README.zh-CN.md sits beside it, the root one and any plugin's alike.
Once one language is there both must be, and each translation's first line records the sha256 of the README.md it was translated from,
so an edit to README.md that leaves a translation behind shows up as a digest that no longer matches.
The check says nothing about whether the translation means what the English means,
which only a reader can judge.

With `--rev`, the files are read from that commit instead of the working tree,
which is what the pre-push hook needs, because an uncommitted edit is not what gets pushed.
"""

import argparse
import hashlib
import os
import posixpath
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SOURCE = "README.md"
LANGUAGES = ("zh-TW", "zh-CN")
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


def files(rev):
    """Every path git knows at that commit, or in the working tree including files not yet added."""
    if rev is None:
        command = ["git", "-C", ROOT, "ls-files", "--cached", "--others", "--exclude-standard"]
    else:
        command = ["git", "-C", ROOT, "ls-tree", "-r", "--name-only", rev]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.splitlines()


def translated(rev):
    """The folders whose README.md has at least one translation beside it, so a plugin that has none is left alone."""
    names = set(files(rev))
    folders = {posixpath.dirname(name) for name in names
               if posixpath.basename(name) in ["README.%s.md" % language for language in LANGUAGES]}
    return sorted(folders)


def check(rev):
    problems = []
    for folder in translated(rev):
        source_path = posixpath.join(folder, SOURCE)
        source = read(source_path, rev)
        if source is None:
            problems.append("%s has translations but no README.md" % (folder or "the root"))
            continue
        current = hashlib.sha256(source).hexdigest()
        stale = []
        for language in LANGUAGES:
            name = posixpath.join(folder, "README.%s.md" % language)
            text = read(name, rev)
            if text is None:
                stale.append("%s is missing" % name)
                continue
            first = text.decode("utf-8", errors="replace").splitlines()[:1]
            match = DIGEST.search(first[0]) if first else None
            if not match:
                stale.append("%s records no source digest on its first line" % name)
            elif match.group(1) != current:
                stale.append("%s was translated from another %s (records %s)" % (name, source_path, match.group(1)[:12]))
        if stale:
            problems += stale + ["re-translate the paragraphs of %s that changed, then record its current digest %s" % (source_path, current)]
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
