"""Deterministic self-check for the whoami identity script.

The script decides whose work the report is about, so every case here is a known-answer case,
either over candidate dictionaries shaped as `collect` returns them,
or over real git repositories built in temp dirs with authors chosen per commit.
The machine's own git identity is hidden for the whole module,
so a repository with no local user.name or user.email really has no seed.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/identity_tests.py
"""

import io
import json
import os
import subprocess
import sys
import tempfile

# the global and system git config would otherwise seed every repository with the machine's own identity,
# and the script's git calls inherit os.environ, so this has to happen before any git call
_GLOBAL_CONFIG = os.path.join(tempfile.mkdtemp(), "gitconfig")
open(_GLOBAL_CONFIG, "w").close()
os.environ["GIT_CONFIG_GLOBAL"] = _GLOBAL_CONFIG
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
# any of these would point git elsewhere or put an identity on a commit behind the fixture's back
for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
             "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
    os.environ.pop(_var, None)

# import the module under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _SCRIPTS)
import identity as ident  # noqa: E402

_SCRIPT = os.path.join(_SCRIPTS, "identity.py")

NO_SEED = "no repository has a git user.name or user.email, so there is nobody to assess"
NONE_CONFIRMED = "no identity was confirmed"
HEADER = "these identities cannot be shown to be the person running this, so stop:\n"
SUCCESS = "every confirmed identity belongs to the person running this\n"


def _no_commits(address):
    return "'%s' has no commits on the default branch of any repository in scope" % address


def _not_linked(address):
    return "'%s' is not linked to the git identity of the person running this" % address


# every check records (group, name, ok, detail),
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(name, got, want):
    ok = got == want
    _results.append((_group, name, ok, "" if ok else "got %r, want %r" % (got, want)))


# ----- synthetic repositories ---------------------------------------------------

