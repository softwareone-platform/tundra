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
    """One panel of the page, from its opening tag to the next panel or the end of the main element.
    Its id is "p-" for the overview, "p-r-<slug>" for a repository and "p-s-<id>" for a session."""
    start = page.index('<section class="panel" id="%s"' % panel_id)
    ends = [i for i in (page.find('<section class="panel"', start + 1), page.find("</main>", start)) if i >= 0]
    return page[start:min(ends)]


def _kind_section(page, kind):
    """The overview's card of open items of one kind, or None when the overview has none."""
    overview = _panel(page, "p-")
    # the twin cards of what happened carry "twin" before the kind, so they never match here
    found = re.search(r'<section class="card col %s( clamp)?">' % kind, overview)
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
    report = _report({"s1": _body(open_items=_many("action", 12) + _many("decision", 10) + _many("question", 3))})
    page = render.page(report, _digest())
    action = _kind_section(page, "action")
    check("the overview's action card clamps", action.startswith('<section class="card col action clamp">'), True)
    check("ten actions visible", action.count('<li data-go='), 10)
    check("two actions folded", action.count('<li class="more" hidden data-go='), 2)
    check("the folded ones are the last two",
          re.findall(r'<li class="more" hidden data-go=[^>]*><div class="t">(action item \d\d)</div>', action),
          ["action item 11", "action item 12"])
    check("the visible ones are the first ten in order",
          re.findall(r'<li data-go=[^>]*><div class="t">(action item \d\d)</div>', action),
          ["action item %02d" % n for n in range(1, 11)])
    check("one show-all button carrying the total", action.count("show-all"), 1)
    check("show-all names the total", '<button type="button" class="btn ghost show-all">[show_all] (12)</button>' in action, True)
    check("heading counts the total",
          re.search(r'<h3 class="kindhead"><span class="kind"><svg class="i"[^>]*>(?:(?!</svg>).)*</svg>\[action\]</span> 12</h3>',
                    action) is not None, True)
    decision = _kind_section(page, "decision")
    check("exactly at the limit nothing folds", "more" in decision, False)
    check("exactly at the limit no button", "show-all" in decision, False)
    check("under the limit no button", "show-all" in _kind_section(page, "question"), False)


def test_overview_empty():
    page = render.page(_report(), _digest())
    overview = _panel(page, "p-")
    # the overview keeps a card for every kind, so a quiet day still shows where open items would go
    for kind in render.KINDS:
        section = _kind_section(page, kind)
        check("a %s card with nothing open" % kind, section is not None, True)
        if section is None:
            continue
        check("the %s card counts zero" % kind, ("[%s]</span> 0</h3>" % kind) in section, True)
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
        check("%s in an item's where-line" % slug, ('<div class="where">%s%s</div>' % (tile, slug)) in overview, True)
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
    return re.findall(r'<section class="card col ([^"]+)">(.*?)</section>', text)


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
        heads = [(cls, re.match(r'<h3 class="kindhead"><span class="kind">(%s)\[(\w+)\]</span> (\d+)</h3>' % _ICON, content).groups())
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
        check("on %s the last two actions folded" % panel_id,
              re.findall(r'<li class="more" hidden %s[^>]*><div class="t">([^<]*)</div>' % attribute, action), ["action item 11", "action item 12"])
        check("on %s the actions' button counts all" % panel_id,
              '<button type="button" class="btn ghost show-all">[show_all] (12)</button>' in action, True)
        check("on %s ten done lines visible" % panel_id, done.count("<li " + attribute), 10)
        check("on %s the eleventh done line folded" % panel_id,
              re.findall(r'<li class="more" hidden %s[^>]*><div class="t">([^<]*)</div>' % attribute, done), ["done line 11"])
        check("on %s the done button counts all" % panel_id,
              '<button type="button" class="btn ghost show-all">[show_all] (11)</button>' in done, True)
    overview = _panel(page, "p-")
    folded = dict(_cards(overview[overview.index('<details class="folded">'):]))
    check("the overview's folded done card folds too", folded["twin action"].count('<li class="more" hidden data-go='), 1)


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


_TESTS = (test_valid_report_has_no_problems, test_missing_top_level_fields, test_day_not_the_digests,
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
          test_main_refuses_invalid_report, test_main_writes_page, test_script_on_cp1252_console,
          test_placeholder_labels, test_unknown_fields, test_happened_lists_must_be_lines, test_topics_with_a_bad_line,
          test_detail_must_be_a_string, test_session_body_not_an_object, test_open_not_a_list_of_objects,
          test_shared_problem_reported_once, test_bad_marks_never_a_problem,
          test_emoji_like_accepts, test_emoji_like_rejects, test_same_path, test_marks_match_paths_loosely,
          test_marks_duplicates, test_marks_unusable, test_tile_is_the_same_everywhere, test_main_with_bad_marks,
          test_code_chips, test_item_body, test_happened_cards, test_folded_overview_lane,
          test_item_anchors_and_links, test_lanes_below_the_overview, test_levels_fold_past_limit,
          test_slugs, test_panel_keys, test_running_count_chip, test_summary_excludes_what_happened,
          test_fill_and_length, test_session_position, test_badge)


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
