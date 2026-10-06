"""Deterministic self-check for the remind-me renderer.

The renderer turns the model's judgement and the collector's digest into the page and the session summary,
so every case here is a known-answer case over a synthetic digest and report,
built from one minimal valid pair and varied one field at a time.
Nothing reads the real ~/.claude, nothing opens a browser, and nothing touches the network.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/render_tests.py
"""

import copy
import html
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

# import the module under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _SCRIPTS)
import render  # noqa: E402

_SCRIPT = os.path.join(_SCRIPTS, "render.py")

SCHEMA_HEADER = "the report does not match the schema, so nothing was written:"

# every check records (group, name, ok, detail),
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(name, got, want):
    ok = got == want
    _results.append((_group, name, ok, "" if ok else "got %r, want %r" % (got, want)))


# ----- synthetic documents -----------------------------------------------------

# an absolute folder on this platform, since the repository panel offers its buttons only for an absolute path
_ROOT = os.path.abspath(os.path.join(os.sep, "work"))


def _folder(name):
    return os.path.join(_ROOT, name)


def _labels():
    """Every label, each naming its own key, so a check can tell which label landed where."""
    return {key: "[%s]" % key for key in render.LABELS}


def _session(sid, first="09:00", last="10:00", cwd=None, **extra):
    session = {"id": sid, "first": first, "last": last, "cwd": cwd or _folder("repo-a"),
               "usage": {"model": {"input_tokens": 10, "cache_creation_input_tokens": 5,
                                   "cache_read_input_tokens": 100, "output_tokens": 7}},
               "prompts": [{"kind": "prompt"}], "pull_requests_new": []}
    session.update(extra)
    return session


def _group_of(name, sessions, **extra):
    group = {"path": _folder(name), "current": False, "sessions": sessions}
    group.update(extra)
    return group


def _digest(groups=None, **extra):
    digest = {"day": "2026-10-05", "generated": "2026-10-06T08:00:00+02:00",
              "repositories": groups if groups is not None else [_group_of("repo-a", [_session("s1")])],
              "pull_requests": {}}
    digest.update(extra)
    return digest


def _body(topics=("topic of the session",), open_items=(), done=()):
    return {"topics": list(topics), "open": list(open_items), "done": list(done)}


def _report(sessions=None, **extra):
    """The smallest report the schema accepts for the default digest: every label and one session entry."""
    report = {"day": "2026-10-05", "language": "en", "headline": "the headline", "labels": _labels(),
              "sessions": sessions if sessions is not None else {"s1": _body()}}
    report.update(extra)
    return report


def _item(kind, text, **extra):
    item = {"kind": kind, "text": text}
    item.update(extra)
    return item


def _panel(page, panel_id):
    """One panel of the page, from its opening tag to the next panel or the end of the main element."""
    start = page.index('<section class="panel" id="%s"' % panel_id)
    ends = [i for i in (page.find('<section class="panel"', start + 1), page.find("</main>", start)) if i >= 0]
    return page[start:min(ends)]


def _kind_section(page, kind):
    overview = _panel(page, "overview")
    start = overview.find('<section class="part kind-%s">' % kind)
    if start < 0:
        return None
    return overview[start:overview.index("</section>", start)]


