"""List the git identities that belong to the person running whoami, or check a confirmed set of them.

Seeds from `git config user.name` and `user.email` in each repository, normalises every author
on each repository's default branch, and links authors transitively to the seeds.

    python identity.py <repository> [<repository> ...]
    python identity.py <repository> [<repository> ...] --check <address> [<address> ...]

Without --check it prints one JSON document: the seeds, and every linked candidate with the
addresses as written in the commits and its commit count in each repository.
With --check it exits 0 only when every confirmed address is a linked candidate
reachable from a seed through confirmed candidates alone.

Exit status 2 means a path is not a git repository.
Exit status 4 means the identities cannot be shown to belong to the person running it.
"""

import argparse
import json
import re
import subprocess
import sys

NOT_A_REPOSITORY = 2
NOT_THE_PERSON = 4

NOREPLY = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$")


class NotARepository(Exception):
    pass


def git(repository, *args):
    result = subprocess.run(["git", "-C", repository] + list(args), capture_output=True,
                            encoding="utf-8", errors="replace")
    return result.returncode, result.stdout


def normalise_name(name):
    name = " ".join(name.split())
    # a web UI can write the name as "Last, First"
    parts = [p.strip() for p in name.split(",")]
    if len(parts) == 2 and all(parts):
        name = parts[1] + " " + parts[0]
    return name.casefold()


def email_key(email):
    email = email.strip().lower()
    noreply = NOREPLY.match(email)
    # both forms of a GitHub noreply address, with and without the numeric id, name the same login
    return "login:" + noreply.group(1) if noreply else "email:" + email


def keys_of(names, email):
    keys = {email_key(email)} if email.strip() else set()
    return keys | {"name:" + normalise_name(n) for n in names if n.strip()}


def default_branch(repository):
    code, _ = git(repository, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/HEAD")
    return "refs/remotes/origin/HEAD" if code == 0 else "HEAD"


def seeds_of(repository):
    code, _ = git(repository, "rev-parse", "--git-dir")
    if code != 0:
        raise NotARepository(repository)
    name = git(repository, "config", "user.name")[1].strip()
    email = git(repository, "config", "user.email")[1].strip()
    return [{"repository": repository, "name": name, "email": email}] if name or email else []


def authors_of(repository):
    """Every non-merge author on the default branch, keyed by lower-cased address."""
    code, out = git(repository, "log", "--no-merges", "--format=%an%x00%ae", default_branch(repository))
    # a repository with no commits yet has no default branch to read
    if code != 0:
        return {}
    authors = {}
    for line in out.splitlines():
        name, _, address = line.partition("\0")
        entry = authors.setdefault(address.lower(), {"addresses": set(), "names": set(), "commits": 0})
        entry["addresses"].add(address)
        entry["names"].add(name)
        entry["commits"] += 1
    return authors


def collect(repositories):
    seeds, candidates = [], {}
    for repository in repositories:
        seeds += seeds_of(repository)
        for email, a in authors_of(repository).items():
            c = candidates.setdefault(email, {"email": email, "addresses": set(), "names": set(), "commits": {}})
            c["addresses"] |= a["addresses"]
            c["names"] |= a["names"]
            c["commits"][repository] = a["commits"]
    return seeds, candidates


def seed_keys(seeds):
    keys = set()
    for s in seeds:
        keys |= keys_of([s["name"]], s["email"])
    return keys


def link(seeds, candidates):
    """The candidates reachable from a seed, each joining when it shares a normalised name or address."""
    keys = seed_keys(seeds)
    linked = set()
    grew = True
    while grew:
        grew = False
        for email, c in candidates.items():
            if email in linked:
                continue
            own = keys_of(c["names"], email)
            if own & keys:
                linked.add(email)
                keys |= own
                grew = True
    return linked


def listing(seeds, candidates):
    linked = link(seeds, candidates)
    direct = seed_keys(seeds)
    rows = []
    for email in linked:
        c = candidates[email]
        rows.append({"email": email, "addresses": sorted(c["addresses"]), "names": sorted(c["names"]),
                     "seed": bool(keys_of(c["names"], email) & direct), "commits": c["commits"]})
    # seeds first, then the most commits, so the likeliest identities head the confirmation
    rows.sort(key=lambda r: (not r["seed"], -sum(r["commits"].values()), r["email"]))
    return {"seeds": seeds, "candidates": rows}


def check(seeds, candidates, confirmed):
    """The problems that keep the confirmed set from being the person's, or an empty list."""
    if not seeds:
        return ["no repository has a git user.name or user.email, so there is nobody to assess"]
    confirmed = {a.strip().lower() for a in confirmed if a.strip()}
    if not confirmed:
        return ["no identity was confirmed"]
    problems = ["'%s' has no commits on the default branch of any repository in scope" % a
                for a in sorted(confirmed) if a not in candidates]
    # linking only through confirmed identities, so an identity the person turned down cannot carry a stranger in
    reached = link(seeds, {a: candidates[a] for a in confirmed if a in candidates})
    problems += ["'%s' is not linked to the git identity of the person running this" % a
                 for a in sorted(confirmed) if a in candidates and a not in reached]
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repositories", nargs="+")
    parser.add_argument("--check", nargs="+", metavar="ADDRESS")
    args = parser.parse_args(argv)

    # a Windows console defaults to cp1252, which cannot encode most names written outside English
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    try:
        seeds, candidates = collect(args.repositories)
    except NotARepository as error:
        sys.stderr.write("not a git repository: %s\n" % error)
        return NOT_A_REPOSITORY

    if args.check is None:
        json.dump(listing(seeds, candidates), sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
        if not seeds:
            sys.stderr.write("no repository has a git user.name or user.email, so there is nobody to assess\n")
            return NOT_THE_PERSON
        return 0

    problems = check(seeds, candidates, args.check)
    if problems:
        sys.stderr.write("these identities cannot be shown to be the person running this, so stop:\n")
        sys.stderr.write("".join("- %s\n" % p for p in problems))
        return NOT_THE_PERSON
    sys.stdout.write("every confirmed identity belongs to the person running this\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