def _git(repository, *args):
    result = subprocess.run(["git", "-C", repository] + list(args), capture_output=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def _repo(name=None, email=None):
    """A fresh repository on main, with a repo-local identity only where a seed is intended."""
    path = tempfile.mkdtemp()
    _git(path, "init", "-q", "-b", "main")
    if name is not None:
        _git(path, "config", "user.name", name)
    if email is not None:
        _git(path, "config", "user.email", email)
    return path


def _as(name, email):
    """Per-call options that make one commit carry exactly this author and nothing from any config."""
    return ("-c", "user.name=" + name, "-c", "user.email=" + email, "-c", "commit.gpgsign=false")


def _commit(repository, name, email, count=1):
    for _ in range(count):
        _git(repository, *(_as(name, email) + ("commit", "-q", "--allow-empty", "-m", "change")))


def _cand(email, names, commits=None):
    """A candidate shaped as collect returns it."""
    return {"email": email, "addresses": {email}, "names": set(names), "commits": commits or {"r": 1}}


def _seed(name, email, repository="r"):
    return {"repository": repository, "name": name, "email": email}


# ----- fixture: the machine's identity is out of reach ---------------------------

def test_isolation():
    probe = subprocess.run(["git", "-C", _repo(), "config", "user.name"], capture_output=True)
    check("fixture: no global or system user.name reaches a new repository", probe.returncode, 1)
    probe = subprocess.run(["git", "-C", tempfile.mkdtemp(), "rev-parse", "--git-dir"], capture_output=True)
    check("fixture: a temp dir is outside any repository", probe.returncode != 0, True)


# ----- normalise_name, email_key, keys_of ----------------------------------------

def test_normalise_name():
    check("Last, First is turned round", ident.normalise_name("Lee, Ann"), "ann lee")
    check("Last,First with no space is turned round", ident.normalise_name("Lee,Ann"), "ann lee")
    check("space before the comma is dropped", ident.normalise_name("Lee ,  Ann"), "ann lee")
    check("runs of whitespace collapse", ident.normalise_name("  Ann \t  Lee  "), "ann lee")
    check("letter case folded", ident.normalise_name("ANN Lee"), "ann lee")
    # casefold, not lower: lower() leaves the sharp s alone, casefold() turns it into ss
    check("casefold rather than lower", ident.normalise_name("Stra\u00dfe"), "strasse")
    check("two commas left in place", ident.normalise_name("Lee, Ann, Jr"), "lee, ann, jr")
    check("an empty side leaves the comma in place", ident.normalise_name(", Ann"), ", ann")
    check("no comma is only folded", ident.normalise_name("Ann Lee"), "ann lee")


def test_email_key():
    check("noreply with a numeric id names the login",
          ident.email_key("123+login@users.noreply.github.com"), "login:login")
    check("noreply without an id names the same login",
          ident.email_key("login@users.noreply.github.com"), "login:login")
    check("noreply is matched after lower-casing",
          ident.email_key("123+Login@Users.Noreply.GitHub.com"), "login:login")
    check("ordinary address lower-cased and stripped", ident.email_key(" Ann@Example.COM "), "email:ann@example.com")
    check("a plus address elsewhere is an ordinary address",
          ident.email_key("123+ann@example.com"), "email:123+ann@example.com")
    check("a longer domain ending past noreply is an ordinary address",
          ident.email_key("login@users.noreply.github.com.example.org"),
          "email:login@users.noreply.github.com.example.org")


def test_keys_of():
    check("address and normalised names",
          ident.keys_of(["Lee, Ann", "A. Lee"], "Ann@Corp.com"), {"email:ann@corp.com", "name:ann lee", "name:a. lee"})
    check("blank names skipped", ident.keys_of(["", "  "], "ann@corp.com"), {"email:ann@corp.com"})
    check("blank address skipped", ident.keys_of(["Ann"], "  "), {"name:ann"})
    check("nothing gives no keys", ident.keys_of([], ""), set())


# ----- default_branch and authors_of over real repositories -----------------------

def test_default_branch():
    plain = _repo()
    _commit(plain, "Ann", "ann@corp.com")
    check("no origin/HEAD reads HEAD", ident.default_branch(plain), "HEAD")

    tracked = _repo()
    _commit(tracked, "Shipped Author", "shipped@corp.com")
    _git(tracked, "update-ref", "refs/remotes/origin/main", _git(tracked, "rev-parse", "HEAD"))
    _git(tracked, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    # a local commit not yet on origin's default branch, so reading HEAD would count it
    _commit(tracked, "Local Author", "local@corp.com")
    check("origin/HEAD present is read", ident.default_branch(tracked), "refs/remotes/origin/HEAD")
    check("only origin's default branch is counted", sorted(ident.authors_of(tracked)), ["shipped@corp.com"])

    dangling = _repo()
    _commit(dangling, "Ann", "ann@corp.com")
    _git(dangling, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    check("origin/HEAD naming a missing branch falls back to HEAD", ident.default_branch(dangling), "HEAD")


def test_authors_of():
    repo = _repo()
    _commit(repo, "Ann Lee", "Ann.Lee@Example.com")
    _commit(repo, "Lee, Ann", "ann.lee@example.com", count=2)
    _commit(repo, "Bob", "bob@example.com")
    check("keyed by lower-cased address, addresses and names as written, commits counted",
          ident.authors_of(repo),
          {"ann.lee@example.com": {"addresses": {"Ann.Lee@Example.com", "ann.lee@example.com"},
                                   "names": {"Ann Lee", "Lee, Ann"}, "commits": 3},
           "bob@example.com": {"addresses": {"bob@example.com"}, "names": {"Bob"}, "commits": 1}})


def test_authors_of_skips_merges():
    repo = _repo()
    _commit(repo, "Base", "base@example.com")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "Side", "side@example.com")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "Main", "main@example.com")
    _git(repo, *(_as("Merger", "merger@example.com") + ("merge", "-q", "--no-ff", "--no-edit", "-m", "merge side", "side")))
    check("fixture: HEAD is a merge commit", len(_git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()), 3)
    check("merge author left out, merged commits kept", sorted(ident.authors_of(repo)),
          ["base@example.com", "main@example.com", "side@example.com"])


def test_authors_of_default_branch_only():
    repo = _repo()
    _commit(repo, "Main", "main@example.com")
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "Feature Only", "feature@example.com")
    _git(repo, "checkout", "-q", "main")
    check("a commit only on another branch not counted", sorted(ident.authors_of(repo)), ["main@example.com"])


