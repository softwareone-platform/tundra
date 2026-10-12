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
    """Every label, each naming its own key, so a check can tell which label landed where.
    A label that carries numbers keeps its placeholders after the key, since the schema requires them."""
    labels = {key: "[%s]" % key for key in render.LABELS}
    for key, names in render.PLACEHOLDERS.items():
        labels[key] = "[%s %s]" % (key, " ".join("{%s}" % name for name in names))
    return labels


_DAY = "2026-10-05"


def _stamp(when):
    """A bare HH:MM falls on the fixture day, so a one-day case names only its times."""
    return when if " " in when else "%s %s" % (_DAY, when)


def _session(sid, first="09:00", last="10:00", cwd=None, **extra):
    first, last = _stamp(first), _stamp(last)
    session = {"id": sid, "first": first, "last": last, "cwd": cwd or _folder("repo-a"),
               "days": sorted({first.partition(" ")[0], last.partition(" ")[0]}),
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
    digest = {"from": _DAY, "to": _DAY, "earliest": "2026-09-05", "generated": "2026-10-06T08:00:00+02:00",
              "repositories": groups if groups is not None else [_group_of("repo-a", [_session("s1")])],
              "pull_requests": {}}
    digest.update(extra)
    return digest


def _body(topics=("topic of the session",), open_items=(), done=()):
    return {"topics": list(topics), "open": list(open_items), "done": list(done)}


def _report(sessions=None, **extra):
    """The smallest report the schema accepts for the default digest: every label and one session entry."""
    report = {"from": _DAY, "to": _DAY, "language": "en", "headline": "the headline", "labels": _labels(),
              "sessions": sessions if sessions is not None else {"s1": _body()}}
    report.update(extra)
    return report


def _item(kind, text, **extra):
    item = {"kind": kind, "text": text}
    item.update(extra)
    return item


def _panel(page, panel_id):
    """One panel of the page, from its opening tag to the next panel or the end of the main element.
    Its id is "p-" for the overview, "p-r-<slug>" for a repository and "p-s-<id>" for a session."""
    start = page.index('<section class="panel" id="%s"' % panel_id)
    ends = [i for i in (page.find('<section class="panel"', start + 1), page.find("</main>", start)) if i >= 0]
    return page[start:min(ends)]


def _kind_section(page, kind):
    """The overview's card of open items of one kind, or None when the overview has none."""
    overview = _panel(page, "p-")
    # the twin cards of what happened carry "twin" before the kind, so they never match here
    found = re.search(r'<section class="card col %s( clamp)?"(?: data-kind="[^"]*" data-count="\d+")?>' % kind, overview)
    if found is None:
        return None
    return overview[found.start():overview.index("</section>", found.start())]


def _head(panel):
    """The header card of a repository or session panel, where its buttons live."""
    start = panel.index('<section class="card head scope">')
    return panel[start:panel.index("</section>", start)]


def _icon_chip(cls, text):
    """A pattern for a chip whose icon comes before its text, the icon kept from running into a later chip."""
    return r'<span class="%s"><svg class="i"[^>]*>(?:(?!</svg>).)*</svg>%s</span>' % (re.escape(cls), re.escape(text))


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
    body = dict(_body(topics=["a", "b"], done=["finished x"],
                      open_items=[_item(kind, "text " + kind, detail="why " + kind) for kind in render.KINDS]),
                decided=["chose y"], found=["cause z"])
    full = _report({"s1": body}, marks={_folder("repo-a"): "\U0001F9FE"})
    check("report using every kind and every optional field is valid", render.problems_in(full, _digest()), [])


def test_missing_top_level_fields():
    for key in ("from", "to", "language", "headline", "labels", "sessions"):
        report = _report()
        del report[key]
        check("missing %s is named" % key, render.problems_in(report, _digest()), ["missing top-level field: %s" % key])
    # with a top-level field missing the rest cannot be read, so nothing past them is reported
    check("only top-level problems when one is missing",
          render.problems_in({"from": "2000-01-01", "to": "2000-01-01"}, _digest()),
          ["missing top-level field: language", "missing top-level field: headline",
           "missing top-level field: labels", "missing top-level field: sessions"])


def test_range_not_the_digests():
    check("a different from is named", render.problems_in(_report(**{"from": "2026-10-04"}), _digest()),
          ["report from 2026-10-04 is not the digest's from 2026-10-05"])
    check("a different to is named", render.problems_in(_report(to="2026-10-06"), _digest()),
          ["report to 2026-10-06 is not the digest's to 2026-10-05"])
    check("both ends differing are named, from first",
          render.problems_in(_report(**{"from": "2026-10-04", "to": "2026-10-06"}), _digest()),
          ["report from 2026-10-04 is not the digest's from 2026-10-05",
           "report to 2026-10-06 is not the digest's to 2026-10-05"])


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
    topics_problem = "session s1 needs topics, a list of non-blank strings"
    check("empty topics", render.problems_in(_report({"s1": _body(topics=[])}), _digest()), [topics_problem])
    check("absent topics", render.problems_in(_report({"s1": {"open": []}}), _digest()), [topics_problem])


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
    # rows_for reads the view page() and summary() leave behind, so pin it to one day's view
    saved = render.VIEW["range"]
    render.VIEW["range"] = False
    try:
        rows = render.rows_for({"sessions": [s1, s2, s3]})
        apart = render.rows_for({"sessions": [s1, _session("s4", first="10:01", last="10:30")]})
        touching = render.rows_for({"sessions": [s1, _session("s5", first="10:00", last="10:30")]})
    finally:
        render.VIEW["range"] = saved
    check("overlapping sessions get separate lanes, others share",
          [[s["id"] for s in row["sessions"]] for row in rows], [["s1", "s3"], ["s2"]])
    check("sessions apart share one lane", [[s["id"] for s in row["sessions"]] for row in apart], [["s1", "s4"]])
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
    # no letters or digits and one character from the emoji blocks, so it passes as a mark and reaches the tile
    mark = "<\U0001F9FE>"
    body = dict(_body(topics=["topic " + payload], done=["done " + payload],
                      open_items=[_item("decision", "open " + payload, detail="detail " + payload)]),
                decided=["decided " + payload], found=["found " + payload])
    report = _report({"s1": body}, headline="headline " + payload, marks={_folder("repo-a"): mark})
    page = render.page(report, _digest())
    check("no raw markup from the report", payload in page, False)
    for field in ("topic", "done", "open", "detail", "decided", "found", "headline"):
        check("%s escaped" % field, ("%s %s" % (field, escaped)) in page, True)
    check("mark escaped in its tile", '<span class="tile emoji" aria-hidden="true">&lt;\U0001F9FE&gt;</span>' in page, True)
    check("no raw mark", mark in page, False)


def test_theme_labels_cannot_close_script():
    payload = "x</script><script>alert(1)</script>"
    for key, side, other in (("theme_light", "light", "dark"), ("theme_dark", "dark", "light")):
        labels = _labels()
        labels[key] = payload
        page = render.page(_report(labels=labels), _digest())
        # the page's own markup opens and closes exactly one script element, so any extra tag came from the label
        check("one script opened (%s)" % key, page.lower().count("<script"), 1)
        check("one script closed (%s)" % key, page.lower().count("</script"), 1)
        # read to the end of the page, because a raw label would close the script early and so fall outside a slice that stops at "</script>"
        check("no raw label in the script (%s)" % key, payload in page[page.index("<script>"):], False)
        # the pill is drawn on the server, so each radio button carries its label as escaped text after its icon
        faces = dict(re.findall(r'<button type="button" role="radio" data-theme-set="(\w+)">'
                                r'<svg class="i"[^>]*>(?:(?!</svg>).)*</svg>(.*?)</button>', page))
        check("the pill shows the label escaped (%s)" % key, faces,
              {side: html.escape(payload), other: "[theme_%s]" % other})


def test_running_session_has_no_resume():
    running = _session("run1", first="11:00", last="12:00", running="running")
    page = render.page(_report({"s1": _body(), "run1": _body()}),
                       _digest([_group_of("repo-a", [_session("s1"), running])]))
    panel = _panel(page, "p-s-run1")
    check("running session is marked", '<span class="chip live big"><i class="dot"></i>[cannot_resume]</span>' in panel, True)
    check("running session has no resume link", "%2Fresume%20run1" in panel, False)
    check("running session has no copy resume", "[copy_resume]" in panel, False)
    stopped = _panel(page, "p-s-s1")
    link = render.deep_link(_folder("repo-a"), "/resume s1").replace("&", "&amp;")
    check("stopped session has its resume link", ('href="%s"' % link) in stopped, True)
    check("stopped session has copy resume", "[copy_resume]" in stopped, True)


def test_repository_resume_last():
    sessions = [_session("s1", first="09:00", last="11:00"), _session("s2", first="12:00", last="15:00"),
                _session("s3", first="16:00", last="17:00", running="running")]
    report = _report({sid: _body() for sid in ("s1", "s2", "s3")})
    panel = _panel(render.page(report, _digest([_group_of("repo-a", sessions)])), "p-r-repo-a")
    check("resume the last session is offered", "[resume_last]" in panel, True)
    check("it resumes the latest-ending stopped session", "%2Fresume%20s2" in panel, True)
    check("not an earlier one", "%2Fresume%20s1" in panel, False)
    check("not the running one", "%2Fresume%20s3" in panel, False)

    # listed first but ending latest, so only a pick by end time chooses s1 over the last in list order
    nested = [_session("s1", first="09:00", last="16:00"), _session("s2", first="10:00", last="11:00")]
    panel = _panel(render.page(_report({"s1": _body(), "s2": _body()}), _digest([_group_of("repo-a", nested)])), "p-r-repo-a")
    check("latest by end time, not by list order", "%2Fresume%20s1" in panel, True)
    check("not the session listed last", "%2Fresume%20s2" in panel, False)

    all_running = [_session("s1", running="running"), _session("s2", first="12:00", last="15:00", running="running")]
    panel = _panel(render.page(_report({"s1": _body(), "s2": _body()}), _digest([_group_of("repo-a", all_running)])), "p-r-repo-a")
    check("absent when every session is running", "[resume_last]" in panel, False)
    check("no resume link at all", "%2Fresume" in panel, False)
    check("a new session is still offered", "[new_session]" in panel, True)


def test_current_repository_marked():
    digest = _digest([_group_of("repo-a", [_session("s1")], current=True),
                      _group_of("repo-b", [_session("s2", cwd=_folder("repo-b"))])])
    page = render.page(_report({"s1": _body(), "s2": _body()}), digest)
    chip = _icon_chip("chip here", "[here]")
    check("current repository panel carries the chip", re.search(chip, _panel(page, "p-r-repo-a")) is not None, True)
    check("the other repository panel does not", re.search(chip, _panel(page, "p-r-repo-b")) is not None, False)
    # the timeline no longer draws the chip, so the repository header is its only place
    check("chip once on the page", len(re.findall(chip, page)), 1)
    check("no chip when no repository is current", re.search(chip, render.page(_report(), _digest())) is not None, False)


def _many(kind, count):
    return [_item(kind, "%s item %02d" % (kind, n)) for n in range(1, count + 1)]


def test_overview_folds_past_limit():
    check("limit is ten", render.OVERVIEW_LIMIT, 10)
    check("the overview shows three per repository", render.PER_REPOSITORY, 3)
    report = _report({"s1": _body(open_items=_many("action", 12) + _many("decision", 10) + _many("question", 3))})
    page = render.page(report, _digest())
    action = _kind_section(page, "action")
    check("the overview's action card clamps",
          action.startswith('<section class="card col action clamp" data-kind="action" data-count="12">'), True)
    check("three actions visible", action.count('<li data-go='), 3)
    check("nine actions beyond the repository's first three", action.count('<li class="beyond" data-go='), 9)
    # newest first, and within one session the last the model listed comes first
    check("the visible ones are the last three listed, newest first",
          re.findall(r'<li data-go=[^>]*><div class="t">(action item \d\d)</div>', action),
          ["action item 12", "action item 11", "action item 10"])
    check("the ones beyond follow in the same order",
          re.findall(r'<li class="beyond" data-go=[^>]*><div class="t">(action item \d\d)</div>', action),
          ["action item %02d" % n for n in range(9, 0, -1)])
    check("a link points to the rest on the repository's page",
          '<button type="button" class="btn quiet more-link" data-go="r-repo-a">[more_in_repository 9 repo-a]</button>' in action, True)
    check("no show-all on the overview", "show-all" in action, False)
    check("heading counts the total",
          re.search(r'<h3 class="kindhead"><span class="kind"><svg class="i"[^>]*>(?:(?!</svg>).)*</svg>\[action\]</span> 12'
                    r'<button type="button" class="maxbtn grow" data-max-kind="action"', action) is not None, True)
    question = _kind_section(page, "question")
    check("exactly at three per repository nothing goes beyond", "beyond" in question, False)
    check("exactly at three per repository no link to the rest", "more-link" in question, False)
    # the limit of ten still holds below the overview
    cards = dict(_cards(_panel(page, "p-r-repo-a")))
    check("exactly at the limit nothing folds", 'class="more"' in cards["decision"], False)
    check("exactly at the limit no button", "show-all" in cards["decision"], False)
    check("under the limit no button", "show-all" in cards["question"], False)


def test_overview_empty():
    page = render.page(_report(), _digest())
    overview = _panel(page, "p-")
    # the overview keeps a card for every kind, so a quiet day still shows where open items would go
    for kind in render.KINDS:
        section = _kind_section(page, kind)
        check("a %s card with nothing open" % kind, section is not None, True)
        if section is None:
            continue
        check("the %s card counts zero" % kind, ('[%s]</span> 0<button type="button" class="maxbtn grow"' % kind) in section, True)
        check("the %s card lists nothing" % kind, "<li" in section, False)
    check("no what-happened lane when nothing happened", '<details class="folded">' in overview, False)
    check("nothing open is not said on the overview", "[nothing_open]" in overview, False)


def test_bars_carry_no_count():
    sessions = [_session("s1"), _session("s2", first="11:00", last="12:00")]
    report = _report({"s1": _body(open_items=_many("action", 3)), "s2": _body()})
    page = render.page(report, _digest([_group_of("repo-a", sessions)]))
    bars = re.findall(r'<button type="button" class="bar( has)?" data-go="s-(\w+)"[^>]*>(.*?)</button>', _panel(page, "p-"))
    check("one bar per session, coloured only when something is open", [(has, sid) for has, sid, _ in bars],
          [(" has", "s1"), ("", "s2")])
    check("bars have no text", [text for _, _, text in bars], ["", ""])


# ----- repository and session panels ---------------------------------------------

def _repository_panel(**group_extra):
    """The repo-a panel of a page with one repository, its group varied by the given fields."""
    digest = _digest([_group_of("repo-a", [_session("s1")], **group_extra)])
    return _panel(render.page(_report(), digest), "p-r-repo-a")


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
    check("and the count is shown beside it", re.search(_icon_chip("chip warn", "[uncommitted] 2"), panel) is not None, True)


def test_repository_live_chips():
    live = {"current_branch": "main", "uncommitted": 3,
            "branches": [{"unpushed": 2}, {"unpushed": None}, {"unpushed": 1}]}
    panel = _repository_panel(is_repository=True, live=live)
    check("the current branch is a chip", re.search(_icon_chip("chip", "main"), panel) is not None, True)
    check("uncommitted is counted", re.search(_icon_chip("chip warn", "[uncommitted] 3"), panel) is not None, True)
    # by hand: 2 + 0 + 1, a branch whose count is unknown adding nothing
    check("unpushed is summed over branches", re.search(_icon_chip("chip warn", "[unpushed] 3"), panel) is not None, True)
    check("a readable repository is not marked unread", "[state_unread]" in panel, False)

    zero = {"current_branch": "main", "uncommitted": 0, "branches": [{"unpushed": 0}, {"unpushed": None}]}
    panel = _repository_panel(is_repository=True, live=zero)
    check("no uncommitted chip at zero", "[uncommitted]" in panel, False)
    check("no unpushed chip at zero", "[unpushed]" in panel, False)


def _copied(panel):
    return [html.unescape(value) for value in re.findall(r'data-copy="([^"]*)"', panel)]


def _links(panel):
    return [html.unescape(value) for value in re.findall(r'<a class="btn(?: ghost)?" href="([^"]*)"', panel)]


def test_each_button_lives_in_one_place():
    cwd = _folder("repo-a")
    page = render.page(_report(), _digest())
    session = _panel(page, "p-s-s1")
    # the timeline below the header has buttons of its own, so the count is over the header alone
    head = _head(session)
    check("a stopped session has one link", head.count("<a "), 1)
    check("and one button", head.count("<button"), 1)
    check("the link resumes the session in its folder", _links(head), [render.deep_link(cwd, "/resume s1")])
    check("the button copies the resume command", _copied(head), [render.resume_command(cwd, "s1")])
    for label in ("[new_session]", "[copy_terminal]", "[copy_path]"):
        check("the session panel has no %s" % label, label in session, False)

    repository = _head(_panel(page, "p-r-repo-a"))
    for label in ("[new_session]", "[copy_terminal]", "[copy_path]"):
        check("the repository header has %s" % label, label in repository, True)
    check("the repository links resume the last session and open a new one",
          _links(repository), [render.deep_link(cwd, "/resume s1"), render.deep_link(cwd)])
    check("the repository copies the terminal command and the path, not a resume command",
          _copied(repository), [render.terminal_command(cwd), cwd])


def test_session_meta():
    usage = {"model": {"input_tokens": 1200, "cache_creation_input_tokens": 300,
                       "cache_read_input_tokens": 900, "output_tokens": 2500000}}
    # a stretch list from an older digest, which the panel must no longer print
    session = _session("s1", usage=usage, active=[["09:05", "09:20"], ["09:41", "09:55"]])
    labels = _labels()
    # a label the renderer no longer reads, so its absence below is a real check
    labels["active"] = "[active]"
    page = render.page(_report(labels=labels), _digest([_group_of("repo-a", [session])]))
    panel = _panel(page, "p-s-s1")
    # by hand: charged = 1200 + 300 = 1500, which is 1.5k, and output 2500000 is 2.5M
    # cache reads stay out of the charged figure, and 900 is large enough that counting them would show 2.4k
    check("meta is charged tokens, output and the id",
          '<div class="path">1.5k [tokens] \u00b7 2.5M [output] \u00b7 s1</div>' in _head(panel), True)
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
    del report["from"]
    code, out, out_path = _main(report, _digest())
    check("exits 2", code, render.EXIT_SCHEMA)
    check("EXIT_SCHEMA is 2", render.EXIT_SCHEMA, 2)
    check("names the schema mismatch and the problem", out.splitlines(),
          [SCHEMA_HEADER, "- missing top-level field: from"])
    check("no file written", os.path.exists(out_path), False)


def test_main_refuses_wrong_types():
    code, out, out_path = _main([], _digest())
    check("a report that is a list exits 2", code, render.EXIT_SCHEMA)
    check("a report that is a list is named", out.splitlines(), [SCHEMA_HEADER, "- the report must be a JSON object"])
    check("a report that is a list writes nothing", os.path.exists(out_path), False)
    report = _report(labels=None)
    report["sessions"] = []
    code, out, out_path = _main(report, _digest())
    check("labels and sessions not objects exits 2", code, render.EXIT_SCHEMA)
    check("labels and sessions not objects are both named", out.splitlines(),
          [SCHEMA_HEADER, "- labels must be an object", "- sessions must be an object"])
    check("labels and sessions not objects writes nothing", os.path.exists(out_path), False)


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
    bad["from"] = "2026-10-04"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    os.remove(out_path)
    proc = subprocess.run([sys.executable, _SCRIPT, report_path, "--digest", digest_path, "--out", out_path],
                          capture_output=True, env=env, timeout=60)
    check("script exits 2 on a mismatch", proc.returncode, 2)
    check("script names the mismatch", proc.stdout.decode("utf-8").splitlines(),
          [SCHEMA_HEADER, "- report from 2026-10-04 is not the digest's from 2026-10-05"])
    check("script writes nothing", os.path.exists(out_path), False)


# ----- problems_in: placeholders, unknown fields, the lists of what happened -----

def test_placeholder_labels():
    labels = _labels()
    labels["session_count"] = "{repository}: {n}"
    labels["duration"] = "{minutes} min, {hours} h"
    check("placeholders kept in another word order are accepted", render.problems_in(_report(labels=labels), _digest()), [])
    labels = _labels()
    labels["session_count"] = "[session_count {n}]"
    check("a label losing one placeholder names it", render.problems_in(_report(labels=labels), _digest()),
          ["label session_count must keep {repository}"])


def test_unknown_fields():
    report = _report()
    report["sesions"] = {}
    check("a misspelt top-level field is named", render.problems_in(report, _digest()), ["unknown top-level field: sesions"])
    body = dict(_body(), decisions=["chose x"])
    check("an unknown session field is named", render.problems_in(_report({"s1": body}), _digest()),
          ["session s1: unknown field decisions"])
    body = _body(open_items=[_item("action", "x", since="2026-10-04")])
    check("an unknown open item field is named", render.problems_in(_report({"s1": body}), _digest()),
          ["session s1: an open item has an unknown field since"])


def test_happened_lists_must_be_lines():
    for key in ("decided", "done", "found"):
        want = ["session s1: %s must be a list of non-blank strings" % key]
        for name, value in (("a string", "one line"), ("an object", {"a": "b"}), ("a blank line", ["ok", "  "]),
                            ("a number line", ["ok", 5]), ("a null line", [None])):
            body = dict(_body(), **{key: value})
            check("%s as %s" % (key, name), render.problems_in(_report({"s1": body}), _digest()), want)
        check("%s empty is valid" % key, render.problems_in(_report({"s1": dict(_body(), **{key: []})}), _digest()), [])


def test_topics_with_a_bad_line():
    topics_problem = "session s1 needs topics, a list of non-blank strings"
    check("topics holding a blank line", render.problems_in(_report({"s1": _body(topics=["a", "  "])}), _digest()), [topics_problem])
    check("topics holding a number", render.problems_in(_report({"s1": _body(topics=["a", 5])}), _digest()), [topics_problem])


def test_detail_must_be_a_string():
    want = ["session s1: an open item's detail must be a string"]
    for name, detail in (("a number", 5), ("null", None), ("a list", ["why"])):
        body = _body(open_items=[_item("action", "x", detail=detail)])
        check("a detail that is %s" % name, render.problems_in(_report({"s1": body}), _digest()), want)
    # blank detail renders nothing, so it is allowed
    body = _body(open_items=[_item("action", "x", detail="")])
    check("an empty detail is valid", render.problems_in(_report({"s1": body}), _digest()), [])


def test_session_body_not_an_object():
    for name, body in (("a list", ["topic"]), ("a string", "topic")):
        check("a session body that is %s" % name, render.problems_in(_report({"s1": body}), _digest()), ["session s1 must be an object"])


def test_open_not_a_list_of_objects():
    want = ["session s1: open must be a list of objects"]
    for name, items in (("a string", "x"), ("a list of strings", ["x"]), ("one object", {"kind": "action", "text": "x"})):
        body = dict(_body(), open=items)
        check("open as %s" % name, render.problems_in(_report({"s1": body}), _digest()), want)


def test_shared_problem_reported_once():
    body = _body(open_items=[_item("todo", "x"), _item("todo", "y")])
    check("two items with one wrong kind give one line", render.problems_in(_report({"s1": body}), _digest()),
          ["session s1: open item kind must be one of decision, action, question"])
    body = _body(open_items=[_item("action", "x", since="a"), _item("action", "y", since="b")])
    check("two items with one unknown field give one line", render.problems_in(_report({"s1": body}), _digest()),
          ["session s1: an open item has an unknown field since"])


def test_bad_marks_never_a_problem():
    path = _folder("repo-a")
    for name, given in (("a list", ["\U0001F9FE"]), ("a string", "\U0001F9FE"), ("a non-emoji value", {path: "a"}),
                        ("a list value", {path: ["\U0001F9FE"]}), ("an unknown path", {_folder("nowhere"): "\U0001F9FE"})):
        report = _report(marks=given)
        page = render.page(report, _digest())
        check("marks as %s is no problem" % name, render.problems_in(report, _digest()), [])
        check("marks as %s still renders" % name, page.startswith("<!doctype html>"), True)
        check("marks as %s leaves repo-a its initial" % name,
              '<span class="tile" aria-hidden="true">R</span>' in _head(_panel(page, "p-r-repo-a")), True)
        # repo-a is the only repository, so any emoji tile on the page would be repo-a's
        check("marks as %s draws no emoji tile" % name, 'class="tile emoji"' in page, False)


# ----- problems_in: values of the wrong type ------------------------------------

def test_report_not_an_object():
    for name, report in (("a list", []), ("a string", "day"), ("null", None), ("a number", 5)):
        check("a report that is %s" % name, render.problems_in(report, _digest()), ["the report must be a JSON object"])


def test_labels_not_an_object():
    # with no object to read, neither a missing label nor a lost placeholder is reported on top
    for name, labels in (("a list", []), ("a string", "x"), ("null", None)):
        check("labels as %s" % name, render.problems_in(_report(labels=labels), _digest()), ["labels must be an object"])


def test_sessions_not_an_object():
    for name, sessions in (("a list", []), ("a string", "x"), ("null", None)):
        report = _report()
        # _report reads a None sessions as the default ones, so the value is set after building
        report["sessions"] = sessions
        check("sessions as %s" % name, render.problems_in(report, _digest()), ["sessions must be an object"])
    report = _report(labels=None, headline=5, extra="x")
    report["sessions"] = []
    check("problems before a sessions that is not an object are still reported, in order",
          render.problems_in(report, _digest()),
          ["headline must be a non-blank string", "labels must be an object", "unknown top-level field: extra",
           "sessions must be an object"])


def test_language_and_headline_must_be_text():
    for key in ("language", "headline"):
        for name, value in (("a number", 5), ("null", None), ("a list", ["x"]), ("blank", "  ")):
            check("%s as %s" % (key, name), render.problems_in(_report(**{key: value}), _digest()),
                  ["%s must be a non-blank string" % key])


def test_label_not_a_string():
    for label in ("here", "duration"):
        for name, value in (("a number", 5), ("a list", ["x"]), ("null", None)):
            labels = _labels()
            labels[label] = value
            # a label already missing is not also told to keep its placeholders
            check("label %s as %s" % (label, name), render.problems_in(_report(labels=labels), _digest()),
                  ["labels missing: %s" % label])


# ----- the repository mark -------------------------------------------------------

def test_emoji_like_accepts():
    for name, value in (("a single emoji", "\U0001F9FE"), ("an emoji with U+FE0F", "\u2600\ufe0f"),
                        ("a heart with U+FE0F", "\u2764\ufe0f"), ("a ZWJ sequence", "\U0001F468\u200d\U0001F469\u200d\U0001F467"),
                        ("eight code points", "\U0001F9FE" * 8),
                        ("the first of U+1F300-U+1FAFF", "\U0001F300"), ("the last of U+1F300-U+1FAFF", "\U0001FAFF"),
                        ("the first of U+2600-U+27BF", "\u2600"), ("the last of U+2600-U+27BF", "\u27bf")):
        check("emoji_like accepts %s" % name, render.emoji_like(value), True)


def test_emoji_like_rejects():
    for name, value in (("an ASCII letter", "a"), ("an ASCII digit", "7"),
                        ("an emoji with an ASCII letter", "\U0001F9FEa"), ("an emoji with an ASCII digit", "\U0001F9FE1"),
                        ("a flag", "\U0001F1F9\U0001F1FC"), ("a flag beside an emoji", "\U0001F1F9\U0001F1FC\u2600"),
                        ("a CJK character", "\u5009"), ("empty", ""), ("blank", "   "),
                        ("nine code points", "\U0001F9FE" * 9), ("an arrow outside the blocks", "\u2190"),
                        ("just below U+1F300", "\U0001F2FF"), ("just above U+1FAFF", "\U0001FB00"),
                        ("just below U+2600", "\u25ff"), ("just above U+27BF", "\u27c0"),
                        ("None", None), ("a number", 5), ("a list", ["\U0001F9FE"])):
        check("emoji_like rejects %s" % name, render.emoji_like(value), False)


def test_same_path():
    for name, left, right, want in (("case", "C:\\Work\\Repo", "c:\\work\\repo", True),
                                    ("slash direction", "C:\\work\\repo", "C:/work/repo", True),
                                    ("a trailing slash", "C:/work/repo/", "C:/work/repo", True),
                                    ("a trailing backslash and case", "C:\\work\\repo\\", "c:/WORK/repo", True),
                                    ("another folder", "C:/work/repo", "C:/work/repo-b", False),
                                    ("a parent folder", "C:/work", "C:/work/repo", False)):
        check("same_path over %s" % name, render.same_path(left, right), want)


def _groups(*names):
    return [{"path": _folder(name)} for name in names]


def test_marks_match_paths_loosely():
    path = _folder("repo-a")
    for name, key in (("upper case, forward slashes and a trailing slash", path.replace("\\", "/").upper() + "/"),
                      ("backslashes and a trailing backslash", path.replace("/", "\\") + "\\")):
        check("a key in %s finds its repository" % name, render.marks({"marks": {key: "\U0001F9FE"}}, _groups("repo-a")),
              ({path: "\U0001F9FE"}, []))


def test_marks_duplicates():
    given = {"marks": {_folder("first"): "\u2600", _folder("second"): "\u2600\ufe0f"}}
    check("a duplicate compared without U+FE0F keeps the first repository's",
          render.marks(given, _groups("first", "second")), ({_folder("first"): "\u2600"}, ["%s (emoji already used)" % _folder("second")]))
    check("the first is decided by the order given, not the key order",
          render.marks(given, _groups("second", "first")), ({_folder("second"): "\u2600\ufe0f"}, ["%s (emoji already used)" % _folder("first")]))
    same = {"marks": {_folder("first"): "\U0001F9FE", _folder("second"): "\U0001F9FE"}}
    check("an identical duplicate", render.marks(same, _groups("first", "second")),
          ({_folder("first"): "\U0001F9FE"}, ["%s (emoji already used)" % _folder("second")]))

    # first is listed first in the digest, but second has more open, so ordered() puts second first on the page
    digest = _digest([_group_of("first", [_session("f1", cwd=_folder("first"))]),
                      _group_of("second", [_session("s1", cwd=_folder("second"))])])
    report = _report({"f1": _body(), "s1": _body(open_items=_many("action", 2))}, marks=given["marks"])
    page = render.page(report, digest)
    check("on the page the repository first in ordered() keeps the emoji",
          '<span class="tile emoji" aria-hidden="true">\u2600\ufe0f</span>' in _head(_panel(page, "p-r-second")), True)
    check("and the later duplicate shows its initial",
          '<span class="tile" aria-hidden="true">F</span>' in _head(_panel(page, "p-r-first")), True)


def test_marks_unusable():
    groups = _groups("repo-a")
    check("no marks", render.marks({}, groups), ({}, []))
    for name, given in (("a list", ["\U0001F9FE"]), ("a string", "\U0001F9FE"), ("a number", 5)):
        check("marks as %s is not an object" % name, render.marks({"marks": given}, groups), ({}, ["marks is not an object"]))
    check("a key naming no repository in the digest",
          render.marks({"marks": {_folder("nowhere"): "\U0001F9FE"}}, groups), ({}, ["%s (not a repository in the digest)" % _folder("nowhere")]))
    check("a value that is not one emoji",
          render.marks({"marks": {_folder("repo-a"): "a"}}, groups), ({}, ["%s (not one emoji)" % _folder("repo-a")]))


_TILE = r'<span class="tile( emoji)?" aria-hidden="true">([^<]*)</span>'


def _tile(text, emoji=False):
    return '<span class="tile%s" aria-hidden="true">%s</span>' % (" emoji" if emoji else "", text)


def test_tile_is_the_same_everywhere():
    mark = "\U0001F9FE"
    digest = _digest([_group_of("alpha", [_session("a1", cwd=_folder("alpha"))]),
                      _group_of("beta", [_session("b1", cwd=_folder("beta"))])])
    report = _report({"a1": _body(open_items=[_item("action", "alpha item")], done=["alpha done"]),
                      "b1": _body(open_items=[_item("action", "beta item")], done=["beta done"])},
                     marks={_folder("alpha"): mark, _folder("beta"): "b"})
    page = render.page(report, digest)
    side = page[page.index('<aside class="card side">'):page.index("</aside>")]
    overview = _panel(page, "p-")
    timeline = overview[overview.index('<section class="card timeline"'):]
    timeline = timeline[:timeline.index("</section>")]
    for slug, tile in (("alpha", _tile(mark, emoji=True)), ("beta", _tile("B"))):
        trail = re.search(r'<div class="trail-of" data-for="r-%s"[^>]*>(.*?)</div>' % slug, page).group(1)
        check("%s in the sidebar" % slug, (tile + '<span class="nm">%s</span>' % slug) in side, True)
        check("%s in the timeline" % slug, (tile + '<span class="nm">%s</span>' % slug) in timeline, True)
        check("%s in the trail" % slug, (tile + slug) in trail, True)
        check("%s in the overview's repository block" % slug,
              ('<div class="ghead">%s<span class="nm">%s</span><b>1</b></div>' % (tile, slug)) in overview, True)
        check("%s in the repository header" % slug, tile in _head(_panel(page, "p-r-" + slug)), True)
    # one emoji tile and one initial tile, so no place drew the other form
    check("never a mix of emoji and initial", sorted(set(re.findall(_TILE, page))), [("", "B"), (" emoji", mark)])


def test_main_with_bad_marks():
    report = _report(marks={_folder("repo-a"): "a", _folder("nowhere"): "\U0001F9FE"})
    digest = _digest()
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        code, out, out_path = _main(report, digest)
        err = sys.stderr.getvalue()
    finally:
        sys.stderr = saved
    check("exits 0", code, 0)
    check("page written", os.path.isfile(out_path), True)
    with open(out_path, encoding="utf-8") as f:
        written = f.read()
    check("the page shows the initial", '<span class="tile" aria-hidden="true">R</span>' in written, True)
    check("one warning line on stderr", err.count("\n"), 1)
    check("the warning starts with what was done", err.startswith("marks ignored, initials used instead: "), True)
    check("it names the non-emoji value", ("%s (not one emoji)" % _folder("repo-a")) in err, True)
    check("it names the unknown key", ("%s (not a repository in the digest)" % _folder("nowhere")) in err, True)
    check("stdout is the summary alone", out, render.summary(report, digest, out_path) + "\n")
    check("stdout says nothing about marks", "marks" in out, False)

    sys.stderr = io.StringIO()
    try:
        code, _, _ = _main(_report(marks=["x"]), digest)
        err = sys.stderr.getvalue()
    finally:
        sys.stderr = saved
    check("marks not an object exits 0", code, 0)
    check("and is named on stderr", err, "marks ignored, initials used instead: marks is not an object\n")

    sys.stderr = io.StringIO()
    try:
        _main(_report(marks={_folder("repo-a"): "\U0001F9FE"}), digest)
        err = sys.stderr.getvalue()
    finally:
        sys.stderr = saved
    check("usable marks print no warning", err, "")


# ----- item text -----------------------------------------------------------------

def test_code_chips():
    check("a backticked span is code, escaped, with no pattern applied inside",
          render.code_chips("run `a<b> & !12 #3 ABC-1 x.py`"), "run <code>a&lt;b&gt; &amp; !12 #3 ABC-1 x.py</code>")
    for name, text, want in (("a pull request id", "see !123 now", "see <code>!123</code> now"),
                             ("a hash number", "see #12 now", "see <code>#12</code> now"),
                             ("a ticket key", "fix ABC-123 today", "fix <code>ABC-123</code> today"),
                             ("a release branch", "on release/x now", "on <code>release/x</code> now"),
                             ("a feature branch", "on feature/x now", "on <code>feature/x</code> now"),
                             ("a bugfix branch", "on bugfix/x now", "on <code>bugfix/x</code> now"),
                             ("a hotfix branch", "on hotfix/x now", "on <code>hotfix/x</code> now"),
                             ("a multi-part ticket key", "fix ABC-1-2 today", "fix <code>ABC-1-2</code> today"),
                             ("a script with a flag", "run foo.py --flag now", "run <code>foo.py --flag</code> now")):
        check("%s outside backticks is code" % name, render.code_chips(text), want)
    for name, text in (("an id glued to a word", "a!12"), ("a key after a slash", "x/ABC-1"),
                       ("a key running into a word", "ABC-1x"), ("a lowercase key", "abc-1")):
        check("%s stays plain" % name, render.code_chips(text), text)
    check("text outside is escaped", render.code_chips('a < b & "c"'), "a &lt; b &amp; &quot;c&quot;")
    check("escaped text beside a span", render.code_chips("x<y `z` !7"), "x&lt;y <code>z</code> <code>!7</code>")


def test_item_body():
    check("detail renders as its own line, escaped and chipped",
          render.item_body(_item("action", "do it", detail="<why> !5")),
          '<div class="t">do it</div><div class="d">&lt;why&gt; <code>!5</code></div>')
    check("absent detail renders nothing", render.item_body(_item("action", "do it")), '<div class="t">do it</div>')
    check("blank detail renders nothing", render.item_body(_item("action", "do it", detail="  ")), '<div class="t">do it</div>')
    check("the where-line follows", render.item_body(_item("action", "do it"), "<W>"), '<div class="t">do it</div><W>')


# ----- what happened -------------------------------------------------------------

_ICON = r'<svg class="i"[^>]*>(?:(?!</svg>).)*</svg>'


def _cards(text):
    """Every card of items in a stretch of the page, as (classes, content) in page order."""
    return re.findall(r'<section class="card col ([^"]+)"(?: data-kind="[^"]*" data-count="\d+")?>(.*?)</section>', text)


def _lanes_of(panel):
    """The Still-open and the What-happened lane of a repository or session panel."""
    start = panel.index('<p class="lanetitle">[still_open]</p>')
    split = panel.index('<p class="lanetitle">[what_happened]</p>')
    return panel[start:split], panel[split:]


def test_happened_cards():
    body = dict(_body(done=["done 1"]), decided=["decided 1", "decided 2"], found=["found 1"])
    page = render.page(_report({"s1": body}), _digest())
    for panel_id in ("p-r-repo-a", "p-s-s1"):
        _, happened = _lanes_of(_panel(page, panel_id))
        heads = [(cls, re.match(r'<h3 class="kindhead"><span class="kind">(%s)\[(\w+)\]</span> (\d+)<button type="button" class="maxbtn grow"' % _ICON,
                                content).groups())
                 for cls, content in _cards(happened)]
        check("on %s decided, done, found, each with its open twin's icon" % panel_id, heads,
              [("twin decision", (render.icon("decision"), "decided", "2")), ("twin action", (render.icon("action"), "done", "1")),
               ("twin question", (render.icon("question"), "found", "1"))])
    body = dict(_body(done=[]), decided=["decided 1"], found=["found 1"])
    _, happened = _lanes_of(_panel(render.page(_report({"s1": body}), _digest()), "p-s-s1"))
    check("a list with no lines gets no card", [cls for cls, _ in _cards(happened)], ["twin decision", "twin question"])
    _, happened = _lanes_of(_panel(render.page(_report({"s1": _body(done=["merged !12"])}), _digest()), "p-s-s1"))
    check("a line of what happened is chipped", '<div class="t">merged <code>!12</code></div>' in happened, True)


def _counts(overview):
    summary = re.search(r'<span class="counts">(.*?)</span></summary>', overview).group(1)
    return re.findall(r'<span class="kind (\w+)">%s\[(\w+)\] (\d+)</span>' % _ICON, summary)


def test_folded_overview_lane():
    digest = _digest([_group_of("repo-a", [_session("s1")]), _group_of("repo-b", [_session("s2", cwd=_folder("repo-b"))])])
    page = render.page(_report({"s1": _body(), "s2": dict(_body(), found=["the cause"])}), digest)
    overview = _panel(page, "p-")
    check("one line in one session folds the lane in", overview.count('<details class="folded">'), 1)
    check("its counts show only the kind with lines", _counts(overview), [("question", "found", "1")])
    folded = overview[overview.index('<details class="folded">'):]
    check("its cards are the lists with lines", [cls for cls, _ in _cards(folded)], ["twin question"])

    body = lambda decided, found: dict(_body(), decided=decided, found=found)
    page = render.page(_report({"s1": body(["a", "b"], []), "s2": body(["c"], ["d"])}), digest)
    # by hand: decided 2 + 1 over both sessions, found 1, done none
    check("counts sum over sessions, a kind with none left out", _counts(_panel(page, "p-")),
          [("decision", "decided", "3"), ("question", "found", "1")])


# ----- item anchors --------------------------------------------------------------

def test_item_anchors_and_links():
    s1 = dict(_body(open_items=[_item("action", "open a0"), _item("decision", "open d1"), _item("action", "open a2")],
                    done=["done 0", "done 1"]), decided=["decided 0"], found=["found 0"])
    s2 = _body(open_items=[_item("question", "open q0")], done=["done s2"])
    digest = _digest([_group_of("repo-a", [_session("s1"), _session("s2", first="11:00", last="12:00")])])
    page = render.page(_report({"s1": s1, "s2": s2}), digest)
    # n is the line's place in its own list, counted from 0 in each session
    want = {"s1": {"open a0": "i-s1-o0", "open d1": "i-s1-o1", "open a2": "i-s1-o2", "done 0": "i-s1-d0", "done 1": "i-s1-d1",
                   "decided 0": "i-s1-c0", "found 0": "i-s1-f0"},
            "s2": {"open q0": "i-s2-o0", "done s2": "i-s2-d0"}}
    linked_want = sorted((text, "s-" + sid, anchor) for sid in want for text, anchor in want[sid].items())
    for panel_id in ("p-", "p-r-repo-a"):
        panel = _panel(page, panel_id)
        linked = re.findall(r'<li data-go="([^"]*)" data-item="([^"]*)" tabindex="0"><div class="t">([^<]*)</div>', panel)
        check("on %s each item leads to its session and names its anchor" % panel_id,
              sorted((text, go, item) for go, item, text in linked), linked_want)
        check("on %s no item is a destination" % panel_id, "<li id=" in panel, False)
    for sid in want:
        panel = _panel(page, "p-s-" + sid)
        plain = re.findall(r'<li id="([^"]*)"><div class="t">([^<]*)</div>', panel)
        check("on the %s panel each item carries its anchor as its id" % sid, {text: anchor for anchor, text in plain}, want[sid])
        check("on the %s panel the anchors are unique" % sid, len(set(anchor for anchor, _ in plain)), len(plain))
        check("on the %s panel no item leads anywhere" % sid, re.search(r"<li[^>]*data-go", panel) is None, True)
    ids = re.findall(r' id="([^"]+)"', page)
    check("every id on the page is unique", len(set(ids)), len(ids))
    targets = set(re.findall(r'data-item="([^"]+)"', page))
    check("every link points at an id on the page", targets <= set(ids), True)


# ----- lanes below the overview --------------------------------------------------

def _empty(label):
    return r'<div class="card empty">%s\[%s\]</div>' % (_ICON, label)


def test_lanes_below_the_overview():
    sessions = [_session("s1"), _session("s2", first="11:00", last="12:00")]
    report = _report({"s1": _body(open_items=_many("action", 2), done=["shipped"]), "s2": _body()})
    page = render.page(report, _digest([_group_of("repo-a", sessions)]))
    for panel_id in ("p-r-repo-a", "p-s-s1"):
        panel = _panel(page, panel_id)
        still, happened = _lanes_of(panel)
        check("on %s only the kind with items gets a card" % panel_id, [cls for cls, _ in _cards(still)], ["action"])
        check("on %s only the list with lines gets a card" % panel_id, [cls for cls, _ in _cards(happened)], ["twin action"])
        check("on %s no card clamps" % panel_id, "clamp" in panel, False)
    still, happened = _lanes_of(_panel(page, "p-s-s2"))
    check("an empty Still-open lane says nothing is open", re.fullmatch(r'<p class="lanetitle">\[still_open\]</p>' + _empty("nothing_open"), still) is not None, True)
    check("an empty What-happened lane says nothing happened",
          re.match(r'<p class="lanetitle">\[what_happened\]</p>' + _empty("nothing_happened"), happened) is not None, True)


def test_levels_fold_past_limit():
    body = _body(open_items=_many("action", 12), done=["done line %02d" % n for n in range(1, 12)])
    page = render.page(_report({"s1": body}), _digest())
    for panel_id, attribute in (("p-r-repo-a", "data-go="), ("p-s-s1", "id=")):
        cards = dict(_cards(_panel(page, panel_id)))
        action, done = cards["action"], cards["twin action"]
        check("on %s ten actions visible" % panel_id, action.count("<li " + attribute), 10)
        # newest first, so the first two the model listed are the ones folded
        check("on %s the first two actions folded" % panel_id,
              re.findall(r'<li class="more" hidden %s[^>]*><div class="t">([^<]*)</div>' % attribute, action), ["action item 02", "action item 01"])
        check("on %s the actions' button counts all" % panel_id,
              '<button type="button" class="btn ghost show-all" data-max-kind="action">[show_all] (12)</button>' in action, True)
        check("on %s ten done lines visible" % panel_id, done.count("<li " + attribute), 10)
        check("on %s the first done line folded" % panel_id,
              re.findall(r'<li class="more" hidden %s[^>]*><div class="t">([^<]*)</div>' % attribute, done), ["done line 01"])
        check("on %s the done button counts all" % panel_id,
              '<button type="button" class="btn ghost show-all" data-max-kind="done">[show_all] (11)</button>' in done, True)
    overview = _panel(page, "p-")
    folded = dict(_cards(overview[overview.index('<details class="folded">'):]))
    # by hand: 11 done lines, three shown for the repository and 11 - 3 = 8 beyond
    check("the overview's folded done card shows the repository's first three", folded["twin action"].count('<li data-go='), 3)
    check("the overview's folded done card keeps the rest beyond", folded["twin action"].count('<li class="beyond" data-go='), 8)
    check("the overview's folded done card links to the rest",
          '<button type="button" class="btn quiet more-link" data-go="r-repo-a">[more_in_repository 8 repo-a]</button>' in folded["twin action"], True)


def test_folded_links_land_on_folded_items():
    # a link to an item past a card's first ones lands on an item folded on its session panel too,
    # and the page opens it by maximising the card by its data-kind, so each folded target must sit in one card
    # whose own maximise button names that kind
    page = render.page(_report({"s1": _body(open_items=_many("action", 12))}), _digest())
    session = _panel(page, "p-s-s1")
    folded_ids = re.findall(r'<li class="more" hidden id="([^"]+)">', session)
    check("the session panel folds the first two actions, newest first", folded_ids, ["i-s1-o1", "i-s1-o0"])
    links = re.findall(r'<li class="more" [^>]*data-item="([^"]+)"', _panel(page, "p-r-repo-a"))
    check("on p-r-repo-a each folded link targets a folded item on the session panel", links, folded_ids)
    overview = _panel(page, "p-")
    check("on p- no item is folded", '<li class="more"' in overview, False)
    beyond = re.findall(r'<li class="beyond" [^>]*data-item="([^"]+)"', overview)
    check("on p- the folded targets are among the items past the repository's first three", set(folded_ids) <= set(beyond), True)
    kinded = re.findall(r'<section class="card col [^"]+" data-kind="([^"]*)" data-count="\d+">(.*?)</section>', session)
    for anchor in folded_ids:
        holders = [(kind, content) for kind, content in kinded if '<li class="more" hidden id="%s">' % anchor in content]
        check("%s sits in one card" % anchor, len(holders), 1)
        check("%s shares its card with a maximise button of the card's kind" % anchor,
              [content.count('<button type="button" class="maxbtn grow" data-max-kind="%s"' % kind) for kind, content in holders], [1])


# ----- panel keys ----------------------------------------------------------------

def test_slugs():
    groups = [{"path": os.path.join(_ROOT, "z", "REPO")}, {"path": os.path.join(_ROOT, "x", "repo")},
              {"path": os.path.join(_ROOT, "y", "Repo")}, {"path": _folder("My  Repo!x")}, {"path": _folder("@@")}]
    got = render.slugs(groups)
    check("lower-cased, a non-word run as one dash", got[_folder("My  Repo!x")], "my-repo-x")
    check("nothing left is repository", got[_folder("@@")], "repository")
    check("one name in three folders numbered in path order",
          [got[os.path.join(_ROOT, name, folder)] for name, folder in (("x", "repo"), ("y", "Repo"), ("z", "REPO"))],
          ["repo", "repo-2", "repo-3"])


def _path_line(path):
    return '<div class="path">%s</div>' % html.escape(path)


def test_panel_keys():
    # y comes first in the digest and, with more open, first in ordered(), but x comes first by path
    y, x = os.path.join(_ROOT, "y", "repo"), os.path.join(_ROOT, "x", "repo")
    digest = _digest([{"path": y, "current": False, "sessions": [_session("s1", cwd=y)]},
                      {"path": x, "current": False, "sessions": [_session("s2", cwd=x)]}])
    page = render.page(_report({"s1": _body(open_items=_many("action", 2)), "s2": _body()}), digest)
    check("the overview's key is empty", '<section class="panel" id="p-" data-key=""' in page, True)
    check("the first by path keeps the plain slug", _path_line(x) in _head(_panel(page, "p-r-repo")), True)
    check("the second by path is numbered, whatever ordered() gives", _path_line(y) in _head(_panel(page, "p-r-repo-2")), True)
    check("a repository panel is keyed r-<slug>", '<section class="panel" id="p-r-repo-2" data-key="r-repo-2"' in page, True)
    check("a session panel is keyed s-<id>", '<section class="panel" id="p-s-s1" data-key="s-s1"' in page, True)


# ----- the repository header -----------------------------------------------------

def test_running_count_chip():
    sessions = [_session("s1", running="running"), _session("s2", first="11:00", last="12:00", running="running"),
                _session("s3", first="13:00", last="14:00")]
    page = render.page(_report({sid: _body() for sid in ("s1", "s2", "s3")}), _digest([_group_of("repo-a", sessions)]))
    head = _head(_panel(page, "p-r-repo-a"))
    check("the head counts the running sessions", '<span class="chip live"><i class="dot"></i>[running_count 2]</span>' in head, True)
    check("one running chip", head.count('class="chip live"'), 1)
    check("no chip when none runs", 'class="chip live"' in _head(_panel(render.page(_report(), _digest()), "p-r-repo-a")), False)


# ----- the summary leaves out what happened --------------------------------------

def test_summary_excludes_what_happened():
    body = dict(_body(open_items=[_item("action", "still to do")], done=["done line"]), decided=["decided line"], found=["found line"])
    got = render.summary(_report({"s1": body}), _digest(), "x.html")
    for line in ("done line", "decided line", "found line"):
        check("%s is not in the summary" % line, line in got, False)
    check("the open item is", "  - [action]: still to do" in got.splitlines(), True)


# ----- filled labels -------------------------------------------------------------

def test_fill_and_length():
    check("fill by name in any order", render.fill("{b} then {a}", a=1, b=2), "2 then 1")
    labels = _labels()
    check("an hour or more uses duration", render.length(_session("s1", first="09:00", last="11:25"), labels), "[duration 2 25]")
    check("exactly an hour uses duration", render.length(_session("s1", first="09:00", last="10:00"), labels), "[duration 1 0]")
    check("under an hour uses duration_minutes", render.length(_session("s1", first="09:00", last="09:59"), labels), "[duration_minutes 59]")
    labels["duration"] = "{minutes}m/{hours}h"
    check("duration filled in its own word order", render.length(_session("s1", first="09:00", last="11:25"), labels), "25m/2h")


def test_session_position():
    # the later session is listed first, so only an order by start time numbers the earlier one 1
    sessions = [_session("late", first="11:00", last="11:30"), _session("early", first="09:00", last="10:00")]
    page = render.page(_report({"late": _body(), "early": _body()}), _digest([_group_of("repo-a", sessions)]))
    for sid, position, span in (("early", "[session_position 1 2]", "[duration 1 0]"), ("late", "[session_position 2 2]", "[duration_minutes 30]")):
        head = _head(_panel(page, "p-s-" + sid))
        eyebrow = re.search(r'<span class="eyebrow">%s(.*?)</span>' % _ICON, head).group(1)
        check("the %s session's eyebrow shows its position by start time" % sid, eyebrow, "[level_session] \u00b7 " + position)
        check("the %s session's head shows its length" % sid, ('<span class="span">%s</span>' % span) in head, True)


# ----- sidebar badge -------------------------------------------------------------

def test_badge():
    for number, want in ((0, "0"), (99, "99"), (100, "99+"), (1234, "99+")):
        check("badge(%d)" % number, render.badge(number), want)
    page = render.page(_report({"s1": _body(open_items=_many("action", 100))}), _digest())
    side = page[page.index('<aside class="card side">'):page.index("</aside>")]
    check("the sidebar's counts read 99+", re.findall(r'<span class="badge(?: small)?">([^<]*)</span>', side), ["99+", "99+", "99+"])


# ----- a range of days -----------------------------------------------------------

_FROM, _TO = "2026-10-02", "2026-10-08"
_SPAN = "%s \u2013 %s" % (_FROM, _TO)


def _range_digest():
    """A week with sessions on four days: 10-02 (Fri), 10-05 (Mon) running into 10-06, and 10-08 (Thu) in a second repository.
    10-03 to 10-04 and 10-07 have none, so they fold into two gaps."""
    sessions_a = [_session("a1", first="2026-10-02 09:15", last="2026-10-02 10:00"),
                  _session("a2", first="2026-10-05 08:40", last="2026-10-06 11:20")]
    sessions_b = [_session("b1", first="2026-10-08 14:00", last="2026-10-08 14:05", cwd=_folder("repo-b"))]
    return _digest([_group_of("repo-a", sessions_a), _group_of("repo-b", sessions_b)], **{"from": _FROM, "to": _TO})


def _range_report():
    return _report({"a1": _body(), "a2": _body(), "b1": _body()}, **{"from": _FROM, "to": _TO})


def _in_view(axis, fn):
    """Run fn under one view: a range's when an axis is given, one day's otherwise, and put the view back afterwards."""
    saved = dict(render.VIEW)
    render.VIEW["range"], render.VIEW["axis"] = axis is not None, axis
    try:
        return fn()
    finally:
        render.VIEW.update(saved)


def _markup(page):
    """The page without its style and script, whose CSS and JavaScript name the range's classes and attributes on every page."""
    page = re.sub(r"<style>.*?</style>", "", page, flags=re.S)
    return re.sub(r"<script>.*?</script>", "", page, flags=re.S)


def _timeline_of(panel):
    start = panel.index('<section class="card timeline"')
    return panel[start:panel.index("</section>", start)]


def test_range_report_fields():
    check("a range report matching its digest is valid", render.problems_in(_range_report(), _range_digest()), [])
    check("a leftover day key is an unknown field",
          render.problems_in(_report(day=_DAY), _digest()), ["unknown top-level field: day"])


def test_single_and_span_text():
    check("from equal to to is one day", render.single(_digest()), True)
    check("from before to is a range", render.single(_range_digest()), False)
    check("one day reads as its date", render.span_text(_digest()), "2026-10-05")
    check("a range reads as both ends around a spaced en dash", render.span_text(_range_digest()), "2026-10-02 \u2013 2026-10-08")


def test_clock_and_minutes():
    # by hand: 9 * 60 + 30
    check("clock is minutes past midnight", render.clock("2026-10-05 09:30"), 570)
    check("clock ignores the date", render.clock("2026-10-06 09:30"), 570)
    # by hand: 10 minutes to midnight plus 10 past it
    check("minutes subtract across midnight", render.minutes("2026-10-06 00:10") - render.minutes("2026-10-05 23:50"), 20)
    check("minutes subtract across a month's end", render.minutes("2026-10-01 00:00") - render.minutes("2026-09-30 23:59"), 1)
    check("minutes subtract across whole days", render.minutes("2026-10-08 09:00") - render.minutes("2026-10-05 09:00"), 3 * 1440)


def test_shown_and_stamp_html():
    before = dict(render.VIEW)
    one_day = _in_view(None, lambda: (render.shown("2026-10-05 09:00"), render.stamp_html("2026-10-05 09:00")))
    check("one day shows the time alone", one_day[0], "09:00")
    check("one day's page text is the time, with no time element", one_day[1], "09:00")
    axis = render.day_axis(_range_digest())
    on_range = _in_view(axis, lambda: (render.shown("2026-10-05 09:00"), render.stamp_html("2026-10-05 09:00")))
    check("a range shows the month and day before the time", on_range[0], "10-05 09:00")
    check("a range's page text carries the stamp in a time element", on_range[1],
          '<time datetime="2026-10-05T09:00">10-05 09:00</time>')
    check("the view is put back", render.VIEW, before)


def test_length_across_days():
    labels = _labels()
    check("a session ending on a later day has no length",
          render.length(_session("x", first="2026-10-05 23:00", last="2026-10-06 00:30"), labels), "")
    check("a session within one day still has its length",
          render.length(_session("x", first="2026-10-02 09:15", last="2026-10-02 10:00"), labels), "[duration_minutes 45]")
    page = render.page(_range_report(), _range_digest())
    check("on a range page a same-day session's head shows its length",
          '<span class="span">[duration_minutes 45]</span>' in _head(_panel(page, "p-s-a1")), True)
    check("on a range page a cross-day session's head shows an empty span",
          '<span class="span"></span>' in _head(_panel(page, "p-s-a2")), True)


def _shape(axis):
    """Each column as its day or "gap", its weight and whether it is a Monday, the parts a hand derivation can name exactly."""
    return [("gap" if column.get("gap") else column["day"], column["weight"], column.get("monday")) for column in axis["columns"]]


def test_day_axis():
    axis = render.day_axis(_range_digest())
    # by hand: 10-02 day, 10-03 and 10-04 fold into one gap, 10-05 Monday, 10-06 day, 10-07 gap, 10-08 day
    check("one column per day with sessions, a run without folded into one gap", _shape(axis),
          [("2026-10-02", 1.0, False), ("gap", 0.28, None), ("2026-10-05", 1.0, True),
           ("2026-10-06", 1.0, False), ("gap", 0.28, None), ("2026-10-08", 1.0, False)])
    # by hand: weights sum to 4 + 2 * 0.28 = 4.56, so a day is 100 / 4.56 = 21.930 wide and a gap 28 / 4.56 = 6.140,
    # and the left edges run 0, 1, 1.28, 2.28, 3.28 and 3.56 over 4.56
    check("left edges in percent", [round(column["left"], 3) for column in axis["columns"]],
          [0.0, 21.93, 28.07, 50.0, 71.93, 78.07])
    check("widths in percent", [round(column["width"], 3) for column in axis["columns"]],
          [21.93, 6.14, 21.93, 21.93, 6.14, 21.93])
    check("widths sum to 100", round(sum(column["width"] for column in axis["columns"]), 9), 100.0)
    check("each column starts where the last ended",
          [round(left["left"] + left["width"] - right["left"], 9) for left, right in zip(axis["columns"], axis["columns"][1:])], [0.0] * 5)
    check("the index holds the day columns only", sorted(axis["index"]), ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-08"])
    check("the index points at the columns themselves",
          all(axis["index"][column["day"]] is column for column in axis["columns"] if not column.get("gap")), True)
    # by hand: the clocks are 09:15, 10:00, 08:40, 11:20, 14:00 and 14:05, so 08:00 down to 15:00 up
    check("low is the earliest clock's hour", axis["low"], 8 * 60)
    check("high is the hour after the latest clock", axis["high"], 15 * 60)


def test_day_axis_reads_days():
    # resumed on 10-04 with nothing on 10-03 or 10-05, so its days name a day between its first and last
    session = _session("x", first="2026-10-02 09:00", last="2026-10-06 10:00", days=["2026-10-02", "2026-10-04", "2026-10-06"])
    digest = _digest([_group_of("repo-a", [session])], **{"from": "2026-10-01", "to": "2026-10-07"})
    # by hand: 10-05 is a Monday without a session, so it is a gap and no column is marked
    check("the columns follow the days a session names, gaps at both ends", _shape(render.day_axis(digest)),
          [("gap", 0.28, None), ("2026-10-02", 1.0, False), ("gap", 0.28, None), ("2026-10-04", 1.0, False),
           ("gap", 0.28, None), ("2026-10-06", 1.0, False), ("gap", 0.28, None)])

    long_run = _digest([_group_of("repo-a", [_session("x", first="2026-10-01 09:00", last="2026-10-10 10:00")])],
                       **{"from": "2026-10-01", "to": "2026-10-10"})
    axis = render.day_axis(long_run)
    check("eight days without a session are one gap of the same weight", _shape(axis),
          [("2026-10-01", 1.0, False), ("gap", 0.28, None), ("2026-10-10", 1.0, False)])
    # by hand: 100 / 2.28 = 43.860 and 28 / 2.28 = 12.281
    check("the folded gap stays narrow", [round(column["width"], 3) for column in axis["columns"]], [43.86, 12.281, 43.86])

    instant = _digest([_group_of("repo-a", [_session("x", first="2026-10-05 09:00", last="2026-10-05 09:00")])],
                      **{"from": "2026-10-05", "to": "2026-10-06"})
    axis = render.day_axis(instant)
    check("an instant at 09:00 spans 09:00 to 10:00", (axis["low"], axis["high"]), (540, 600))


def _two_day_axis():
    """Two whole days of 50% each, 09:00 to 10:00, so a position reads off by hand."""
    digest = _digest([_group_of("repo-a", [_session("x", first="2026-10-05 09:00", last="2026-10-05 09:30"),
                                           _session("y", first="2026-10-06 09:15", last="2026-10-06 09:45")])],
                     **{"from": "2026-10-05", "to": "2026-10-06"})
    return render.day_axis(digest)


def test_position():
    axis = _two_day_axis()
    # by hand: pad is min(0.06, 3 / 500) = 0.006, so a day's inner track runs from 0.6% to 99.4% of its 50
    got = _in_view(axis, lambda: [round(render.position(stamp), 6) for stamp in
                                  ("2026-10-05 09:00", "2026-10-05 10:00", "2026-10-06 09:30", "2026-10-06 09:00")])
    check("a stamp lands inside its day's gutters", got, [0.3, 49.7, 75.0, 50.3])

    axis = render.day_axis(_range_digest())

    def ends():
        return [(column["left"], render.position(column["day"] + " 08:00"), render.position(column["day"] + " 15:00"), column["left"] + column["width"])
                for column in axis["columns"] if not column.get("gap")]
    check("the hour range's start and end stay strictly inside every day column",
          [left < start < end < right for left, start, end, right in _in_view(axis, ends)], [True] * 4)

    month = _digest([_group_of("repo-a", [_session("d%02d" % day, first="2026-09-%02d 09:00" % day, last="2026-09-%02d 17:00" % day)
                                          for day in range(1, 31)])], **{"from": "2026-09-01", "to": "2026-09-30"})
    axis = render.day_axis(month)
    column = axis["index"]["2026-09-15"]
    # by hand: a column of 100 / 30 = 3.333% would get 3 / 33.33 = 0.09, so the pad is capped at 0.06
    check("on a narrow column the gutter is capped at 6% of the column",
          round(_in_view(axis, lambda: render.position("2026-09-15 09:00")) - column["left"], 9), round(column["width"] * 0.06, 9))


def _row_ids(rows):
    return [[session["id"] for session in row["sessions"]] for row in rows]


def test_rows_for_on_a_range():
    filler = [_session("d%02d" % day, first="2026-09-%02d 09:00" % day, last="2026-09-%02d 17:00" % day, cwd=_folder("filler"))
              for day in range(1, 31)]
    near = [_session("n1", first="2026-09-15 09:00", last="2026-09-15 09:05"), _session("n2", first="2026-09-15 11:00", last="2026-09-15 11:05")]
    far = [_session("f1", first="2026-09-01 09:00", last="2026-09-01 09:05"), _session("f2", first="2026-09-30 09:00", last="2026-09-30 09:05")]
    month = _digest([_group_of("filler", filler), _group_of("near", near), _group_of("far", far)], **{"from": "2026-09-01", "to": "2026-09-30"})
    axis = render.day_axis(month)
    # by hand: a day is 3.333% wide with a 6% pad over 09:00 to 18:00, so a minute is 2.933 / 540 = 0.0054%,
    # n1 ends 0.027% after it starts and n2 starts 0.652% after n1, past n1's end and its 0.25 margin,
    # but n1 is drawn at least 0.9% wide, so with the margin it covers n2's start
    check("two short sessions two hours apart on a month get a row each",
          _row_ids(_in_view(axis, lambda: render.rows_for({"sessions": near}))), [["n1"], ["n2"]])
    check("the same two on one day's view share a row", _row_ids(_in_view(None, lambda: render.rows_for({"sessions": near}))), [["n1", "n2"]])
    check("sessions a month apart share a row", _row_ids(_in_view(axis, lambda: render.rows_for({"sessions": far}))), [["f1", "f2"]])

    def margin(start):
        group = [_session("m1", first="2026-10-05 09:00", last="2026-10-05 09:00"),
                 _session("m2", first="2026-10-05 " + start, last="2026-10-05 11:30")]
        digest = _digest([_group_of("repo-a", group), _group_of("repo-b", [_session("m3", first="2026-10-06 09:00", last="2026-10-06 09:30")])],
                         **{"from": "2026-10-05", "to": "2026-10-06"})
        axis = render.day_axis(digest)
        return _row_ids(_in_view(axis, lambda: render.rows_for({"sessions": group})))
    # by hand: two days of 50% over 09:00 to 12:00, pad 0.006, so a minute is 49.4 / 180 = 0.2744%,
    # m1 sits at 0.3% and with its 0.9% minimum and 0.25 margin reaches 1.45%,
    # m2 at 09:04 starts at 0.3 + 4 * 0.2744 = 1.398%, inside the margin only, and at 09:05 at 1.672%, past it
    check("a start inside the margin after a minimum-width bar takes a new row", margin("09:04"), [["m1"], ["m2"]])
    check("a start past the margin shares the row", margin("09:05"), [["m1", "m2"]])


def test_range_timeline_markup():
    page = render.page(_range_report(), _range_digest())
    timeline = _timeline_of(_panel(page, "p-"))
    ticks = re.search(r'<div class="ticks days" data-cuts="([^"]*)">(.*?)</div>', timeline)
    check("the range's ticks carry the column edges", ticks is not None, True)
    if ticks is None:
        return
    # by hand: the left edges after the first column, 1, 1.28, 2.28, 3.28 and 3.56 over 4.56, gaps included
    check("data-cuts lists every edge after the first", ticks.group(1), "21.930 28.070 50.000 71.930 78.070")
    # by hand: each day's middle, 0.5, 1.78, 2.78 and 4.06 over 4.56
    check("one date tick per day column, Monday marked",
          re.findall(r'<span class="dtick( mon)?" style="left:([0-9.]+)%"><time datetime="([^"]+)">([^<]+)</time></span>', ticks.group(2)),
          [("", "10.965", "2026-10-02", "10-02"), (" mon", "39.035", "2026-10-05", "10-05"),
           ("", "60.965", "2026-10-06", "10-06"), ("", "89.035", "2026-10-08", "10-08")])
    check("no hour ticks on a range", re.search(r">\d\d:00</span>", ticks.group(2)), None)
    tracks = re.findall(r'<div class="track" style="height:\d+px">(.*?)</div>', timeline)
    check("one track per repository", len(tracks), 2)
    for number, track in enumerate(tracks):
        check("track %d draws a line per day and a band per gap" % number,
              re.findall(r'<span class="(dayline(?: mon)?|gap)" style="([^"]*)"></span>', track),
              [("dayline", "left:0.000%"), ("gap", "left:21.930%;width:6.140%"), ("dayline mon", "left:28.070%"),
               ("dayline", "left:50.000%"), ("gap", "left:71.930%;width:6.140%"), ("dayline", "left:78.070%")])
    bars = dict((sid, (label, width)) for sid, label, width in
                re.findall(r'<button type="button" class="bar[^"]*" data-go="s-(\w+)" aria-label="([^"]*)" style="left:[0-9.]+%;width:([0-9.]+)%', timeline))
    check("a session spanning days is one bar", timeline.count('data-go="s-a2"'), 1)
    check("bars are labelled in plain text, dates included", {sid: label for sid, (label, _) in bars.items()},
          {"a1": "repo-a 10-02 09:15-10-02 10:00", "a2": "repo-a 10-05 08:40-10-06 11:20", "b1": "repo-b 10-08 14:00-10-08 14:05"})
    # by hand: five minutes of a 21.93% day over seven hours is 21.93 * 0.973 * 5 / 420 = 0.25%, under the minimum
    check("a short bar is drawn at the minimum width", bars.get("b1", (None, None))[1], "0.90")


def test_one_day_timeline_markup():
    markup = _markup(render.page(_report(), _digest()))
    for literal in ("<time", "data-cuts", "dayline", 'class="gap"', "dtick"):
        check("a one-day page has no %s" % literal, literal in markup, False)
    # by hand: s1 runs 09:00 to 10:00, so the axis is 09:00 to 11:00 and each hour is half its width
    check("a one-day page has hour ticks",
          '<div class="ticks"><span style="left:0.00%">09:00</span><span style="left:50.00%">10:00</span>'
          '<span style="left:100.00%">11:00</span></div>' in markup, True)


def _sidebar_child(page, sid):
    return re.search(r'<button type="button" class="nav child" data-go="s-%s"(?: data-p="\d+"(?: hidden)?)? title="([^"]*)">(.*?)</button>' % sid, page)


def test_range_text_and_attributes():
    digest = _range_digest()
    page = render.page(_range_report(), digest)
    a2 = '<time datetime="2026-10-05T08:40">10-05 08:40</time>'
    check("the session head's title is two time elements",
          '<h2>%s\u2013<time datetime="2026-10-06T11:20">10-06 11:20</time> <span class="span"></span></h2>' % a2
          in _head(_panel(page, "p-s-a2")), True)
    child = _sidebar_child(page, "a2")
    check("the sidebar child is found", child is not None, True)
    if child is not None:
        check("the sidebar child's title attribute is plain", child.group(1), "10-05 08:40 topic of the session")
        check("the sidebar child's text carries a time element",
              child.group(2), '<span class="nm"><span class="at">%s</span>topic of the session</span>' % a2)
    trail = re.search(r'<div class="trail-of" data-for="s-a2"[^>]*>(.*?)</div>', page).group(1)
    check("the trail's session step carries a time element",
          trail.endswith('<span class="here"><span class="at">%s</span>topic of the session</span></nav>' % a2), True)
    # by hand: the earliest first is a1's 10-02 09:15 and the latest last b1's 10-08 14:05
    check("the overview timeline heading carries two time elements",
          '<h2>[timeline] <span><time datetime="2026-10-02T09:15">10-02 09:15</time>\u2013'
          '<time datetime="2026-10-08T14:05">10-08 14:05</time></span></h2>' in _timeline_of(_panel(page, "p-")), True)
    check("the session panel's data-title is plain",
          re.search(r'<section class="panel" id="p-s-a2" [^>]*data-title="([^"]*)"', page).group(1),
          "repo-a 10-05 08:40 \u00b7 remind-me " + _SPAN)
    # a2 ends 10-06 11:20, after a1's 10-02 10:00, so it is the session the shortcut resumes
    titles = re.findall(r'<a class="btn" href="[^"]*" title="([^"]*)">', _head(_panel(page, "p-r-repo-a")))
    check("the repository's resume-last title is plain",
          titles, [html.escape("%s 10-05 08:40-10-06 11:20" % _folder("repo-a"), quote=True)])
    attributes = re.findall(r' (?:title|aria-label|data-title)="([^"]*)"', page)
    check("no attribute on the page carries a time element", [value for value in attributes if "&lt;time" in value], [])


def test_range_headings():
    page = render.page(_range_report(), _range_digest())
    check("the sidebar names the range", ('<div class="day">%s</div>' % _SPAN) in page, True)
    eyebrow = re.search(r'<span class="eyebrow">%s(.*?)</span>' % _ICON, _panel(page, "p-")).group(1)
    check("the overview eyebrow names the range", eyebrow, "[level_range] \u00b7 " + _SPAN)
    check("the page title names the range", ("<title>remind-me %s</title>" % _SPAN) in page, True)


def test_one_day_heading():
    page = render.page(_report(), _digest())
    eyebrow = re.search(r'<span class="eyebrow">%s(.*?)</span>' % _ICON, _panel(page, "p-")).group(1)
    check("the overview eyebrow of one day names the day", eyebrow, "[level_day] \u00b7 2026-10-05")


def test_level_range_required():
    report = _report()
    del report["labels"]["level_range"]
    # a one-day report never shows level_range, yet the schema still requires it
    check("a one-day report without level_range names it", render.problems_in(report, _digest()),
          ["labels missing: level_range"])


_SCHEMA = os.path.join(_HERE, "..", "report-schema.md")


def _schema_label_keys(text):
    """The keys of the label table, one per row, in the order the schema lists them."""
    section = text[text.index("\n## Labels\n"):]
    end = section.find("\n## ", 1)
    if end >= 0:
        section = section[:end]
    return re.findall(r"^\| `(\w+)` \|", section, re.MULTILINE)


def test_schema_lists_every_label():
    # the model learns the labels only from this table, and the renderer refuses a report missing any of them
    with open(_SCHEMA, encoding="utf-8") as f:
        keys = _schema_label_keys(f.read())
    check("the schema's label table holds exactly the renderer's labels", sorted(keys), sorted(render.LABELS))
    check("no label is listed twice", sorted(key for key in set(keys) if keys.count(key) > 1), [])


def test_summary_on_a_range():
    out = os.path.join(tempfile.mkdtemp(), "report.html")
    report = _report({"a1": _body(open_items=[_item("action", "a1 first"), _item("question", "a1 second")]),
                      "a2": _body(open_items=[_item("decision", "a2 first"), _item("action", "a2 second")]),
                      "b1": _body(open_items=[_item("question", "b1 only")])}, **{"from": _FROM, "to": _TO})
    # the view left by a one-day page, so only summary() setting it itself gives the range's form
    got = _in_view(None, lambda: render.summary(report, _range_digest(), out))
    lines = got.splitlines()
    # by hand: three sessions of 10 + 5 charged each, in two repositories
    check("the figures line opens with the range",
          lines[2], "%s \u00b7 3 [sessions] \u00b7 2 [repositories] \u00b7 45 [tokens]" % _SPAN)
    # by hand: repo-a has four open and comes first; a2 ended 10-06 after a1's 10-02, and within a session the last listed comes first,
    # so its newest three are a2 second, a2 first and a1 second, with 4 - 3 = 1 left; repo-b's one ended 10-08
    check("each repository lists its newest three open items with their session's last day, then the rest as a count",
          lines[3:-4], ["", "### repo-a", "- [action]: a2 second (10-06)", "- [decision]: a2 first (10-06)", "- [question]: a1 second (10-02)",
                        "- [more_in_repository 1 repo-a]", "", "### repo-b", "- [question]: b1 only (10-08)"])
    quiet = _in_view(None, lambda: render.summary(_range_report(), _range_digest(), out))
    check("a repository with nothing open is left out", [line for line in quiet.splitlines() if line.startswith("### ")], [])


def test_summary_on_a_range_ends_with_the_link():
    out = os.path.join(tempfile.mkdtemp(), "report.html")
    report = _report({"a1": _body(open_items=[_item("action", "a1 first")]), "a2": _body(), "b1": _body()}, **{"from": _FROM, "to": _TO})
    got = _in_view(None, lambda: render.summary(report, _range_digest(), out))
    # by hand: the range's one open item, then the same closing a one-day summary has
    check("ends with the last item, the page URI, then the language hint", got.splitlines()[-5:],
          ["- [action]: a1 first (10-02)", "", pathlib.Path(out).resolve().as_uri(), "", "_[language_hint]_"])
    check("the URI is a file URI", got.splitlines()[-3].startswith("file:///"), True)
    # a range with nothing open still closes the same way
    quiet = _in_view(None, lambda: render.summary(_range_report(), _range_digest(), out))
    check("with nothing open it still ends with the page URI then the language hint", quiet.splitlines()[-3:],
          [pathlib.Path(out).resolve().as_uri(), "", "_[language_hint]_"])


def test_view_does_not_leak():
    render.page(_range_report(), _range_digest())
    markup = _markup(render.page(_report(), _digest()))
    check("a one-day page right after a range has no time element", "<time" in markup, False)
    check("a one-day page right after a range has hour ticks", '<div class="ticks"><span style="left:0.00%">09:00</span>' in markup, True)
    check("a one-day page right after a range has no day lines", "dayline" in markup, False)
    check("its sidebar child shows the time alone", _sidebar_child(markup, "s1").group(1), "09:00 topic of the session")

    render.summary(_range_report(), _range_digest(), "x.html")
    got = render.summary(_report(), _digest(), "x.html")
    check("a one-day summary right after a range shows times alone",
          [line for line in got.splitlines() if line.startswith("- ")], ["- **09:00\u201310:00** topic of the session"])
    check("its figures line opens with the day", got.splitlines()[2].startswith("2026-10-05 \u00b7 "), True)

    render.page(_range_report(), _range_digest())
    got = render.summary(_report(), _digest(), "x.html")
    check("a one-day summary right after a range page shows times alone",
          [line for line in got.splitlines() if line.startswith("- ")], ["- **09:00\u201310:00** topic of the session"])


# ----- fragments folded into the report ------------------------------------------

def test_merged_without_fragments():
    report = _report()
    for name, fragments in (("an empty list", []), ("None", None)):
        got, problems = render.merged(report, fragments)
        check("with %s of fragments the report itself comes back" % name, got is report, True)
        check("with %s of fragments nothing is a problem" % name, problems, [])
    for name, value in (("a list", []), ("a string", "day"), ("null", None)):
        got, problems = render.merged(value, [("repo-a.json", {"sessions": {"s1": _body()}})])
        check("a report that is %s comes back as it is" % name, got is value, True)
        check("a report that is %s gives no fragment problem" % name, problems, [])


def test_merged_folds_each_fragment():
    report = _report({"s1": _body(topics=["one"])})
    before = copy.deepcopy(report)
    fragments = [("repo-a.json", {"sessions": {"s2": _body(topics=["two"])}}),
                 ("repo-b.json", {"sessions": {"s3": _body(topics=["three"])}})]
    got, problems = render.merged(report, fragments)
    check("well-formed fragments carry no problem", problems, [])
    check("the report's own sessions are kept and each fragment's folded in after them",
          list(got["sessions"].items()), [("s1", _body(topics=["one"])), ("s2", _body(topics=["two"])), ("s3", _body(topics=["three"]))])
    check("every other field is the report's",
          {key: value for key, value in got.items() if key != "sessions"}, {key: value for key, value in before.items() if key != "sessions"})
    check("the report passed in is not changed", report, before)
    # the owner's ruling: with fragments given, a report without a sessions object takes the fragments' sessions alone
    for name, value in (("absent", None), ("a list", ["s1"]), ("a string", "s1")):
        report = _report()
        if value is None:
            del report["sessions"]
        else:
            report["sessions"] = value
        got, problems = render.merged(report, [("repo-a.json", {"sessions": {"s1": _body()}})])
        check("a report whose sessions is %s takes the fragments' sessions" % name, got["sessions"], {"s1": _body()})
        check("a report whose sessions is %s gives no fragment problem" % name, problems, [])


def test_merged_refuses_a_malformed_fragment():
    want = ["fragment bad.json must be an object with a sessions object"]
    good = ("good.json", {"sessions": {"s2": _body()}})
    for name, fragment in (("a list", []), ("a string", "x"), ("null", None), ("an object without sessions", {}),
                           ("an object whose sessions is a list", {"sessions": ["s2"]}),
                           ("an object whose sessions is null", {"sessions": None})):
        got, problems = render.merged(_report(), [("bad.json", fragment), good])
        check("a fragment that is %s is named" % name, problems, want)
        check("a fragment that is %s is skipped and the next still folded in" % name, sorted(got["sessions"]), ["s1", "s2"])
    # with no sessions object to read, its other fields are not named on top
    _, problems = render.merged(_report(), [("bad.json", {"sessions": [], "headline": "x"})])
    check("a field beside a sessions that is not an object is not named too", problems, want)


def test_merged_names_unknown_fragment_fields():
    fragment = {"sessions": {"s2": _body()}, "labels": {}, "headline": "from the fragment"}
    got, problems = render.merged(_report(), [("repo-b.json", fragment)])
    check("each extra field is named, in the fragment's order", problems,
          ["fragment repo-b.json: unknown field labels", "fragment repo-b.json: unknown field headline"])
    check("its sessions are still folded in", sorted(got["sessions"]), ["s1", "s2"])
    check("an extra field does not reach the report", got["headline"], "the headline")


def test_merged_refuses_a_session_twice():
    fragments = [("repo-a.json", {"sessions": {"s2": _body(topics=["from a"])}}),
                 ("repo-b.json", {"sessions": {"s2": _body(topics=["from b"])}})]
    _, problems = render.merged(_report({}), fragments)
    check("one id in two fragments is named", problems, ["session s2 is in more than one fragment"])
    _, problems = render.merged(_report({"s1": _body()}), [("repo-a.json", {"sessions": {"s1": _body()}})])
    check("a fragment id already in the report is named", problems, ["session s1 is in more than one fragment"])


def _fragment_files(fragments):
    """Each fragment written as JSON into a folder of its own, so a line naming one shows whether the name is the file's basename."""
    folder = os.path.join(tempfile.mkdtemp(), "readers")
    os.makedirs(folder)
    paths = []
    for name, fragment in fragments:
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fragment, f)
        paths.append(path)
    return paths


def _report_file(out_path):
    """The report file _main wrote beside the page."""
    return os.path.join(os.path.dirname(out_path), "report.json")


def test_main_folds_fragments():
    digest = _digest([_group_of("repo-a", [_session("s1")]), _group_of("repo-b", [_session("s2", cwd=_folder("repo-b"))])])
    one = _body(topics=["from repo-a"], open_items=[_item("action", "do a")])
    two = _body(topics=["from repo-b"], done=["did b"])
    paths = _fragment_files([("repo-a.json", {"sessions": {"s1": one}}), ("repo-b.json", {"sessions": {"s2": two}})])
    code, out, out_path = _main(_report({}), digest, ["--fragments"] + paths)
    whole = _report({"s1": one, "s2": two})
    check("exits 0", code, 0)
    check("page written", os.path.isfile(out_path), True)
    with open(out_path, encoding="utf-8") as f:
        written = f.read()
    check("the page is the one built from the report holding every session", written, render.page(whole, digest))
    check("stdout is that report's summary", out, render.summary(whole, digest, out_path) + "\n")
    # read as bytes, so a CRLF on any platform would show
    with open(_report_file(out_path), "rb") as f:
        raw = f.read()
    check("the report file now holds every session", json.loads(raw.decode("utf-8")), whole)
    check("it is JSON with an indent of one and LF line ends", raw.decode("utf-8"), json.dumps(whole, indent=1))
    check("no CRLF in it", b"\r\n" in raw, False)


def test_main_refuses_bad_fragments():
    report = _report({})
    paths = _fragment_files([("bad.json", []), ("repo-a.json", {"sessions": {"zz": _body()}, "headline": "x"})])
    code, out, out_path = _main(report, _digest(), ["--fragments"] + paths)
    check("exits 2", code, render.EXIT_SCHEMA)
    check("the fragments' problems come first, each named by its file's basename, then the report's", out.splitlines(),
          [SCHEMA_HEADER, "- fragment bad.json must be an object with a sessions object",
           "- fragment repo-a.json: unknown field headline",
           "- session zz is not in the digest",
           "- session s1 from the digest has no entry in the report"])
    check("no page written", os.path.exists(out_path), False)
    with open(_report_file(out_path), encoding="utf-8") as f:
        check("the report file is left as it was", f.read(), json.dumps(report))


def test_main_leaves_the_report_without_fragments():
    report = _report()
    code, _, out_path = _main(report, _digest())
    check("exits 0", code, 0)
    with open(_report_file(out_path), encoding="utf-8") as f:
        check("without --fragments the report file is not rewritten", f.read(), json.dumps(report))


# ----- the latest item on top ----------------------------------------------------

def test_newest_first():
    early = _session("e", first="08:00", last="10:00")
    late = _session("l", first="09:00", last="11:00")
    pairs = [("e0", None, early, 0), ("e1", None, early, 1), ("l0", None, late, 0), ("l1", None, late, 1)]
    check("the latest session first, and within a session the last the model listed first",
          [pair[0] for pair in render.newest_first(pairs)], ["l1", "l0", "e1", "e0"])
    long_one = _session("g", first="07:00", last="12:00")
    short = _session("s", first="09:00", last="10:00")
    check("by the session's last, not its first",
          [pair[0] for pair in render.newest_first([("s0", None, short, 0), ("g0", None, long_one, 0)])], ["g0", "s0"])
    # by clock alone 23:00 would come before 08:00, so only the date in minutes() puts the next morning first
    night = _session("n", first="2026-10-05 22:00", last="2026-10-05 23:00")
    morning = _session("m", first="2026-10-06 07:00", last="2026-10-06 08:00")
    check("a later day comes first whatever the clock",
          [pair[0] for pair in render.newest_first([("n0", None, night, 0), ("m0", None, morning, 0)])], ["m0", "n0"])
    a = _session("a", first="09:00", last="10:00")
    b = _session("b", first="09:30", last="10:00")
    # by hand: the keys are (minutes of 10:00, id, index), so reversed b1, b0, a1, a0
    check("two sessions ending in one minute keep each session's items together",
          [pair[0] for pair in render.newest_first([("a0", None, a, 0), ("b0", None, b, 0), ("a1", None, a, 1), ("b1", None, b, 1)])],
          ["b1", "b0", "a1", "a0"])


def test_dated():
    session = _session("x", first="2026-10-05 09:00", last="2026-10-06 11:20")
    where = '<div class="where">W</div>'
    check("on one day the where-line is unchanged", _in_view(None, lambda: render.dated(where, session)), where)
    check("on one day an empty where-line stays empty", _in_view(None, lambda: render.dated("", session)), "")
    axis = render.day_axis(_range_digest())
    chip = '<span class="date"><time datetime="2026-10-06">10-06</time></span>'
    check("on a range an empty where-line becomes the day of the session's last",
          _in_view(axis, lambda: render.dated("", session)), '<div class="where">%s</div>' % chip)
    check("on a range the day and a separator open a where-line",
          _in_view(axis, lambda: render.dated(where, session)), '<div class="where">%s<span class="sep">\u00b7</span>W</div>' % chip)


def _linked(text):
    """Each item that leads to its session in a stretch of the page, as (session, data-day, text, what follows the text), in page order."""
    return re.findall(r'<li(?: class="[^"]*")?(?: hidden)? data-go="s-(\w+)" data-item="[^"]*"(?: data-day="([^"]*)")? tabindex="0">'
                      r'<div class="t">([^<]*)</div>(.*?)</li>', text)


def _day_chip(day):
    return '<div class="where"><span class="date"><time datetime="%s">%s</time></span></div>' % (day, day[5:])


def test_range_items_newest_first_with_their_day():
    report = _report({"a1": _body(open_items=[_item("action", "a1 x")]),
                      "a2": _body(open_items=[_item("action", "a2 x"), _item("action", "a2 y")], done=["a2 done"]),
                      "b1": _body(open_items=[_item("action", "b1 x")])}, **{"from": _FROM, "to": _TO})
    page = render.page(report, _range_digest())
    # by hand: a2 ran 10-05 to 10-06 and a1 on 10-02, so a2's items lead repo-a, the last listed first, each dated by its session's last day
    a2_day, a1_day, b1_day = "2026-10-06", "2026-10-02", "2026-10-08"
    check("the overview lists each repository's items newest first, each with its day",
          _linked(_kind_section(page, "action")),
          [("a2", a2_day, "a2 y", _day_chip(a2_day)), ("a2", a2_day, "a2 x", _day_chip(a2_day)),
           ("a1", a1_day, "a1 x", _day_chip(a1_day)), ("b1", b1_day, "b1 x", _day_chip(b1_day))])
    cards = dict(_cards(_panel(page, "p-r-repo-a")))
    check("the repository panel lists its items newest first, each with its day", _linked(cards["action"]),
          [("a2", a2_day, "a2 y", _day_chip(a2_day)), ("a2", a2_day, "a2 x", _day_chip(a2_day)), ("a1", a1_day, "a1 x", _day_chip(a1_day))])
    check("a line of what happened on the repository panel carries its day too", _linked(cards["twin action"]),
          [("a2", a2_day, "a2 done", _day_chip(a2_day))])
    session = _panel(page, "p-s-a2")
    check("the session panel lists its items newest first, with no day",
          re.findall(r'<li id="([^"]*)"><div class="t">([^<]*)</div>(.*?)</li>', session),
          [("i-a2-o1", "a2 y", ""), ("i-a2-o0", "a2 x", ""), ("i-a2-d0", "a2 done", "")])
    check("the session panel carries no date chip", 'class="date"' in session, False)
    check("the session panel carries no data-day", "data-day" in session, False)

    one_day = _markup(render.page(_report({"s1": _body(open_items=[_item("action", "x")], done=["y"])}), _digest()))
    check("a one-day page carries no date chip", 'class="date"' in one_day, False)
    check("a one-day page carries no data-day", "data-day" in one_day, False)


def test_range_page_has_no_day_headings():
    report = _report({"a1": _body(open_items=[_item("action", "a1 x")]),
                      "a2": _body(open_items=[_item("action", "a2 x")], done=["a2 done"]),
                      "b1": _body(open_items=[_item("question", "b1 x")])}, **{"from": _FROM, "to": _TO})
    page = render.page(report, _range_digest())
    # the script makes the headings when a card is maximised, so the name is on the page, just not in its markup
    check("the script names the day heading", "dayhead" in re.search(r"<script>(.*?)</script>", page, re.S).group(1), True)
    check("the markup carries no day heading", "dayhead" in _markup(page), False)


# ----- the overview grouped by repository ----------------------------------------

def _blocks(card):
    """Each repository block of an overview card, as (tile, name, count, its items, what follows them)."""
    return re.findall(r'<div class="group"><div class="ghead">(<span class="tile[^"]*" aria-hidden="true">[^<]*</span>)<span class="nm">([^<]*)</span>'
                      r'<b>(\d+)</b></div><ul class="items">(.*?)</ul>(.*?)</div>', card)


def _texts(items, beyond=False):
    return re.findall(r'<li%s data-go=[^>]*><div class="t">([^<]*)</div>' % (' class="beyond"' if beyond else ""), items)


def test_overview_groups_by_repository():
    # listed in the digest against the order ordered() gives, so blocks in ordered() order cannot come from the digest's
    digest = _digest([_group_of("gamma", [_session("g1", cwd=_folder("gamma"))]),
                      _group_of("beta", [_session("b1", cwd=_folder("beta"))]),
                      _group_of("Alpha Repo", [_session("a1", cwd=_folder("Alpha Repo"))])])
    report = _report({"a1": _body(open_items=_many("action", 5)),
                      "b1": _body(open_items=_many("action", 2) + _many("question", 1), done=["beta done"]),
                      "g1": _body(done=["gamma done %d" % n for n in range(1, 5)])})
    page = render.page(report, digest)

    action = _kind_section(page, "action")
    blocks = _blocks(action)
    # by hand: Alpha Repo has five open, beta three and gamma none, and gamma has no action
    check("a block per repository with actions, in ordered() order, headed by its tile, name and count",
          [(tile, name, count) for tile, name, count, _, _ in blocks], [(_tile("A"), "Alpha Repo", "5"), (_tile("B"), "beta", "2")])
    if len(blocks) == 2:
        check("the first three of a repository are plain, newest first", _texts(blocks[0][3]), ["action item 05", "action item 04", "action item 03"])
        check("the rest are beyond", _texts(blocks[0][3], beyond=True), ["action item 02", "action item 01"])
        # the slug leads the link and the name fills the label, so a name with a space tells them apart
        check("a link to the rest names their count and the repository",
              blocks[0][4], '<button type="button" class="btn quiet more-link" data-go="r-alpha-repo">[more_in_repository 2 Alpha Repo]</button>')
        check("a repository with three or fewer has nothing beyond", "beyond" in blocks[1][3], False)
        check("and no link to the rest", blocks[1][4], "")
    # by hand: 5 + 2
    check("the card counts every repository's items",
          action.startswith('<section class="card col action clamp" data-kind="action" data-count="7"><h3 class="kindhead">'), True)
    check("the heading counts them too", '[action]</span> 7<button type="button" class="maxbtn grow"' in action, True)
    check("the question card has a block for beta alone",
          [(name, count) for _, name, count, _, _ in _blocks(_kind_section(page, "question"))], [("beta", "1")])
    decision = _kind_section(page, "decision")
    check("a kind with no items has no block", "ghead" in decision, False)
    check("and an empty list in their place", '<div class="groups"><ul class="items"></ul></div>' in decision, True)

    overview = _panel(page, "p-")
    done = dict(_cards(overview[overview.index('<details class="folded">'):]))["twin action"]
    blocks = _blocks(done)
    check("the folded lane's done card is grouped the same way, in ordered() order",
          [(tile, name, count) for tile, name, count, _, _ in blocks], [(_tile("B"), "beta", "1"), (_tile("G"), "gamma", "4")])
    if len(blocks) == 2:
        check("its first three of gamma are plain, newest first", _texts(blocks[1][3]), ["gamma done 4", "gamma done 3", "gamma done 2"])
        check("its rest of gamma are beyond", _texts(blocks[1][3], beyond=True), ["gamma done 1"])
        check("its link to the rest of gamma",
              blocks[1][4], '<button type="button" class="btn quiet more-link" data-go="r-gamma">[more_in_repository 1 gamma]</button>')
    # by hand: 1 + 4
    check("the folded done card's heading counts every repository's lines", '[done]</span> 5<button type="button" class="maxbtn grow"' in done, True)
    check("the folded done card's count is the total",
          '<section class="card col twin action" data-kind="done" data-count="5">' in overview, True)


# ----- the sidebar's pages -------------------------------------------------------

def _side_box(count, labels=None):
    """The sidebar's session list of a repository holding count sessions, as its attributes, its children, its step rows and its markup.
    The sessions start a minute apart and sit in the digest out of order, and the first to start ends last,
    so only an order by start time puts it at the bottom."""
    sessions = [_session("s%02d" % n, first="09:%02d" % n, last="11:00" if n == 0 else "09:%02d" % n) for n in range(count)]
    # 7 shares no factor with any count used here, so this only reorders
    shuffled = sorted(sessions, key=lambda session: int(session["id"][1:]) * 7 % count)
    report = _report({session["id"]: _body() for session in sessions}, labels=labels or _labels())
    page = render.page(report, _digest([_group_of("repo-a", shuffled)]))
    box = re.search(r'<div class="children" data-children="repo-a"([^>]*)>(.*?)</div>', page)
    attributes = dict(re.findall(r' (data-[\w-]+)="([^"]*)"', box.group(1)))
    children = re.findall(r'<button type="button" class="nav child" data-go="s-(\w+)" data-p="(\d+)"( hidden)? title=', box.group(2))
    steps = re.findall(r'<button type="button" class="step-row" data-sstep="(-?1)"( hidden)?>%s<span>([^<]*)</span></button>' % _ICON, box.group(2))
    return attributes, children, steps, box.group(2)


def test_sidebar_one_page():
    check("a page holds at most eight", render.SIDE_PAGE, 8)
    attributes, children, steps, markup = _side_box(8)
    check("eight sessions are one page of eight, its attributes there all the same", attributes,
          {"data-pages": "1", "data-size": "8", "data-total": "8", "data-newer": "[newer {n}]", "data-older": "[older {n}]"})
    check("every child on page 0, none hidden, newest first by start time", children, [("s%02d" % n, "0", "") for n in range(7, -1, -1)])
    check("no step rows", steps, [])
    check("no step-row at all", "step-row" in markup, False)


def test_sidebar_pages():
    attributes, children, steps, _ = _side_box(9)
    # by hand: 9 over 8 is 2 pages, and 9 over 2 rounded up is 5 a page
    check("nine sessions are two pages of five", (attributes["data-pages"], attributes["data-size"], attributes["data-total"]), ("2", "5", "9"))
    check("five on page 0, four on page 1", [page for _, page, _ in children], ["0"] * 5 + ["1"] * 4)
    check("the rows to turn the page, the newer one hidden, the older one counting the 4 left", steps, [("-1", " hidden", ""), ("1", "", "[older 4]")])

    labels = _labels()
    labels["newer"] = 'newer & "{n}"'
    labels["older"] = "<older {n}>"
    attributes, children, steps, markup = _side_box(26, labels)
    # by hand: 26 over 8 is 4 pages, and 26 over 4 rounded up is 7 a page, so 7, 7, 7 and 26 - 21 = 5
    check("twenty-six sessions are four pages of seven, the labels escaped", attributes,
          {"data-pages": "4", "data-size": "7", "data-total": "26", "data-newer": "newer &amp; &quot;{n}&quot;", "data-older": "&lt;older {n}&gt;"})
    check("children newest first by start time", [sid for sid, _, _ in children], ["s%02d" % n for n in range(25, -1, -1)])
    check("pages of 7, 7, 7 and 5", [page for _, page, _ in children], ["0"] * 7 + ["1"] * 7 + ["2"] * 7 + ["3"] * 5)
    check("children past the first page hidden", [hidden for _, _, hidden in children], [""] * 7 + [" hidden"] * 19)
    # by hand: 26 - 7 = 19 older than the first page
    check("the rows to turn the page", steps, [("-1", " hidden", ""), ("1", "", "&lt;older 19&gt;")])
    check("the newer row is above the children", markup.startswith('<button type="button" class="step-row" data-sstep="-1" hidden>'), True)
    check("the older row is below them", markup.endswith('<span>&lt;older 19&gt;</span></button>'), True)


# ----- labels for the overview's links, the side pages and the card buttons -----

def test_paging_and_maximise_labels():
    for label, value, lost in (("more_in_repository", "{n} more", "{repository}"), ("more_in_repository", "more in {repository}", "{n}"),
                               ("more_in_repository", "more", "{n}, {repository}"), ("newer", "newer", "{n}"), ("older", "older", "{n}")):
        labels = _labels()
        labels[label] = value
        check("label %s as %r names %s" % (label, value, lost), render.problems_in(_report(labels=labels), _digest()),
              ["label %s must keep %s" % (label, lost)])
    labels = _labels()
    del labels["maximise"]
    del labels["restore"]
    check("maximise and restore absent are named as missing", render.problems_in(_report(labels=labels), _digest()),
          ["labels missing: maximise, restore"])


# ----- card markup ---------------------------------------------------------------

def _card_heads(panel):
    """Each card of a panel as (data-kind, data-count, its heading's content), in page order."""
    return re.findall(r'<section class="card col [^"]+" data-kind="(\w+)" data-count="(\d+)"><h3 class="kindhead">(.*?)</h3>', panel)


def test_card_kinds_and_counts():
    body = dict(_body(open_items=_many("decision", 2) + _many("action", 1) + _many("question", 3), done=["d1", "d2"]),
                decided=["c1"], found=["f1", "f2", "f3", "f4"])
    page = render.page(_report({"s1": body}), _digest())
    want = [("decision", "2"), ("action", "1"), ("question", "3"), ("decided", "1"), ("done", "2"), ("found", "4")]
    for panel_id in ("p-", "p-r-repo-a", "p-s-s1"):
        check("on %s every card names its kind and count" % panel_id, [(kind, count) for kind, count, _ in _card_heads(_panel(page, panel_id))], want)
    check("no card on the page lacks them", page.count('<section class="card col'),
          len(re.findall(r'<section class="card col [^"]+" data-kind="\w+" data-count="\d+">', page)))


def test_card_heading_buttons():
    labels = _labels()
    labels["maximise"] = 'grow <"all">'
    labels["restore"] = "back & forth"
    body = dict(_body(open_items=[_item("decision", "x")], done=["d"]), found=["f"])
    page = render.page(_report({"s1": body}, labels=labels), _digest())
    grow, shrink = "grow &lt;&quot;all&quot;&gt;", "back &amp; forth"
    # the overview keeps a card for every open kind, the levels below only those with items
    for panel_id, kinds in (("p-", ["decision", "action", "question", "done", "found"]),
                            ("p-r-repo-a", ["decision", "done", "found"]), ("p-s-s1", ["decision", "done", "found"])):
        heads = _card_heads(_panel(page, panel_id))
        check("on %s the cards are the expected kinds" % panel_id, [kind for kind, _, _ in heads], kinds)
        for kind, _, head in heads:
            want = ('<button type="button" class="maxbtn grow" data-max-kind="%s" title="%s" aria-label="%s">%s</button>'
                    '<button type="button" class="maxbtn shrink" data-max-restore title="%s" aria-label="%s">%s</button>'
                    % (kind, grow, grow, render.icon("maximise"), shrink, shrink, render.icon("restore")))
            check("on %s the %s heading ends with maximise then restore, labels escaped" % (panel_id, kind), head.endswith(want), True)
    check("no raw maximise label", 'grow <"all">' in page, False)
    check("no raw restore label", "back & forth" in page, False)


_TESTS = (test_valid_report_has_no_problems, test_missing_top_level_fields, test_range_not_the_digests,
          test_missing_labels, test_session_not_in_digest, test_digest_session_without_entry,
          test_session_without_topics, test_open_item_kind_and_text,
          test_tokens, test_compact, test_compact_top_unit, test_figures, test_figures_on_page,
          test_windows_detection, test_resume_command, test_terminal_command, test_deep_link,
          test_rows_for, test_ordered,
          test_page_escapes_transcript_text, test_theme_labels_cannot_close_script,
          test_running_session_has_no_resume, test_repository_resume_last,
          test_current_repository_marked, test_overview_folds_past_limit, test_overview_empty,
          test_bars_carry_no_count,
          test_repository_state_unread, test_repository_live_chips, test_each_button_lives_in_one_place,
          test_session_meta,
          test_token_mix, test_token_mix_label_escaped, test_token_mix_zero_usage,
          test_summary, test_summary_repositories_in_order,
          test_main_refuses_invalid_report, test_main_refuses_wrong_types, test_main_writes_page, test_script_on_cp1252_console,
          test_placeholder_labels, test_unknown_fields, test_happened_lists_must_be_lines, test_topics_with_a_bad_line,
          test_detail_must_be_a_string, test_session_body_not_an_object, test_open_not_a_list_of_objects,
          test_shared_problem_reported_once, test_bad_marks_never_a_problem,
          test_report_not_an_object, test_labels_not_an_object, test_sessions_not_an_object,
          test_language_and_headline_must_be_text, test_label_not_a_string,
          test_emoji_like_accepts, test_emoji_like_rejects, test_same_path, test_marks_match_paths_loosely,
          test_marks_duplicates, test_marks_unusable, test_tile_is_the_same_everywhere, test_main_with_bad_marks,
          test_code_chips, test_item_body, test_happened_cards, test_folded_overview_lane,
          test_item_anchors_and_links, test_lanes_below_the_overview, test_levels_fold_past_limit,
          test_folded_links_land_on_folded_items,
          test_slugs, test_panel_keys, test_running_count_chip, test_summary_excludes_what_happened,
          test_fill_and_length, test_session_position, test_badge,
          test_range_report_fields, test_single_and_span_text, test_clock_and_minutes, test_shown_and_stamp_html,
          test_length_across_days, test_day_axis, test_day_axis_reads_days, test_position, test_rows_for_on_a_range,
          test_range_timeline_markup, test_one_day_timeline_markup, test_range_text_and_attributes, test_range_headings,
          test_one_day_heading, test_level_range_required, test_schema_lists_every_label,
          test_summary_on_a_range, test_summary_on_a_range_ends_with_the_link, test_view_does_not_leak,
          test_merged_without_fragments, test_merged_folds_each_fragment, test_merged_refuses_a_malformed_fragment,
          test_merged_names_unknown_fragment_fields, test_merged_refuses_a_session_twice, test_main_folds_fragments,
          test_main_refuses_bad_fragments, test_main_leaves_the_report_without_fragments, test_newest_first, test_dated,
          test_range_items_newest_first_with_their_day, test_range_page_has_no_day_headings, test_overview_groups_by_repository, test_sidebar_one_page,
          test_sidebar_pages, test_paging_and_maximise_labels, test_card_kinds_and_counts, test_card_heading_buttons)


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