def _main(report, digest, extra_args=()):
    """Run main in-process on a report and digest written to a temp dir, with stdout captured.
    stdout must be a real text wrapper, because main reconfigures it to UTF-8."""
    d = tempfile.mkdtemp()
    report_path = os.path.join(d, "report.json")
    digest_path = os.path.join(d, "digest.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f)
    with open(digest_path, "w", encoding="utf-8") as f:
        json.dump(digest, f)
    out_path = os.path.join(d, "report.html")
    out_raw = io.BytesIO()
    # a wrapper translates each newline to os.linesep by default, which would make every exact check platform-bound
    out = io.TextIOWrapper(out_raw, encoding="cp1252", newline="\n")
    saved = sys.stdout
    sys.stdout = out
    try:
        code = render.main([report_path, "--digest", digest_path, "--out", out_path] + list(extra_args))
        out.flush()
    finally:
        sys.stdout = saved
    return code, out_raw.getvalue().decode("utf-8"), out_path


# ----- problems_in: each problem on its own --------------------------------------

def test_valid_report_has_no_problems():
    check("minimal report is valid", render.problems_in(_report(), _digest()), [])
    full = _report({"s1": _body(topics=["a", "b"], done=["finished x"],
                                open_items=[_item(kind, "text " + kind, since="16:36") for kind in render.KINDS])})
    check("report using every kind is valid", render.problems_in(full, _digest()), [])


def test_missing_top_level_fields():
    for key in ("day", "language", "headline", "labels", "sessions"):
        report = _report()
        del report[key]
        check("missing %s is named" % key, render.problems_in(report, _digest()), ["missing top-level field: %s" % key])
    # with a top-level field missing the rest cannot be read, so nothing past them is reported
    check("only top-level problems when one is missing",
          render.problems_in({"day": "2000-01-01"}, _digest()),
          ["missing top-level field: language", "missing top-level field: headline",
           "missing top-level field: labels", "missing top-level field: sessions"])


def test_day_not_the_digests():
    check("a different day is named", render.problems_in(_report(day="2026-10-04"), _digest()),
          ["report day 2026-10-04 is not the digest's day 2026-10-05"])


def test_missing_labels():
    report = _report()
    del report["labels"]["here"]
    report["labels"]["copied"] = "   "
    check("absent and blank labels are named in LABELS order", render.problems_in(report, _digest()),
          ["labels missing: copied, here"])
    check("no labels names every label", render.problems_in(_report(labels={}), _digest()),
          ["labels missing: " + ", ".join(render.LABELS)])


def test_session_not_in_digest():
    # the unknown session also has no topics, which must not be reported on top of it being unknown
    report = _report({"s1": _body(), "zz": {"topics": []}})
    check("an unknown session is named once", render.problems_in(report, _digest()), ["session zz is not in the digest"])


def test_digest_session_without_entry():
    digest = _digest([_group_of("repo-a", [_session("s1"), _session("s2")])])
    check("a digest session missing from the report is named", render.problems_in(_report(), digest),
          ["session s2 from the digest has no entry in the report"])


def test_session_without_topics():
    check("empty topics", render.problems_in(_report({"s1": _body(topics=[])}), _digest()), ["session s1 has no topics"])
    check("absent topics", render.problems_in(_report({"s1": {"open": []}}), _digest()), ["session s1 has no topics"])


def test_open_item_kind_and_text():
    kind_problem = "session s1: open item kind must be one of decision, action, question"
    check("unknown kind", render.problems_in(_report({"s1": _body(open_items=[_item("todo", "x")])}), _digest()),
          [kind_problem])
    check("absent kind", render.problems_in(_report({"s1": _body(open_items=[{"text": "x"}])}), _digest()),
          [kind_problem])
    check("blank text", render.problems_in(_report({"s1": _body(open_items=[_item("action", "  ")])}), _digest()),
          ["session s1: an open item has no text"])
    check("absent text", render.problems_in(_report({"s1": _body(open_items=[{"kind": "question"}])}), _digest()),
          ["session s1: an open item has no text"])


# ----- tokens, compact, figures --------------------------------------------------

def test_tokens():
    buckets = [{"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100, "output_tokens": 7},
               {"input_tokens": 1}, {"cache_read_input_tokens": 3, "output_tokens": 2}]
    # by hand: charged = (10 + 5) + 1, cached = 100 + 3, output = 7 + 2
    check("charged is input plus cache writes, cached is cache reads", render.tokens(buckets),
          {"charged": 16, "cached": 103, "output": 9})
    check("no buckets is all zero", render.tokens([]), {"charged": 0, "cached": 0, "output": 0})


def test_compact():
    cases = [(0, "0"), (999, "999"), (1000, "1k"), (1500, "1.5k"), (12340, "12.3k"),
             (2000000, "2M"), (2340000, "2.3M"), (4300000, "4.3M"),
             (1000000000, "1B"), (2500000000, "2.5B"), (4560000000, "4.6B")]
    for number, want in cases:
        check("compact(%d)" % number, render.compact(number), want)
    # the unit is chosen after rounding to one decimal, so a number that rounds to 1000 of one unit reads as 1 of the next
    boundaries = [(999949, "999.9k"), (999950, "1M"), (999999, "1M"), (999999999, "1B")]
    for number, want in boundaries:
        check("compact(%d) at a unit boundary" % number, render.compact(number), want)


def test_compact_top_unit():
    # by hand: 1234567890000 / 1000 three times is 1234.56789, and B is the last unit, so the loop stops there
    check("past B stays in B", render.compact(1234567890000), "1234.6B")
    # by hand: 999999999999 is 999.999999999B, which rounds to 1000.0 but has no unit above B to move to
    check("rounding to 1000B does not move past B", render.compact(999999999999), "1000B")
    check("exactly a thousand B", render.compact(1000000000000), "1000B")


def test_figures():
    s1 = _session("s1", prompts=[{"kind": "prompt"}, {"kind": "command"}, {"kind": "interrupt"}],
                  pull_requests_new=["pr!1", "pr!2"])
    s2 = _session("s2", first="11:00", last="12:00", prompts=[{"kind": "prompt"}, {"kind": "tool"}],
                  pull_requests_new=["pr!2", "pr!4", "pr!5"])
    s3 = _session("s3", cwd=_folder("repo-b"), prompts=[])
    del s3["pull_requests_new"]
    digest = _digest([_group_of("repo-a", [s1, s2]), _group_of("repo-b", [s3])],
                     pull_requests={"pr!1": {"status": "completed"}, "pr!2": {"status": "active"},
                                    "pr!3": {"status": "merged"}, "pr!4": {"status": "merged"}})
    got = render.figures(digest)
    check("sessions", got["sessions"], 3)
    check("repositories", got["repositories"], 2)
    check("prompts counts prompt and command only", got["prompts"], 3)
    # pr!3 is merged but no session created it, and pr!5 has no state at all
    check("pull requests counts only the new ones, once each", got["pull_requests"], 4)
    check("completed counts completed or merged among the new", got["completed"], 2)
    check("tokens over every session", got["tokens"], {"charged": 45, "cached": 300, "output": 21})


def test_figures_on_page():
    digest = _digest([_group_of("repo-a", [_session("s1", pull_requests_new=["pr!1", "pr!2"])])],
                     pull_requests={"pr!1": {"status": "merged"}, "pr!2": {"status": "active"}})
    page = render.page(_report(), digest)
    check("pull requests shown as completed over new", "<span><b>1 / 2</b> [pull_requests]</span>" in page, True)
    check("charged tokens shown", "<span><b>15</b> [tokens]</span>" in page, True)
    check("cached tokens shown", "<span><b>100</b> [tokens_cached]</span>" in page, True)


# ----- commands and links per platform -------------------------------------------

def _on(platform, is_windows, fn):
    saved_platform, saved_windows = sys.platform, render.windows
    sys.platform = platform
    render.windows = lambda: is_windows
    try:
        return fn()
    finally:
        sys.platform, render.windows = saved_platform, saved_windows


def test_windows_detection():
    saved = sys.platform
    try:
        sys.platform = "win32"
        check("win32 is Windows", render.windows(), True)
        sys.platform = "linux"
        check("linux is not Windows", render.windows(), False)
        sys.platform = "darwin"
        check("darwin is not Windows", render.windows(), False)
    finally:
        sys.platform = saved


def test_resume_command():
    check("Windows changes folder with PowerShell, doubling a quote",
          _on("win32", True, lambda: render.resume_command("C:\\it's here", "abc")),
          "Set-Location -LiteralPath 'C:\\it''s here'; claude --resume abc")
    check("elsewhere changes folder with cd, closing and reopening the quote",
          _on("linux", False, lambda: render.resume_command("/home/o'neil/x", "abc")),
          "cd '/home/o'\\''neil/x' && claude --resume abc")
    check("platform restored afterwards", render.windows(), sys.platform.startswith("win"))


def test_terminal_command():
    check("Windows opens Windows Terminal in the folder",
          _on("win32", True, lambda: render.terminal_command("C:\\a b")), 'wt.exe -d "C:\\a b"')
    check("macOS opens Terminal in the folder",
          _on("darwin", False, lambda: render.terminal_command("/a/it's")), "open -a Terminal '/a/it'\\''s'")
    check("Linux changes folder",
          _on("linux", False, lambda: render.terminal_command("/a/it's")), "cd '/a/it'\\''s'")


def test_deep_link():
    # by hand from RFC 3986: ':' is %3A, '\' is %5C, ' ' is %20, '/' is %2F
    check("folder only, no q", render.deep_link("C:\\a b"), "claude-cli://open?cwd=C%3A%5Ca%20b")
    check("an empty prompt adds no q", render.deep_link("C:\\a b", ""), "claude-cli://open?cwd=C%3A%5Ca%20b")
    check("prompt percent-encoded, slash included", render.deep_link("/w/a b", "/resume s1"),
          "claude-cli://open?cwd=%2Fw%2Fa%20b&q=%2Fresume%20s1")


# ----- lanes and order -----------------------------------------------------------

def test_rows_for():
    s1 = _session("s1", first="09:00", last="10:00")
    s2 = _session("s2", first="09:30", last="11:00")
    s3 = _session("s3", first="10:30", last="12:00")
    rows = render.rows_for({"sessions": [s1, s2, s3]})
    check("overlapping sessions get separate lanes, others share",
          [[s["id"] for s in row["sessions"]] for row in rows], [["s1", "s3"], ["s2"]])
    apart = render.rows_for({"sessions": [s1, _session("s4", first="10:01", last="10:30")]})
    check("sessions apart share one lane", [[s["id"] for s in row["sessions"]] for row in apart], [["s1", "s4"]])
    touching = render.rows_for({"sessions": [s1, _session("s5", first="10:00", last="10:30")]})
    check("sessions sharing a minute do not share a lane",
          [[s["id"] for s in row["sessions"]] for row in touching], [["s1"], ["s5"]])


def test_ordered():
    digest = _digest([_group_of("Beta", [_session("b1")]), _group_of("zeta", [_session("z1")]),
                      _group_of("alpha", [_session("a1")]), _group_of("mid", [_session("m1")])])
    report = _report({"b1": _body(), "a1": _body(), "z1": _body(open_items=[_item("action", "x"), _item("action", "y")]),
                      "m1": _body(open_items=[_item("question", "q")])})
    check("most open first, then by name ignoring case",
          [render.name_of(g) for g in render.ordered(digest, report)], ["zeta", "mid", "alpha", "Beta"])


# ----- the page ------------------------------------------------------------------

def test_page_escapes_transcript_text():
    payload = "<script>evil()</script>"
    escaped = "&lt;script&gt;evil()&lt;/script&gt;"
    report = _report({"s1": _body(topics=["topic " + payload], done=["done " + payload],
                                  open_items=[_item("decision", "open " + payload, since="since " + payload)])},
                     headline="headline " + payload)
    page = render.page(report, _digest())
    check("no raw markup from the report", payload in page, False)
    for field in ("topic", "done", "open", "since", "headline"):
        check("%s escaped" % field, ("%s %s" % (field, escaped)) in page, True)


def test_running_session_has_no_resume():
    running = _session("run1", first="11:00", last="12:00", running="running")
    page = render.page(_report({"s1": _body(), "run1": _body()}),
                       _digest([_group_of("repo-a", [_session("s1"), running])]))
    panel = _panel(page, "s-run1")
    check("running session is marked", '<span class="chip live">[running]</span>' in panel, True)
    check("running session has no resume link", "%2Fresume%20run1" in panel, False)
    check("running session has no copy resume", "[copy_resume]" in panel, False)
    check("running session has no actions section", "<h3>[actions]</h3>" in panel, False)
    stopped = _panel(page, "s-s1")
    link = render.deep_link(_folder("repo-a"), "/resume s1").replace("&", "&amp;")
    check("stopped session has its resume link", ('href="%s"' % link) in stopped, True)
    check("stopped session has copy resume", "[copy_resume]" in stopped, True)


def test_repository_resume_last():
    sessions = [_session("s1", first="09:00", last="11:00"), _session("s2", first="12:00", last="15:00"),
                _session("s3", first="16:00", last="17:00", running="running")]
    report = _report({sid: _body() for sid in ("s1", "s2", "s3")})
    panel = _panel(render.page(report, _digest([_group_of("repo-a", sessions)])), "r-0")
    check("resume the last session is offered", "[resume_last]" in panel, True)
    check("it resumes the latest-ending stopped session", "%2Fresume%20s2" in panel, True)
    check("not an earlier one", "%2Fresume%20s1" in panel, False)
    check("not the running one", "%2Fresume%20s3" in panel, False)

    # listed first but ending latest, so only a pick by end time chooses s1 over the last in list order
    nested = [_session("s1", first="09:00", last="16:00"), _session("s2", first="10:00", last="11:00")]
    panel = _panel(render.page(_report({"s1": _body(), "s2": _body()}), _digest([_group_of("repo-a", nested)])), "r-0")
    check("latest by end time, not by list order", "%2Fresume%20s1" in panel, True)
    check("not the session listed last", "%2Fresume%20s2" in panel, False)

    all_running = [_session("s1", running="running"), _session("s2", first="12:00", last="15:00", running="running")]
    panel = _panel(render.page(_report({"s1": _body(), "s2": _body()}), _digest([_group_of("repo-a", all_running)])), "r-0")
    check("absent when every session is running", "[resume_last]" in panel, False)
    check("no resume link at all", "%2Fresume" in panel, False)
    check("a new session is still offered", "[new_session]" in panel, True)


def test_current_repository_marked():
    digest = _digest([_group_of("repo-a", [_session("s1")], current=True),
                      _group_of("repo-b", [_session("s2", cwd=_folder("repo-b"))])])
    page = render.page(_report({"s1": _body(), "s2": _body()}), digest)
    chip = '<span class="chip here">[here]</span>'
    current = [i for i, g in enumerate(render.ordered(digest, _report({"s1": _body(), "s2": _body()})))
               if g.get("current")][0]
    check("current repository panel carries the chip", chip in _panel(page, "r-%d" % current), True)
    check("the other repository panel does not", chip in _panel(page, "r-%d" % (1 - current)), False)
    check("chip once in the panel and once in the timeline", page.count(chip), 2)
    check("no chip when no repository is current", chip in render.page(_report(), _digest()), False)


def _many(kind, count):
    return [_item(kind, "%s item %02d" % (kind, n)) for n in range(1, count + 1)]


def test_overview_folds_past_limit():
    check("limit is ten", render.OVERVIEW_LIMIT, 10)
    report = _report({"s1": _body(open_items=_many("action", 12) + _many("decision", 10) + _many("question", 3))})
    page = render.page(report, _digest())
    action = _kind_section(page, "action")
    check("ten actions visible", action.count('<li><button'), 10)
    check("two actions folded", action.count('<li class="more" hidden><button'), 2)
    check("the folded ones are the last two",
          re.findall(r'<li class="more" hidden><button[^>]*>(action item \d\d) ', action),
          ["action item 11", "action item 12"])
    check("the visible ones are the first ten in order",
          re.findall(r'<li><button[^>]*>(action item \d\d) ', action), ["action item %02d" % n for n in range(1, 11)])
    check("one show-all button carrying the total", action.count("show-all"), 1)
    check("show-all names the total", '<button type="button" class="btn ghost show-all">[show_all] (12)</button>' in action, True)
    check("heading counts the total", '<span class="count action">[action]</span> 12</h3>' in action, True)
    decision = _kind_section(page, "decision")
    check("exactly at the limit nothing folds", "more" in decision, False)
    check("exactly at the limit no button", "show-all" in decision, False)
    check("under the limit no button", "show-all" in _kind_section(page, "question"), False)


def test_overview_empty():
    page = render.page(_report(), _digest())
    check("no kind section without open items", [_kind_section(page, k) for k in render.KINDS], [None, None, None])
    check("nothing open is said", '<p class="none">[nothing_open]</p>' in _panel(page, "overview"), True)


def test_bars_carry_no_count():
    sessions = [_session("s1"), _session("s2", first="11:00", last="12:00")]
    report = _report({"s1": _body(open_items=_many("action", 3)), "s2": _body()})
    page = render.page(report, _digest([_group_of("repo-a", sessions)]))
    bars = re.findall(r'<button type="button" class="bar( has)?" data-show="s-(\w+)"[^>]*>(.*?)</button>', page)
    check("one bar per session, coloured only when something is open", [(has, sid) for has, sid, _ in bars],
          [(" has", "s1"), ("", "s2")])
    check("bars have no text", [text for _, _, text in bars], ["", ""])


# ----- repository and session panels ---------------------------------------------

def _repository_panel(**group_extra):
    """The r-0 panel of a page with one repository, its group varied by the given fields."""
    digest = _digest([_group_of("repo-a", [_session("s1")], **group_extra)])
    return _panel(render.page(_report(), digest), "r-0")


def _unread_chip(title):
    return '<span class="chip warn" title="%s">[state_unread]</span>' % title


def test_repository_state_unread():
    panel = _repository_panel(is_repository=True, live={"uncommitted": None, "notes": ["git status failed: x"], "branches": []})
    check("an unread repository is marked, its note as the title", _unread_chip("git status failed: x") in panel, True)

    # collect.py --no-live writes no live key, so nothing about the repository was read
    panel = _repository_panel(is_repository=True)
    check("a repository with no live state is marked with an empty title", _unread_chip("") in panel, True)

    panel = _repository_panel(is_repository=True, live={"uncommitted": 0, "notes": [], "branches": []})
    check("a clean repository is not marked unread", "[state_unread]" in panel, False)
    check("a clean repository shows no uncommitted chip", "[uncommitted]" in panel, False)

    for name, extra in (("is_repository False", {"is_repository": False}), ("is_repository absent", {})):
        check("a plain folder is not marked unread (%s)" % name, "[state_unread]" in _repository_panel(**extra), False)

    panel = _repository_panel(is_repository=True, live={"uncommitted": None, "notes": ["first", "second"], "branches": []})
    check("two notes are joined with a semicolon", _unread_chip("first; second") in panel, True)

    panel = _repository_panel(is_repository=True, live={"uncommitted": None, "notes": ['say "hi" <script>x</script>'], "branches": []})
    check("a note is escaped in the title attribute",
          _unread_chip("say &quot;hi&quot; &lt;script&gt;x&lt;/script&gt;") in panel, True)
    check("no raw markup from a note", "<script>x</script>" in panel, False)

    # collect.py writes a note only when git status fails today, but any note marks the repository, count or not
    panel = _repository_panel(is_repository=True, live={"uncommitted": 2, "notes": ["git log failed"], "branches": []})
    check("notes with a count still mark the repository unread", _unread_chip("git log failed") in panel, True)
    check("and the count is shown beside it", '<span class="chip warn">[uncommitted] 2</span>' in panel, True)


def test_repository_live_chips():
    live = {"current_branch": "main", "uncommitted": 3,
            "branches": [{"unpushed": 2}, {"unpushed": None}, {"unpushed": 1}]}
    panel = _repository_panel(is_repository=True, live=live)
    check("the current branch is a chip", '<span class="chip">main</span>' in panel, True)
    check("uncommitted is counted", '<span class="chip warn">[uncommitted] 3</span>' in panel, True)
    # by hand: 2 + 0 + 1, a branch whose count is unknown adding nothing
    check("unpushed is summed over branches", '<span class="chip warn">[unpushed] 3</span>' in panel, True)
    check("a readable repository is not marked unread", "[state_unread]" in panel, False)

    zero = {"current_branch": "main", "uncommitted": 0, "branches": [{"unpushed": 0}, {"unpushed": None}]}
    panel = _repository_panel(is_repository=True, live=zero)
    check("no uncommitted chip at zero", "[uncommitted]" in panel, False)
    check("no unpushed chip at zero", "[unpushed]" in panel, False)


def _copied(panel):
    return [html.unescape(value) for value in re.findall(r'data-copy="([^"]*)"', panel)]


def _links(panel):
    return [html.unescape(value) for value in re.findall(r'<a class="btn" href="([^"]*)"', panel)]


def test_each_button_lives_in_one_place():
    cwd = _folder("repo-a")
    page = render.page(_report(), _digest())
    session = _panel(page, "s-s1")
    check("a stopped session has one link", session.count("<a "), 1)
    check("and one button", session.count("<button"), 1)
    check("the link resumes the session in its folder", _links(session), [render.deep_link(cwd, "/resume s1")])
    check("the button copies the resume command", _copied(session), [render.resume_command(cwd, "s1")])
    for label in ("[new_session]", "[copy_terminal]", "[copy_path]"):
        check("the session panel has no %s" % label, label in session, False)

    repository = _panel(page, "r-0")
    for label in ("[new_session]", "[copy_terminal]", "[copy_path]"):
        check("the repository panel has %s" % label, label in repository, True)
    check("the repository links resume the last session and open a new one",
          _links(repository), [render.deep_link(cwd, "/resume s1"), render.deep_link(cwd)])
    check("the repository copies the resume command, the terminal command and the path",
          _copied(repository), [render.resume_command(cwd, "s1"), render.terminal_command(cwd), cwd])


def test_session_meta():
    usage = {"model": {"input_tokens": 1200, "cache_creation_input_tokens": 300,
                       "cache_read_input_tokens": 900, "output_tokens": 2500000}}
    # a stretch list from an older digest, which the panel must no longer print
    session = _session("s1", usage=usage, active=[["09:05", "09:20"], ["09:41", "09:55"]])
    labels = _labels()
    # a label the renderer no longer reads, so its absence below is a real check
    labels["active"] = "[active]"
    page = render.page(_report(labels=labels), _digest([_group_of("repo-a", [session])]))
    panel = _panel(page, "s-s1")
    # by hand: charged = 1200 + 300 = 1500, which is 1.5k, and output 2500000 is 2.5M
    # cache reads stay out of the charged figure, and 900 is large enough that counting them would show 2.4k
    check("meta is charged tokens, output and the id",
          '<p class="meta"><span>1.5k [tokens]</span><span>2.5M [output]</span><span>s1</span></p>' in panel, True)
    check("no active label on the page", "[active]" in page, False)
    check("no stretch times on the page", ["09:05" in page, "09:41" in page], [False, False])


# ----- the token mix -------------------------------------------------------------

def _legend(page):
    return re.findall(r'<span><i style="background:var\(--mix-(\w+)\)"></i>(.*?)</span>', page)


def _widths(page):
    return re.findall(r'<span style="width:([0-9.]+%);background:var\(--mix-(\w+)\)"></span>', page)


def test_token_mix():
    s1 = _session("s1", usage={"model": {"input_tokens": 50, "cache_creation_input_tokens": 100,
                                         "cache_read_input_tokens": 500, "output_tokens": 25}})
    s2 = _session("s2", first="11:00", last="12:00",
                  usage={"model": {"cache_creation_input_tokens": 50, "cache_read_input_tokens": 100, "output_tokens": 25}})
    page = render.page(_report({"s1": _body(), "s2": _body()}), _digest([_group_of("repo-a", [s1, s2])]))
    # by hand: read 600, write 150, fresh 50, out 50, so whole is 850
    # widths 100*x/850 to three decimals, 70.588 17.647 5.882 5.882, and shares to one decimal, 70.6 17.6 5.9 5.9
    check("the bar is in read, write, fresh, out order with its widths", _widths(page),
          [("70.588%", "read"), ("17.647%", "write"), ("5.882%", "fresh"), ("5.882%", "out")])
    check("the legend names each part by its label, with its count and share", _legend(page),
          [("read", "[cache_read] 600 (70.6%)"), ("write", "[cache_write] 150 (17.6%)"),
           ("fresh", "[uncached_input] 50 (5.9%)"), ("out", "[output] 50 (5.9%)")])
    for literal in ("cache read", "cache write", "uncached input"):
        check("no English literal %r" % literal, literal in page, False)


def test_token_mix_label_escaped():
    labels = _labels()
    labels["cache_read"] = 'cr <b>"x"</b>'
    page = render.page(_report(labels=labels), _digest())
    check("the legend label is escaped",
          '</i>cr &lt;b&gt;&quot;x&quot;&lt;/b&gt; 100 (' in page, True)
    check("no raw markup from the label", 'cr <b>"x"</b>' in page, False)


def test_token_mix_zero_usage():
    zero = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
    for name, usage in (("every bucket zero", {"model": zero}), ("no buckets", {})):
        # whole falls back to 1, so every part is 0 / 1 rather than a division by zero
        page = render.page(_report(), _digest([_group_of("repo-a", [_session("s1", usage=usage)])]))
        check("every width is zero (%s)" % name, [width for width, _ in _widths(page)], ["0.000%"] * 4)
        check("every share is zero (%s)" % name, [text for _, text in _legend(page)],
              ["[cache_read] 0 (0.0%)", "[cache_write] 0 (0.0%)", "[uncached_input] 0 (0.0%)", "[output] 0 (0.0%)"])


# ----- the session summary -------------------------------------------------------

def test_summary():
    sessions = [_session("s1"), _session("s2", first="11:00", last="12:00", running="running")]
    report = _report({"s1": _body(topics=["topic a", "topic b"], open_items=[_item("action", "do x"), _item("question", "ask y")]),
                      "s2": _body(topics=["topic c"])})
    out = os.path.join(tempfile.mkdtemp(), "report.html")
    got = render.summary(report, _digest([_group_of("repo-a", sessions)]), out)
    # written by hand from the format: headline, figures line, one heading per repository, sessions with their open items
    want = "\n".join([
        "**the headline**", "",
        "2026-10-05 \u00b7 2 [sessions] \u00b7 1 [repositories] \u00b7 30 [tokens]", "",
        "### repo-a",
        "- **09:00\u201310:00** topic a / topic b",
        "  - [action]: do x",
        "  - [question]: ask y",
        "- **11:00\u201312:00** ([running]) topic c", "",
        pathlib.Path(out).resolve().as_uri(), "",
        "_[language_hint]_"])
    check("summary is the expected Markdown", got, want)
    check("no table rows", [line for line in got.splitlines() if line.startswith("|")], [])
    check("ends with the page URI then the language hint", got.splitlines()[-3:],
          [pathlib.Path(out).resolve().as_uri(), "", "_[language_hint]_"])
    check("the URI is a file URI", got.splitlines()[-3].startswith("file:///"), True)


def test_summary_repositories_in_order():
    digest = _digest([_group_of("quiet", [_session("q1")]), _group_of("busy", [_session("b1", cwd=_folder("busy"))])])
    report = _report({"q1": _body(), "b1": _body(open_items=[_item("decision", "pick")])})
    headings = [line for line in render.summary(report, digest, "x.html").splitlines() if line.startswith("### ")]
    check("one heading per repository, most open first", headings, ["### busy", "### quiet"])


# ----- main ----------------------------------------------------------------------

def test_main_refuses_invalid_report():
    report = _report()
    del report["day"]
    code, out, out_path = _main(report, _digest())
    check("exits 2", code, render.EXIT_SCHEMA)
    check("EXIT_SCHEMA is 2", render.EXIT_SCHEMA, 2)
    check("names the schema mismatch and the problem", out.splitlines(),
          [SCHEMA_HEADER, "- missing top-level field: day"])
    check("no file written", os.path.exists(out_path), False)


def test_main_writes_page():
    opened = []
    saved = render.webbrowser.open
    render.webbrowser.open = opened.append
    try:
        report, digest = _report(), _digest()
        code, out, out_path = _main(report, digest)
        check("exits 0", code, 0)
        check("page written", os.path.isfile(out_path), True)
        with open(out_path, encoding="utf-8") as f:
            written = f.read()
        check("written page is the rendered page", written, render.page(report, digest))
        check("written page is a page", written.startswith("<!doctype html>"), True)
        check("stdout is the summary", out, render.summary(report, digest, out_path) + "\n")
        check("no browser without --open", opened, [])

        code, out, out_path = _main(report, digest, ["--open"])
        check("--open exits 0", code, 0)
        check("--open opens the page's file URI once", opened, [pathlib.Path(out_path).resolve().as_uri()])
    finally:
        render.webbrowser.open = saved


def test_script_on_cp1252_console():
    # the summary holds a middle dot and an en dash, so a console left at cp1252 would fail on the summary itself
    d = tempfile.mkdtemp()
    report_path, digest_path, out_path = (os.path.join(d, n) for n in ("report.json", "digest.json", "report.html"))
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(_report(), f)
    with open(digest_path, "w", encoding="utf-8") as f:
        json.dump(_digest(), f)
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run([sys.executable, _SCRIPT, report_path, "--digest", digest_path, "--out", out_path],
                          capture_output=True, env=env, timeout=60)
    check("script exits 0", proc.returncode, 0)
    check("stderr empty", proc.stderr, b"")
    check("figures line printed as UTF-8",
          "2026-10-05 \u00b7 1 [sessions] \u00b7 1 [repositories] \u00b7 15 [tokens]" in proc.stdout.decode("utf-8").splitlines(), True)

    bad = copy.deepcopy(_report())
    bad["day"] = "2026-10-04"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    os.remove(out_path)
    proc = subprocess.run([sys.executable, _SCRIPT, report_path, "--digest", digest_path, "--out", out_path],
                          capture_output=True, env=env, timeout=60)
    check("script exits 2 on a mismatch", proc.returncode, 2)
    check("script names the mismatch", proc.stdout.decode("utf-8").splitlines(),
          [SCHEMA_HEADER, "- report day 2026-10-04 is not the digest's day 2026-10-05"])
    check("script writes nothing", os.path.exists(out_path), False)


_TESTS = (test_valid_report_has_no_problems, test_missing_top_level_fields, test_day_not_the_digests,
          test_missing_labels, test_session_not_in_digest, test_digest_session_without_entry,
          test_session_without_topics, test_open_item_kind_and_text,
          test_tokens, test_compact, test_compact_top_unit, test_figures, test_figures_on_page,
          test_windows_detection, test_resume_command, test_terminal_command, test_deep_link,
          test_rows_for, test_ordered,
          test_page_escapes_transcript_text, test_running_session_has_no_resume, test_repository_resume_last,
          test_current_repository_marked, test_overview_folds_past_limit, test_overview_empty,
          test_bars_carry_no_count,
          test_repository_state_unread, test_repository_live_chips, test_each_button_lives_in_one_place,
          test_session_meta,
          test_token_mix, test_token_mix_label_escaped, test_token_mix_zero_usage,
          test_summary, test_summary_repositories_in_order,
          test_main_refuses_invalid_report, test_main_writes_page, test_script_on_cp1252_console)


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
