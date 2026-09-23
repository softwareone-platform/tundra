"""Resolve every remote plugin source's tip commit and pin it in marketplace.json.

A registry that points at another repository has to name the commit it publishes,
and looking that commit up by hand is the step that makes a release tedious enough to skip.
This does the lookup for all entries at once and rewrites the shas in place.

`--check` is the same walk without the rewrite, so CI can fail when an entry has
drifted behind the branch it tracks.

It also refuses a `version` key on an entry. Claude Code always reads the version from the
plugin's own plugin.json and never warns when the entry disagrees, so a version here is a
number that looks authoritative and decides nothing.
"""

import base64
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARKETPLACE = os.path.join(HERE, "..", ".claude-plugin", "marketplace.json")

EXIT_OK = 0
EXIT_STALE = 1
EXIT_USAGE = 2


def _run(argv):
    """Return stdout, or None when the command fails or is missing entirely."""
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace")


def remote_of(source):
    """The clone URL for a plugin source, or None when the source is a local path."""
    if not isinstance(source, dict):
        return None
    if source.get("source") == "github" and "repo" in source:
        return "https://github.com/%s.git" % source["repo"]
    url = source.get("url")
    if not url:
        return None
    # git-subdir accepts owner/repo shorthand where the other types want a full URL
    if re.match(r"^[\w.-]+/[\w.-]+$", url):
        return "https://github.com/%s.git" % url
    return url


def resolve_tip(url, ref):
    """The commit a ref currently points at, asked of the remote without cloning it."""
    out = _run(["git", "ls-remote", url, ref or "HEAD"])
    if not out:
        return None
    best = None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        sha, name = parts
        # an annotated tag lists the tag object first and the commit as `^{}`,
        # and it is the commit that gets checked out
        if name.endswith("^{}"):
            return sha
        if best is None:
            best = sha
    return best


def version_at(source, sha):
    """The plugin's declared version at a commit, read through gh. Best effort."""
    url = remote_of(source) or ""
    match = re.search(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?$", url)
    if not match:
        return None
    path = source.get("path", "").strip("/")
    manifest = "%s/.claude-plugin/plugin.json" % path if path else ".claude-plugin/plugin.json"
    # the ref goes in the query string: passing it with -f makes gh send a POST, which 404s
    out = _run(["gh", "api",
                "repos/%s/%s/contents/%s?ref=%s" % (match.group(1), match.group(2), manifest, sha),
                "--jq", ".content"])
    if not out:
        return None
    try:
        raw = base64.b64decode("".join(out.split()))
        return json.loads(raw.decode("utf-8")).get("version")
    except (ValueError, TypeError):
        return None


def main(argv):
    if len(argv) != 2 or argv[1] not in ("--check", "--write"):
        sys.stderr.write("usage: sync-plugin-sources.py {--check|--write}\n")
        return EXIT_USAGE

    if not os.path.exists(MARKETPLACE):
        sys.stderr.write("no marketplace.json at %s\n" % os.path.normpath(MARKETPLACE))
        return EXIT_USAGE

    text = open(MARKETPLACE, encoding="utf-8").read()
    data = json.loads(text)
    entries = data.get("plugins", [])

    remote = [e for e in entries if remote_of(e.get("source")) is not None]
    local = len(entries) - len(remote)

    problems = []
    for entry in entries:
        if "version" in entry:
            problems.append("%s: entry declares a version; it is never read when the "
                            "plugin's own plugin.json has one" % entry.get("name"))

    if not remote:
        print("no remote plugin sources; %d local entr%s need%s no pinning"
              % (local, "y" if local == 1 else "ies", "s" if local == 1 else ""))
        for problem in problems:
            sys.stderr.write("%s\n" % problem)
        return EXIT_STALE if problems else EXIT_OK

    rows, stale, unreachable = [], [], []
    for entry in remote:
        source = entry["source"]
        name = entry.get("name", "?")
        url, ref = remote_of(source), source.get("ref")
        tip = resolve_tip(url, ref)
        if tip is None:
            unreachable.append(name)
            rows.append((name, ref or "(default)", source.get("sha"), None, None))
            continue
        pinned = source.get("sha")
        if pinned != tip:
            stale.append((entry, tip))
        rows.append((name, ref or "(default)", pinned, tip, version_at(source, tip)))

    width = max(len(r[0]) for r in rows)
    print("%-*s  %-12s  %-12s  %-12s  %s" % (width, "plugin", "ref", "pinned", "tip", "version at tip"))
    for name, ref, pinned, tip, version in rows:
        print("%-*s  %-12s  %-12s  %-12s  %s"
              % (width, name, ref,
                 (pinned or "(unpinned)")[:12], (tip or "UNREACHABLE")[:12], version or "-"))
    print()

    # stdout is block buffered when redirected, so the table would otherwise land after the warnings
    sys.stdout.flush()
    for problem in problems:
        sys.stderr.write("%s\n" % problem)
    if unreachable:
        sys.stderr.write("could not reach: %s\n" % ", ".join(unreachable))

    if argv[1] == "--check":
        if stale or problems or unreachable:
            if stale:
                sys.stderr.write("%d entr%s behind the ref it tracks\n"
                                 % (len(stale), "y is" if len(stale) == 1 else "ies are"))
            return EXIT_STALE
        print("every remote source is pinned to its ref's tip")
        return EXIT_OK

    if unreachable:
        sys.stderr.write("refusing to write while a source is unreachable\n")
        return EXIT_STALE
    if not stale:
        print("nothing to write; every remote source was already current")
        return EXIT_OK

    # the tip came back in the walk above, so pinning costs no second round trip
    for entry, tip in stale:
        entry["source"]["sha"] = tip
    # the file is plain two-space JSON, so a round trip is lossless here
    with open(MARKETPLACE, "w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print("pinned %d entr%s: %s"
          % (len(stale), "y" if len(stale) == 1 else "ies",
             ", ".join(e.get("name", "?") for e, _ in stale)))
    return EXIT_STALE if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