def test_empty_repository():
    repo = _repo("Ann", "ann@corp.com")
    check("no commits gives no authors", ident.authors_of(repo), {})
    check("no commits still gives the seed", ident.collect([repo]), ([_seed("Ann", "ann@corp.com", repo)], {}))


# ----- seeds_of and collect -----------------------------------------------------

def test_seeds_of():
    outside = tempfile.mkdtemp()
    try:
        ident.seeds_of(outside)
        raised = None
    except ident.NotARepository as error:
        raised = error.args
    check("a directory outside any repository raises NotARepository naming it", raised, (outside,))

    missing = os.path.join(tempfile.mkdtemp(), "missing")
    try:
        ident.seeds_of(missing)
        raised = None
    except ident.NotARepository as error:
        raised = error.args
    check("a path that does not exist raises NotARepository", raised, (missing,))

    repo = _repo()
    check("no user.name or user.email gives no seed", ident.seeds_of(repo), [])
    repo = _repo(name="Ann Lee")
    check("user.name alone is a seed", ident.seeds_of(repo), [_seed("Ann Lee", "", repo)])
    repo = _repo(email="ann@corp.com")
    check("user.email alone is a seed", ident.seeds_of(repo), [_seed("", "ann@corp.com", repo)])
    repo = _repo("Ann Lee", "ann@corp.com")
    check("both make one seed", ident.seeds_of(repo), [_seed("Ann Lee", "ann@corp.com", repo)])


def test_collect_counts_per_repository():
    first = _repo("Ann", "ann@corp.com")
    _commit(first, "Ann", "Ann@Corp.com", count=2)
    second = _repo()
    _commit(second, "Ann", "ann@corp.com")
    _commit(second, "Bob", "bob@corp.com")
    seeds, candidates = ident.collect([first, second])
    check("only the repository with an identity seeds", seeds, [_seed("Ann", "ann@corp.com", first)])
    check("one address across repositories is one candidate", sorted(candidates), ["ann@corp.com", "bob@corp.com"])
    check("commits counted in each repository", candidates["ann@corp.com"]["commits"], {first: 2, second: 1})
    check("addresses merged across repositories", candidates["ann@corp.com"]["addresses"],
          {"Ann@Corp.com", "ann@corp.com"})
    check("candidate carries its lower-cased address", candidates["bob@corp.com"]["email"], "bob@corp.com")


# ----- link: transitive through shared keys -------------------------------------

def test_link():
    seeds = [_seed("Seed Name", "a@corp.com")]
    # c comes before b, so a single pass over the candidates would miss c
    candidates = {"c@corp.com": _cand("c@corp.com", ["Other"]),
                  "b@corp.com": _cand("b@corp.com", ["N", "Other"]),
                  "a@corp.com": _cand("a@corp.com", ["N"]),
                  "d@corp.com": _cand("d@corp.com", ["Stranger"])}
    check("linked transitively through shared names, stranger left out", ident.link(seeds, candidates),
          {"a@corp.com", "b@corp.com", "c@corp.com"})

    check("normalised seed name links another address",
          ident.link([_seed("Ann Lee", "ann@corp.com")], {"x@home.net": _cand("x@home.net", ["lee, ANN"])}),
          {"x@home.net"})
    check("noreply forms of one login link",
          ident.link([_seed("", "123+ann@users.noreply.github.com")],
                     {"ann@users.noreply.github.com": _cand("ann@users.noreply.github.com", ["Someone"])}),
          {"ann@users.noreply.github.com"})
    check("no seeds link nothing", ident.link([], candidates), set())


# ----- listing ------------------------------------------------------------------

def test_listing_from_repositories():
    # the seed's name is not on any commit, so everything past its own address links through commits alone
    first = _repo("ann", "ann@corp.com")
    _commit(first, "Ann Lee", "Ann@Corp.com")
    _commit(first, "Ann Lee", "ann@corp.com")
    _commit(first, "Lee, Ann", "ann@home.net")
    _commit(first, "A. Lee", "ann@home.net")
    _commit(first, "A. Lee", "al@old.org")
    _commit(first, "Bob Stone", "bob@corp.com")
    second = _repo()
    _commit(second, "A. Lee", "ann@home.net")
    _commit(second, "Bob Stone", "bob@corp.com")
    check("only linked candidates, seeds first, then most commits", ident.listing(*ident.collect([first, second])),
          {"seeds": [_seed("ann", "ann@corp.com", first)],
           "candidates": [
               {"email": "ann@corp.com", "addresses": ["Ann@Corp.com", "ann@corp.com"], "names": ["Ann Lee"],
                "seed": True, "commits": {first: 2}},
               {"email": "ann@home.net", "addresses": ["ann@home.net"], "names": ["A. Lee", "Lee, Ann"],
                "seed": False, "commits": {first: 2, second: 1}},
               {"email": "al@old.org", "addresses": ["al@old.org"], "names": ["A. Lee"],
                "seed": False, "commits": {first: 1}}]})


def test_listing_seed_flag():
    seeds = [_seed("Ann Lee", "ann@corp.com")]
    candidates = {"ann@corp.com": _cand("ann@corp.com", ["Other"]),
                  "x@home.net": _cand("x@home.net", ["Lee, Ann"]),
                  "z@old.org": _cand("z@old.org", ["Other"])}
    flags = {r["email"]: r["seed"] for r in ident.listing(seeds, candidates)["candidates"]}
    check("seed flag only for a key shared directly with a seed",
          flags, {"ann@corp.com": True, "x@home.net": True, "z@old.org": False})


def test_listing_order():
    seeds = [_seed("Ann", "ann@corp.com")]
    # the non-seeds have more commits than the seeds, and bea's total beats five only when summed across repositories
    # four tie at five, because rows come out of a set in hash order and a two-way tie would pass half the time unsorted
    candidates = {"ann@corp.com": _cand("ann@corp.com", ["Ann"], {"r": 1}),
                  "ann@home.net": _cand("ann@home.net", ["Ann", "Nan"], {"r": 2}),
                  "zed@old.org": _cand("zed@old.org", ["Nan"], {"r": 5}),
                  "kim@old.org": _cand("kim@old.org", ["Nan"], {"r": 5}),
                  "amy@old.org": _cand("amy@old.org", ["Nan"], {"r": 5}),
                  "dan@old.org": _cand("dan@old.org", ["Nan"], {"r": 5}),
                  "bea@old.org": _cand("bea@old.org", ["Nan"], {"r": 2, "s": 4})}
    check("seeds first, then commits summed across repositories, then address",
          [r["email"] for r in ident.listing(seeds, candidates)["candidates"]],
          ["ann@home.net", "ann@corp.com", "bea@old.org", "amy@old.org", "dan@old.org", "kim@old.org", "zed@old.org"])
    check("no seeds lists nobody", ident.listing([], candidates), {"seeds": [], "candidates": []})


# ----- check: the confirmed set must be the person's -----------------------------

def _person():
    """A seed, two of the person's addresses, a stranger who shares a name with the second, and an unrelated author."""
    seeds = [_seed("Ann Lee", "ann@corp.com")]
    candidates = {"ann@corp.com": _cand("ann@corp.com", ["Ann Lee"]),
                  "ann@home.net": _cand("ann@home.net", ["Ann Lee", "Shared"]),
                  "eve@corp.com": _cand("eve@corp.com", ["Shared"]),
                  "bob@corp.com": _cand("bob@corp.com", ["Bob"])}
    return seeds, candidates


def test_check():
    seeds, candidates = _person()
    check("seed address alone passes", ident.check(seeds, candidates, ["ann@corp.com"]), [])
    check("seed and linked address pass", ident.check(seeds, candidates, ["ann@corp.com", "ann@home.net"]), [])
    check("confirmed addresses compared case-insensitively",
          ident.check(seeds, candidates, ["  ANN@Corp.COM ", "Ann@Home.Net"]), [])
    check("typed-in stranger rejected", ident.check(seeds, candidates, ["ann@corp.com", "stranger@example.com"]),
          [_no_commits("stranger@example.com")])
    check("unlinked author rejected", ident.check(seeds, candidates, ["ann@corp.com", "bob@corp.com"]),
          [_not_linked("bob@corp.com")])
    # eve links only through ann@home.net, which the person did not confirm
    check("candidate linked only through an unconfirmed identity rejected",
          ident.check(seeds, candidates, ["ann@corp.com", "eve@corp.com"]), [_not_linked("eve@corp.com")])
    check("the same candidate passes once its link is confirmed",
          ident.check(seeds, candidates, ["ann@corp.com", "ann@home.net", "eve@corp.com"]), [])
    check("problems listed by kind, each sorted by address",
          ident.check(seeds, candidates, ["zed@example.com", "bob@corp.com", "amy@example.com", "eve@corp.com"]),
          [_no_commits("amy@example.com"), _no_commits("zed@example.com"),
           _not_linked("bob@corp.com"), _not_linked("eve@corp.com")])


def test_check_nothing_to_go_on():
    seeds, candidates = _person()
    check("no seeds is the no-identity problem", ident.check([], candidates, ["ann@corp.com"]), [NO_SEED])
    check("nothing confirmed", ident.check(seeds, candidates, []), [NONE_CONFIRMED])
    check("only blanks confirmed", ident.check(seeds, candidates, ["", "   "]), [NONE_CONFIRMED])


# ----- main: exit status and output ---------------------------------------------

def _main(argv):
    """Run main in-process with stdout and stderr captured.
    Both must be real text wrappers, because main reconfigures both to UTF-8,
    and newline="\\n" stops them writing \\r\\n on Windows, so the text compares exactly."""
    raw_out, raw_err = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(raw_out, encoding="cp1252", newline="\n")
    err = io.TextIOWrapper(raw_err, encoding="cp1252", newline="\n")
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = ident.main(argv)
        out.flush()
        err.flush()
    finally:
        sys.stdout, sys.stderr = saved
    return code, raw_out.getvalue().decode("utf-8"), raw_err.getvalue().decode("utf-8")


def _person_repository():
    repo = _repo("Ann Lee", "ann@corp.com")
    _commit(repo, "Ann Lee", "ann@corp.com")
    _commit(repo, "Bob", "bob@corp.com")
    return repo


def test_main_listing():
    check("NOT_A_REPOSITORY constant", ident.NOT_A_REPOSITORY, 2)
    check("NOT_THE_PERSON constant", ident.NOT_THE_PERSON, 4)

    repo = _person_repository()
    code, out, err = _main([repo])
    check("listing exits 0", code, 0)
    check("listing prints the document", json.loads(out),
          {"seeds": [_seed("Ann Lee", "ann@corp.com", repo)],
           "candidates": [{"email": "ann@corp.com", "addresses": ["ann@corp.com"], "names": ["Ann Lee"],
                           "seed": True, "commits": {repo: 1}}]})
    check("listing stderr silent", err, "")

    unseeded = _repo()
    _commit(unseeded, "Ann Lee", "ann@corp.com")
    code, out, err = _main([unseeded])
    check("listing with no seed exits 4", code, 4)
    check("listing with no seed still prints the document", json.loads(out), {"seeds": [], "candidates": []})
    check("listing with no seed explains on stderr", err, NO_SEED + "\n")


def test_main_check():
    repo = _person_repository()
    code, out, err = _main([repo, "--check", "Ann@Corp.com"])
    check("check passing exits 0", code, 0)
    check("check passing prints the success line", out, SUCCESS)
    check("check passing stderr silent", err, "")

    code, out, err = _main([repo, "--check", "ann@corp.com", "bob@corp.com", "eve@example.com"])
    check("check failing exits 4", code, 4)
    check("check failing prints nothing on stdout", out, "")
    check("check failing lists each problem under the header", err,
          HEADER + "- " + _no_commits("eve@example.com") + "\n" + "- " + _not_linked("bob@corp.com") + "\n")

    unseeded = _repo()
    _commit(unseeded, "Ann Lee", "ann@corp.com")
    code, out, err = _main([unseeded, "--check", "ann@corp.com"])
    check("check with no seed exits 4", code, 4)
    check("check with no seed gives the no-identity problem", err, HEADER + "- " + NO_SEED + "\n")


def test_main_not_a_repository():
    repo = _person_repository()
    outside = tempfile.mkdtemp()
    code, out, err = _main([repo, outside])
    check("a path outside any repository exits 2", code, 2)
    check("not a repository prints nothing on stdout", out, "")
    check("not a repository names the path", err, "not a git repository: %s\n" % outside)

    code, _, _ = _main([outside, "--check", "ann@corp.com"])
    check("not a repository exits 2 in check mode too", code, 2)


def test_non_ascii_on_cp1252_console():
    name = "\u7e41\u9ad4 Lee"
    repo = _repo(name, "ann@corp.com")
    _commit(repo, name, "ann@corp.com")
    # os.environ already hides the machine's git identity, and the child inherits it
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run([sys.executable, _SCRIPT, repo], capture_output=True, env=env, timeout=60)
    check("subprocess exits 0", proc.returncode, 0)
    check("subprocess stderr empty", proc.stderr, b"")
    doc = json.loads(proc.stdout.decode("utf-8"))
    check("non-ASCII seed name survives as UTF-8", [s["name"] for s in doc["seeds"]], [name])
    check("non-ASCII author name survives as UTF-8", [c["names"] for c in doc["candidates"]], [[name]])

    proc = subprocess.run([sys.executable, _SCRIPT, repo, "--check", "stranger@example.com"],
                          capture_output=True, env=env, timeout=60)
    check("subprocess check of a stranger exits 4", proc.returncode, 4)


_TESTS = (test_isolation, test_normalise_name, test_email_key, test_keys_of,
          test_default_branch, test_authors_of, test_authors_of_skips_merges, test_authors_of_default_branch_only,
          test_empty_repository, test_seeds_of, test_collect_counts_per_repository,
          test_link, test_listing_from_repositories, test_listing_seed_flag, test_listing_order,
          test_check, test_check_nothing_to_go_on,
          test_main_listing, test_main_check, test_main_not_a_repository, test_non_ascii_on_cp1252_console)


def _run_all():
    global _group
    for t in _TESTS:
        _group = t.__name__
        try:
            t()
        except Exception as exc:
            # a check that raises is itself a failure
            _results.append((_group, "(crashed)", False,
                             "raised %s: %s" % (type(exc).__name__, exc)))
    return _results


def main():
    """Print a per-check PASS/FAIL breakdown grouped by test, then a summary.
    Exits non-zero if any check fails."""
    _run_all()
    order, groups = [], {}
    for g, name, ok, detail in _results:
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append((name, ok, detail))
    for g in order:
        print(g)
        for name, ok, detail in groups[g]:
            line = "  [%s] %s" % ("PASS" if ok else "FAIL", name)
            if not ok:
                line += " -- " + detail
            # the detail can quote a non-ASCII value, and the console may be cp1252
            print(line.encode("ascii", "backslashreplace").decode("ascii"))
    npass = sum(1 for r in _results if r[2])
    nfail = len(_results) - npass
    print("")
    print("%d passed, %d failed" % (npass, nfail))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
