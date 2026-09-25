"""Deterministic self-check for the whoami report renderer.

The renderer is the last step before a person reads a report about themselves,
so every case here is a known-answer case over a synthetic report document,
built from one minimal valid document and varied one field at a time.
Each rule in the renderer is load-bearing: removing it turns at least one check red.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/render_report_tests.py
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile

# import the module under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _SCRIPTS)
import render_report as rr  # noqa: E402

_SCRIPT = os.path.join(_SCRIPTS, "render_report.py")

SCHEMA_HEADER = "the report does not match the schema, so nothing was written:\n"


# every check records (group, name, ok, detail),
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(name, got, want):
    ok = got == want
    _results.append((_group, name, ok, "" if ok else "got %r, want %r" % (got, want)))


# ----- synthetic documents -----------------------------------------------------

def _pattern(pid, **extra):
    """A valid pattern whose every visible value names its id, so a check can tell patterns apart."""
    p = {"id": pid, "name": "name " + pid, "description": "desc " + pid, "sources": ["code"],
         "confidence": {"level": "verified"},
         "instances": [{"text": "instance one of " + pid, "ref": "ref one of " + pid},
                       {"text": "instance two of " + pid, "ref": "ref two of " + pid}]}
    p.update(extra)
    return p


def _without(obj, key):
    obj = dict(obj)
    del obj[key]
    return obj


def _doc(**over):
    """The smallest document the schema accepts: a summary, one strength, and a scope."""
    doc = {"summary": {"text": "the summary"},
           "strengths": [_pattern("s1", exceptions=[])],
           "scope": {"repositories": "repo-a"}}
    doc.update(over)
    return doc


def _full_doc():
    """A document that fills every optional part, so each section of the page renders."""
    return {
        "language": "en",
        "title": "whoami: repo-a",
        "scope_line": "repo-a over one year",
        "summary": {"text": "first paragraph\n\nsecond paragraph",
                    "axes": [{"name": "axis one", "description": "axis description",
                              "strong": "strong side", "weak": "weak side", "patterns": ["s1", "g1"]}]},
        "diagrams": [{"caption": "a flow",
                      "lanes": [{"title": "lane one",
                                 "steps": [{"text": "step one", "detail": ["detail one", "detail two"],
                                            "tone": "strong", "note": "then"},
                                           {"text": "step two", "tone": "weak"}]},
                                {"title": "lane two", "steps": [{"text": "step three"}]}]}],
        "strengths": [_pattern("s1", exceptions=[{"text": "exception of s1", "ref": "exception ref"}],
                               calibration="3 of 10 elsewhere", checked="read every script")],
        "gaps": [_pattern("g1", confidence={"level": "depends", "on": "the load"})],
        "styles": [_pattern("y1", gives="speed", costs="rework")],
        "implications": [{"area": "reviews", "meaning": "ask early", "patterns": ["s1", "g1"]}],
        "scope": {"repositories": "repo-a", "periods": ["2026-01", "2026-02"]},
        "dissolved": [{"name": "dissolved one", "evidence": "refuted by counts"}],
        "events": [{"text": "event one", "ref": "event ref"}],
    }


def _all_labels(overrides):
    """Every label a document may give, English except the overrides, since validate takes every label or none.
    The title is left out, because it is the one label a document need not give."""
    labels = {k: v for k, v in rr.LABELS.items() if k != "title"}
    labels.update(overrides)
    return labels


def _html(doc):
    return rr.render_html(doc, rr.labels_for(doc))


def _md_lines(doc):
    return rr.render_markdown(doc, rr.labels_for(doc), "OUT").splitlines()


def _section(page, heading):
    """The page from a section's heading to the end of that section, or None when it is absent."""
    start = page.find("<h2>%s</h2>" % heading)
    if start < 0:
        return None
    return page[start:page.index("</section>", start)]


def _has_block(lines, block):
    n = len(block)
    return any(lines[i:i + n] == block for i in range(len(lines) - n + 1))


def _main(doc=None, raw=None, missing=False):
    """Run main in-process on a document written to a temp dir, with stdout and stderr captured.
    Both streams must be real text wrappers, because main reconfigures each of them to UTF-8.
    The output path sits in a directory that does not exist yet, which main has to create.
    With missing set, no report is written, so main is pointed at a path that does not exist."""
    d = tempfile.mkdtemp()
    report = os.path.join(d, "report.json")
    if not missing:
        with open(report, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(doc, ensure_ascii=False))
    out_path = os.path.join(d, "nested", "report.html")
    out_raw, err_raw = io.BytesIO(), io.BytesIO()
    # a wrapper translates each newline to os.linesep by default, which would make every exact check platform-bound
    out = io.TextIOWrapper(out_raw, encoding="cp1252", newline="\n")
    err = io.TextIOWrapper(err_raw, encoding="cp1252", newline="\n")
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = rr.main([report, "--out", out_path])
        out.flush()
        err.flush()
    finally:
        sys.stdout, sys.stderr = saved
    return code, out_raw.getvalue().decode("utf-8"), err_raw.getvalue().decode("utf-8"), out_path


# ----- a valid document renders --------------------------------------------------

def test_valid_document_renders():
    check("fixture: minimal document is valid", rr.validate(_doc()), [])
    check("fixture: full document is valid", rr.validate(_full_doc()), [])

    code, out, err, out_path = _main(_doc())
    check("exits 0", code, 0)
    check("stderr silent", err, "")
    check("HTML file written", os.path.isfile(out_path), True)
    with open(out_path, encoding="utf-8") as f:
        page = f.read()
    check("HTML file is a page", page.startswith("<!doctype html>"), True)
    check("HTML file holds the summary", "<p>the summary</p>" in page, True)
    # written by hand from the format: summary, one strength table, then the path of the page
    check("stdout is the Markdown summary ending with the HTML path", out, "\n".join([
        "## Summary", "", "the summary", "",
        "## Strengths", "",
        "| Pattern | Source | Confidence |",
        "|---|---|---|",
        "| name s1 | code | Verified |", "",
        "Full report: `" + os.path.abspath(out_path) + "`"]) + "\n")


# ----- validation: each problem on its own, and nothing written ------------------

def _invalid(name, doc, problem):
    code, out, err, out_path = _main(doc)
    check(name + ": exits 2", code, 2)
    check(name + ": the problem alone is named on stderr", err, SCHEMA_HEADER + "- " + problem + "\n")
    check(name + ": stdout silent", out, "")
    check(name + ": no file written", os.path.exists(out_path), False)


def test_validation_document():
    check("INVALID constant", rr.INVALID, 2)
    _invalid("missing summary", _without(_doc(), "summary"), "document: 'summary' is missing or empty")
    _invalid("missing summary.text", _doc(summary={}), "summary: 'text' is missing or empty")
    _invalid("empty summary.text", _doc(summary={"text": ""}), "summary: 'text' is missing or empty")
    _invalid("missing scope", _without(_doc(), "scope"), "document: 'scope' is missing or empty")
    _invalid("document not an object", [], "the document is not a JSON object")
    _invalid("group not a list", _doc(gaps={}), "'gaps' is not a list")


def test_validation_pattern_fields():
    for key in ("id", "name", "description", "sources", "confidence"):
        _invalid("pattern missing " + key, _doc(strengths=[_without(_pattern("s1", exceptions=[]), key)]),
                 "strengths[0]: '%s' is missing or empty" % key)
    _invalid("source outside the three", _doc(strengths=[_pattern("s1", exceptions=[], sources=["code", "chat"])]),
             "strengths[0]: source 'chat' is not one of code, prompts, instructions")
    _invalid("confidence level outside the two",
             _doc(strengths=[_pattern("s1", exceptions=[], confidence={"level": "likely"})]),
             "strengths[0]: confidence level must be one of verified, depends")
    _invalid("depends without on", _doc(strengths=[_pattern("s1", exceptions=[], confidence={"level": "depends"})]),
             "strengths[0]: a 'depends' confidence needs 'on'")


def test_validation_instances():
    _invalid("one instance", _doc(strengths=[_pattern("s1", exceptions=[], instances=[{"text": "only"}])]),
             "strengths[0]: a pattern needs at least two instances")
    _invalid("no instances key", _doc(strengths=[_without(_pattern("s1", exceptions=[]), "instances")]),
             "strengths[0]: a pattern needs at least two instances")
    # two is the boundary that passes
    check("two instances valid",
          rr.validate(_doc(strengths=[_pattern("s1", exceptions=[], instances=[{"text": "one"}, {"text": "two"}])])), [])


def test_validation_exceptions():
    _invalid("strength without exceptions", _doc(strengths=[_pattern("s1")]),
             "strengths[0]: a strength needs 'exceptions', empty only when none were found")
    check("strength with an empty exceptions list valid", rr.validate(_doc()), [])
    # only a strength has to say where it broke, so a gap without the key is fine
    check("gap without exceptions valid", rr.validate(_doc(gaps=[_pattern("g1")])), [])


def test_validation_styles():
    _invalid("style without gives", _doc(styles=[_pattern("y1", costs="rework")]),
             "styles[0]: 'gives' is missing or empty")
    _invalid("style without costs", _doc(styles=[_pattern("y1", gives="speed")]),
             "styles[0]: 'costs' is missing or empty")
    check("style with gives and costs valid", rr.validate(_doc(styles=[_pattern("y1", gives="g", costs="c")])), [])


def test_validation_references():
    _invalid("duplicate id across groups", _doc(gaps=[_pattern("s1")]), "gaps[0]: id 's1' is used twice")
    axis = {"name": "a", "strong": "s", "weak": "w", "patterns": ["nope"]}
    _invalid("axis references an unknown pattern", _doc(summary={"text": "t", "axes": [axis]}),
             "summary.axes[0]: pattern 'nope' does not exist")
    _invalid("implication references an unknown pattern",
             _doc(implications=[{"area": "a", "meaning": "m", "patterns": ["nope"]}]),
             "implications[0]: pattern 'nope' does not exist")
    # an id defined in a later group still counts for an axis
    check("axis referencing a gap valid",
          rr.validate(_doc(gaps=[_pattern("g1")], summary={"text": "t", "axes": [dict(axis, patterns=["g1"])]})), [])


def test_validation_shapes():
    # a model can emit a bare value where an object or a list belongs, and each must list a problem rather than raise
    _invalid("pattern that is a string", _doc(strengths=["a bare string"]),
             "strengths[0]: a pattern must be an object")
    _invalid("pattern that is a number", _doc(strengths=[_pattern("s1", exceptions=[])], gaps=[5]),
             "gaps[0]: a pattern must be an object")
    _invalid("axis that is not an object", _doc(summary={"text": "t", "axes": ["axis one"]}),
             "summary.axes[0]: an axis must be an object")
    _invalid("axes that is not a list", _doc(summary={"text": "t", "axes": "axis one"}),
             "summary: 'axes' is not a list")
    _invalid("implication that is not an object", _doc(implications=["reviews"]),
             "implications[0]: an implication must be an object")
    _invalid("implications that is not a list", _doc(implications={"area": "a", "meaning": "m"}),
             "'implications' is not a list")


def test_validation_lists_every_problem():
    doc = _doc(strengths=["a bare string"], gaps=[5], summary={"text": "t", "axes": [7]}, implications="reviews")
    code, out, err, out_path = _main(doc)
    check("exits 2", code, 2)
    # written by hand in the order the schema is walked: groups, then the summary, then the implications
    check("every problem listed together on stderr", err, SCHEMA_HEADER + "".join([
        "- strengths[0]: a pattern must be an object\n",
        "- gaps[0]: a pattern must be an object\n",
        "- summary.axes[0]: an axis must be an object\n",
        "- 'implications' is not a list\n"]))
    check("stdout silent", out, "")
    check("no file written", os.path.exists(out_path), False)


def test_invalid_json():
    code, out, err, out_path = _main(raw="{not json")
    check("exits 2", code, 2)
    check("stderr names invalid JSON", err.startswith("the report is not valid JSON: "), True)
    check("stdout silent", out, "")
    check("no file written", os.path.exists(out_path), False)


def test_missing_report():
    code, out, err, out_path = _main(missing=True)
    check("exits 2", code, 2)
    check("stderr names the unreadable report", err.startswith("the report could not be read: "), True)
    check("stderr is one line", err.count("\n"), 1)
    check("stdout silent", out, "")
    check("no file written", os.path.exists(out_path), False)


# ----- escaping ------------------------------------------------------------------

def _markup(field):
    return '<script>%s"</script>' % field


def _escaped(field):
    # written by hand: html.escape with quote=True turns < > and " into these entities
    return "&lt;script&gt;%s&quot;&lt;/script&gt;" % field


def test_escaping():
    m = _markup
    doc = _doc(
        title=m("title"),
        language=m("language"),
        scope_line=m("scope-line"),
        # every label the renderer knows is overridden,
        # so a label left raw at any site shows up as a raw tag on the page
        labels={key: m("label-" + key) for key in rr.LABELS},
        summary={"text": m("summary"), "axes": [{"name": m("axis"), "description": m("axis-description"),
                                                 "strong": m("strong"), "weak": m("weak"),
                                                 "patterns": ["s1", m("id")]}]},
        # a note renders only between two steps, so a second step follows the one that carries it
        diagrams=[{"caption": m("caption"),
                   "lanes": [{"title": m("lane-title"),
                              "steps": [{"text": m("step-text"), "detail": [m("detail")], "note": m("note")},
                                        {"text": "last"}]}]}],
        # all three sources, so the separator and every source label render
        strengths=[_pattern("s1", name=m("name"), description=m("description"), sources=list(rr.SOURCES),
                            instances=[{"text": m("instance"), "ref": m("ref")}, m("bare-instance")],
                            exceptions=[{"text": m("exception")}], calibration=m("calibration"), checked=m("checked"))],
        # an id is placed inside id="..." and href="#...", where a raw quote would end the attribute
        gaps=[_pattern(m("id"))],
        # a depends confidence, so the depends label renders next to the verified one
        styles=[_pattern("y1", gives=m("gives"), costs=m("costs"),
                         confidence={"level": "depends", "on": "the load"})],
        implications=[{"area": m("area"), "meaning": m("meaning"), "patterns": ["s1", m("id")]}],
        scope={m("scope-key"): m("scope-value"), "list": [m("scope-list")]},
        dissolved=[{"name": m("dissolved-name"), "evidence": m("dissolved-evidence")}],
        events=[{"text": m("event"), "ref": m("event-ref")}],
    )
    check("fixture: markup document is valid", rr.validate(doc), [])
    page = _html(doc)
    for field in ("title", "summary", "axis", "strong", "weak", "lane-title", "step-text", "detail", "note",
                  "caption", "name", "description",
                  "instance", "ref", "bare-instance", "exception", "calibration", "gives", "costs", "area",
                  "meaning", "scope-key", "scope-value", "scope-list", "dissolved-name", "dissolved-evidence",
                  "event", "event-ref"):
        check("%s escaped" % field, _escaped(field) in page, True)
    # a field escaped in one place and raw in another still leaves a raw tag somewhere on the page
    check("no raw script tag anywhere", "<script" in page, False)

    # each of these is asserted in the one place it renders,
    # because the same text escaped elsewhere on the page would hide a raw copy here
    check("axis description escaped", "<p>%s</p>" % _escaped("axis-description") in page, True)
    check("scope line escaped", '<p class="scopeline">%s</p>' % _escaped("scope-line") in page, True)
    check("checked escaped", "<h4>%s</h4><p>%s</p>" % (_escaped("label-checked"), _escaped("checked")) in page, True)
    for key in ("summary", "strengths", "implications", "scope", "appendix"):
        check("%s label heading escaped" % key, "<h2>%s</h2>" % _escaped("label-" + key) in page, True)
    check("pattern id escaped in its row", '<th scope="row" id="%s">' % _escaped("id") in page, True)
    check("pattern id escaped in its links", page.count('<a href="#%s">' % _escaped("id")), 2)
    check("language escaped", page.startswith('<!doctype html><html lang="%s">' % _escaped("language")), True)
    # the value runs to the first quote that closes the tag, so a raw quote inside it would show up here
    for name, pattern in (("id", r'<th scope="row" id="(.*?)">'), ("href", r'<a href="(.*?)">'),
                          ("lang", r'<html lang="(.*?)">')):
        values = re.findall(pattern, page)
        # an empty match list would pass the raw-quote check without having looked at anything
        check("a %s attribute found" % name, len(values) > 0, True)
        check("no raw quote inside a %s attribute" % name, [v for v in values if '"' in v], [])


# ----- the page loads nothing ----------------------------------------------------

def test_html_loads_nothing():
    url_target = r"""url\(\s*["']?\s*([^"')\s]+)"""

    # a data: URI carries its content inline, so only a url() pointing anywhere else fetches something
    def fetched(text):
        return [u for u in re.findall(url_target, text) if not u.startswith("data:")]

    # written by hand: each url() form a stylesheet accepts that points outside the page, then one that does not,
    # so the rule below is shown to catch a remote or relative target rather than pass everything
    for css, want in (("url(http://x/a.png)", ["http://x/a.png"]), ('url("https://x")', ["https://x"]),
                      ("url('//cdn/x')", ["//cdn/x"]), ("url(a.png)", ["a.png"]),
                      ('url( "data:image/svg+xml,%3Csvg" )', [])):
        check("url() target outside the page in %s" % css, fetched(css), want)

    for name, doc in (("minimal", _doc()), ("full", _full_doc())):
        page = _html(doc)
        check(name + ": no remote src or href", re.findall(r"""(?:src|href)\s*=\s*["']?\s*https?:""", page, re.I), [])
        check(name + ": no script element", "<script" in page.lower(), False)
        check(name + ": no link element", "<link" in page.lower(), False)
        # an inline handler runs script with no script element, so the check above alone would miss it
        check(name + ": no inline event handler", re.findall(r"\son[a-z]+\s*=", page, re.I), [])
        # an empty match list would pass the data: check without having looked at anything
        check(name + ": a url() value found", len(re.findall(url_target, page)) > 0, True)
        check(name + ": no CSS import, and no url() but a data: URI", ("@import" in page, fetched(page)), (False, []))


# ----- structure -----------------------------------------------------------------

def test_pattern_tables():
    page = _html(_full_doc())
    strengths, gaps, styles = _section(page, "Strengths"), _section(page, "Gaps"), _section(page, "Ways of working")
    check("each group has its own section", (strengths is None, gaps is None, styles is None), (False, False, False))
    check("strengths holds only its pattern", ("name s1" in strengths, "name g1" in strengths, "name y1" in strengths),
          (True, False, False))
    check("gaps holds only its pattern", ("name s1" in gaps, "name g1" in gaps, "name y1" in gaps), (False, True, False))
    check("styles holds only its pattern", ("name s1" in styles, "name g1" in styles, "name y1" in styles),
          (False, False, True))

    plain_head = "<thead><tr><th>Pattern</th><th>Description</th><th>Source</th><th>Confidence</th></tr></thead>"
    style_head = ("<thead><tr><th>Pattern</th><th>Description</th><th>What it gives</th><th>What it costs</th>"
                  "<th>Source</th><th>Confidence</th></tr></thead>")
    check("strengths head has no gives or costs", plain_head in strengths, True)
    check("gaps head has no gives or costs", plain_head in gaps, True)
    check("styles head has gives and costs", style_head in styles, True)
    check("gives and costs absent outside styles", ("What it gives" in strengths + gaps, "What it costs" in strengths + gaps),
          (False, False))
    check("styles row carries gives then costs", "<td>speed</td><td>rework</td>" in styles, True)


def test_confidence_labels():
    page = _html(_full_doc())
    check("verified renders the verified label",
          '<td class="conf"><span class="badge verified">Verified</span></td>' in _section(page, "Strengths"), True)
    check("depends renders its short label, carrying its constraint as a tooltip",
          '<td class="conf"><span class="badge depends" tabindex="0" title="Depends on: the load"'
          ' data-tip="Depends on: the load">Conditional</span></td>' in _section(page, "Gaps"), True)


def test_evidence_in_details():
    page = _html(_full_doc())
    strengths = _section(page, "Strengths")
    opened = strengths.find("<details><summary>Evidence</summary>")
    check("evidence opens a details element", opened >= 0, True)
    inside = strengths[opened:strengths.index("</details>", opened)]
    for text in ("instance one of s1", "ref one of s1", "instance two of s1", "exception of s1", "exception ref",
                 "3 of 10 elsewhere", "read every script"):
        check("%s inside the details" % text, text in inside, True)
        check("%s nowhere else" % text, page.count(text), 1)
    check("details follows the description in its cell", "<td>desc s1<details>" in strengths, True)


def test_appendix():
    page = _html(_full_doc())
    appendix = _section(page, "Appendix")
    head = "<h2>Appendix</h2><details><summary>Appendix</summary>"
    check("appendix opens with a details element", appendix is not None and appendix.startswith(head), True)
    inside = appendix[len(head):appendix.index("</details>")]
    for text in ("dissolved one", "refuted by counts", "event one", "event ref"):
        check("%s inside the appendix details" % text, text in inside, True)
        check("%s nowhere else" % text, page.count(text), 1)
    check("dissolved and events carry their labels",
          ("<h4>Candidates the analysis refuted</h4>" in inside, "<h4>Single events noticed along the way</h4>" in inside),
          (True, True))


def test_related_links():
    page = _html(_full_doc())
    # written by hand: each related pattern is an anchor to its row, joined by a line break
    links = '<a href="#s1">name s1</a><br><a href="#g1">name g1</a>'
    # written by hand: each side of the axis card lists its own related patterns, as anchors under a heading
    check("axis card lists each related pattern as a link inside its side",
          ('<div class="side strong"><b>Reliably gets right</b>strong side'
           '<div class="links"><h4>Related patterns</h4><a href="#s1">name s1</a></div></div>' in page,
           '<div class="side weak"><b>Reliably misses</b>weak side'
           '<div class="links"><h4>Related patterns</h4><a href="#g1">name g1</a></div></div>' in page),
          (True, True))
    check("implication row lists its related patterns as links",
          '<tr><th scope="row">reviews</th><td>ask early</td><td>%s</td></tr>' % links
          in _section(page, "What this means for your work"), True)
    check("each link target is a pattern row",
          ('<th scope="row" id="s1">name s1</th>' in page, '<th scope="row" id="g1">name g1</th>' in page), (True, True))
    # validate rejects an unknown reference in a whole document, so the link path is called directly
    names = rr.names_by_id(_full_doc())
    check("an unknown id is dropped from the links", rr.pattern_links(["s1", "nope", "g1"], names), links)
    check("only unknown ids render no links", rr.pattern_links(["nope"], names), "")


def test_summary_title_scope():
    page = _html(_full_doc())
    check("summary text split into a paragraph per blank line",
          "<h2>Summary</h2><p>first paragraph</p><p>second paragraph</p>" in page, True)
    check("document title in h1 and title",
          ("<h1>whoami: repo-a</h1>" in page, "<title>whoami: repo-a</title>" in page), (True, True))
    minimal = _html(_doc())
    check("no document title falls back to the title label",
          ("<h1>whoami</h1>" in minimal, "<title>whoami</title>" in minimal), (True, True))
    check("scope list values joined by a semicolon",
          '<tr><th scope="row">periods</th><td>2026-01; 2026-02</td></tr>' in _section(page, "Scope"), True)
    # written by hand: json.dumps with its default separators and the non-ASCII kept as is, then escaped
    doc = _doc(scope={"repositories": [{"name": "repo-a", "period": "\u7e41"}, "repo-b"]})
    check("a dict in a scope list rendered as JSON", _section(_html(doc), "Scope"),
          '<h2>Scope</h2><div class="tablewrap"><table class="scope"><tbody><tr><th scope="row">repositories</th>'
          '<td>{&quot;name&quot;: &quot;repo-a&quot;, &quot;period&quot;: &quot;\u7e41&quot;}; repo-b</td>'
          '</tr></tbody></table></div>')


def test_empty_groups_render_no_section():
    doc = _doc(gaps=[])
    page = _html(doc)
    check("strengths section present", "<h2>Strengths</h2>" in page, True)
    check("empty gaps renders no section", "<h2>Gaps</h2>" in page, False)
    check("absent styles renders no section", "<h2>Ways of working</h2>" in page, False)
    check("no implications renders no section", "<h2>What this means for your work</h2>" in page, False)
    check("no dissolved or events renders no appendix", "<h2>Appendix</h2>" in page, False)
    lines = _md_lines(doc)
    check("Markdown has no empty group heading", ("## Gaps" in lines, "## Ways of working" in lines), (False, False))


# ----- labels --------------------------------------------------------------------

def test_labels_for():
    lab = rr.labels_for({"labels": {"summary": "\u7d50\u8ad6"}})
    check("document label overrides English", lab["summary"], "\u7d50\u8ad6")
    check("label left out falls back to English", lab["gaps"], "Gaps")
    check("non-string label ignored", rr.labels_for({"labels": {"summary": 5}})["summary"], "Summary")
    check("null labels fall back to English", rr.labels_for({"labels": None}), rr.LABELS)
    check("no labels key falls back to English", rr.labels_for({}), rr.LABELS)
    check("defaults left untouched by an override", rr.LABELS["summary"], "Summary")


def test_labels_in_html():
    doc = _full_doc()
    doc["labels"] = {"strengths": "\u512a\u9ede", "verified": "\u5df2\u9a57\u8b49", "code": "\u7a0b\u5f0f"}
    page = _html(doc)
    check("overridden heading rendered", "<h2>\u512a\u9ede</h2>" in page, True)
    check("English heading replaced", "<h2>Strengths</h2>" in page, False)
    check("heading left out stays English", "<h2>Gaps</h2>" in page, True)
    check("overridden confidence label rendered", '<span class="badge verified">\u5df2\u9a57\u8b49</span>' in page, True)
    check("overridden source label rendered", '<td class="nowrap">\u7a0b\u5f0f</td>' in page, True)


def test_separator_joins_sources():
    sources = ["code", "prompts", "instructions"]
    default = _doc(strengths=[_pattern("s1", exceptions=[], sources=sources)])
    check("default separator in HTML", '<td class="nowrap">code, prompts, instructions</td>' in _html(default), True)
    check("default separator in Markdown", "| name s1 | code, prompts, instructions | Verified |" in _md_lines(default),
          True)
    custom = dict(default, labels={"separator": "\u3001"})
    check("custom separator in HTML",
          '<td class="nowrap">code\u3001prompts\u3001instructions</td>' in _html(custom), True)
    check("custom separator in Markdown",
          "| name s1 | code\u3001prompts\u3001instructions | Verified |" in _md_lines(custom), True)


# ----- Markdown ------------------------------------------------------------------

def test_markdown_cells():
    doc = _doc(strengths=[_pattern("s1", exceptions=[], name="a|b\ntwo")],
               implications=[{"area": "a|rea\nbreak", "meaning": "ask early", "patterns": ["s1"]}])
    lines = _md_lines(doc)
    check("pipe escaped and newline flattened in a pattern row", "| a\\|b two | code | Verified |" in lines, True)
    # an implication is a list line rather than a table row, so its pipe stays as typed,
    # while its line break is still flattened to keep the list item on one line
    check("pipe left raw and newline flattened in an implication line", "- **a|rea break** \u2014 ask early" in lines,
          True)
    check("no row split onto a second line", [ln for ln in lines if ln.startswith("two") or ln.startswith("break")], [])

    doc = _doc(strengths=[_pattern("s1", exceptions=[], name="a\\|b"),
                          _pattern("s2", exceptions=[], confidence={"level": "depends", "on": "C:\\dir"})],
               implications=[{"area": "C:\\dir|area", "meaning": "ask early", "patterns": ["s1"]}])
    check("fixture: backslash cell document is valid", rr.validate(doc), [])
    lines = _md_lines(doc)
    # the constraint is written in a line under the table, so the cell holds only the short label
    check("conditional cell holds only its label", "| name s2 | code | Conditional |" in lines, True)
    # written by hand: the pattern name, an em dash, then C, colon, two backslashes, dir
    check("backslash doubled in a condition line", "- name s2 \u2014 Depends on: C:\\\\dir" in lines, True)
    # the backslash is doubled before the pipe is escaped,
    # or the escape added for the pipe would itself be doubled and leave the pipe live.
    # written by hand: a, three backslashes, pipe, b
    check("backslash doubled before the pipe is escaped in a pattern row",
          "| a\\\\\\|b | code | Verified |" in lines, True)
    # the same characters in prose, where the backslash is doubled and the pipe left as typed.
    # written by hand: C, colon, two backslashes, dir, pipe, area, in bold, then the meaning
    check("backslash doubled and pipe left raw in an implication line",
          "- **C:\\\\dir|area** \u2014 ask early" in lines, True)


def test_markdown_tables():
    lines = _md_lines(_full_doc())
    # written by hand: the axis name in bold, then each side under its label, joined by an em dash
    check("axis lines with their labels", _has_block(lines, [
        "**axis one**", "- Reliably gets right \u2014 strong side", "- Reliably misses \u2014 weak side", ""]), True)
    for heading, row in (("Strengths", "| name s1 | code | Verified |"),
                         ("Gaps", "| name g1 | code | Conditional |")):
        check("%s table with its label" % heading, _has_block(lines, [
            "## " + heading, "", "| Pattern | Source | Confidence |", "|---|---|---|", row]), True)
    # a trade-off is long prose that would wrap a terminal table, so it is left to the HTML like a description
    check("Ways of working table with its label", _has_block(lines, [
        "## Ways of working", "",
        "| Pattern | Source | Confidence |",
        "|---|---|---|",
        "| name y1 | code | Verified |"]), True)
    # the meaning is what a reader needs, so each implication is its area and its meaning
    check("implications listed as area and meaning", _has_block(lines, [
        "## What this means for your work", "", "- **reviews** \u2014 ask early"]), True)
    implications = lines[lines.index("## What this means for your work"):lines.index("Full report: `OUT`")]
    # the related patterns are left to the HTML, so neither a name nor an id reaches these lines
    check("implications carry no pattern names or ids",
          [ln for ln in implications if any(s in ln for s in ("name s1", "name g1", "s1", "g1"))], [])
    check("scope line under its label", _has_block(lines, ["## Scope", "", "repo-a over one year"]), True)
    # written by hand: the caption in bold leads, then each lane under its italic title,
    # a numbered step per line with its details after an em dash, an arrow only where it carries a note,
    # and a blank line after each lane
    check("diagram listed lane by lane", _has_block(lines, [
        "**a flow**", "",
        "*lane one*",
        "1. step one \u2014 detail one; detail two",
        "   \u2193 then",
        "2. step two", "",
        "*lane two*",
        "1. step three", ""]), True)
    check("ends with the report path", lines[-1], "Full report: `OUT`")


def test_markdown_prose_backslashes():
    # every heading this document prints is overridden,
    # so a heading left without md_text shows up as a single backslash.
    # each value also holds a pipe, which md_cell would escape,
    # so a heading or prose line switched to the table-cell escape shows up too
    labels = _all_labels({key: "C:\\dir |" + key for key in ("summary", "strengths", "implications", "scope")})
    doc = _doc(summary={"text": "C:\\dir summary| text"}, scope_line="C:\\dir scope| line",
               implications=[{"area": "reviews", "meaning": "ask early", "patterns": ["s1"]}], labels=labels)
    check("fixture: prose backslash document is valid", rr.validate(doc), [])
    lines = _md_lines(doc)
    # written by hand: every backslash in prose comes out doubled, so Markdown shows it as one,
    # and every pipe stays as typed, because prose is not a table cell
    check("summary text doubles its backslash and keeps its pipe", "C:\\\\dir summary| text" in lines, True)
    check("scope line doubles its backslash and keeps its pipe", "C:\\\\dir scope| line" in lines, True)
    for key in ("summary", "strengths", "implications", "scope"):
        check("%s heading doubles its backslash and keeps its pipe" % key, "## C:\\\\dir |%s" % key in lines, True)


def test_markdown_report_path():
    # the path sits in a code span, where a backslash is already literal,
    # so doubling it there would print a path that does not exist
    doc = _doc()
    last = rr.render_markdown(doc, rr.labels_for(doc), "C:\\dir\\report.html").splitlines()[-1]
    # written by hand: the path exactly as passed, with single backslashes
    check("report path placed raw in its code span", last, "Full report: `C:\\dir\\report.html`")


def test_markdown_axis_lines():
    lines = _md_lines(_full_doc())
    # written by hand: the summary paragraphs, then each axis as a bold name and two list lines closed by a blank,
    # and only then the diagram's caption, so nothing else sits between the summary and the diagram
    check("axis block sits between the summary text and the diagram",
          lines[lines.index("## Summary"):lines.index("**a flow**")], [
        "## Summary", "",
        "first paragraph", "", "second paragraph", "",
        "**axis one**",
        "- Reliably gets right \u2014 strong side",
        "- Reliably misses \u2014 weak side",
        ""])
    text = "\n".join(lines)
    # the description is prose that would wrap a terminal line, so it is left to the HTML
    check("axis description absent from the Markdown", "axis description" in text, False)
    check("no related patterns line under the axis", [ln for ln in lines if ln.startswith("Related patterns")], [])

    doc = _full_doc()
    doc["summary"]["axes"].append({"name": "axis two", "strong": "strong two", "weak": "weak two", "patterns": ["y1"]})
    lines = _md_lines(doc)
    # written by hand: the blank line after each axis is what keeps the next bold name out of the list above it
    check("two axes each closed by a blank line", _has_block(lines, [
        "**axis one**", "- Reliably gets right \u2014 strong side", "- Reliably misses \u2014 weak side", "",
        "**axis two**", "- Reliably gets right \u2014 strong two", "- Reliably misses \u2014 weak two", "",
        "**a flow**"]), True)

    doc = _full_doc()
    doc["labels"] = {"strong": "\u5f37\u9805", "weak": "\u5f31\u9805"}
    lines = _md_lines(doc)
    check("overridden side labels lead the list lines", _has_block(lines, [
        "**axis one**", "- \u5f37\u9805 \u2014 strong side", "- \u5f31\u9805 \u2014 weak side", ""]), True)
    check("English side labels replaced", [ln for ln in lines if "Reliably" in ln], [])


def test_markdown_axis_escaping():
    axis = {"name": "C:\\dir axis", "strong": "C:\\dir strong", "weak": "C:\\dir weak", "patterns": ["s1"]}
    doc = _doc(summary={"text": "t", "axes": [axis]},
               labels=_all_labels({"strong": "C:\\dir good", "weak": "C:\\dir bad"}))
    check("fixture: backslash axis document is valid", rr.validate(doc), [])
    lines = _md_lines(doc)
    # written by hand: C, colon, two backslashes, then the rest of each value
    check("backslashes doubled in the axis lines", _has_block(lines, [
        "**C:\\\\dir axis**",
        "- C:\\\\dir good \u2014 C:\\\\dir strong",
        "- C:\\\\dir bad \u2014 C:\\\\dir weak", ""]), True)

    axis = {"name": "axis\none", "strong": "strong\nside", "weak": "weak\nside", "patterns": ["s1"]}
    lines = _md_lines(_doc(summary={"text": "t", "axes": [axis]}))
    # written by hand: each line break becomes one space, so the bold span and each list item stay on one line
    check("line breaks flattened in the axis lines", _has_block(lines, [
        "**axis one**",
        "- Reliably gets right \u2014 strong side",
        "- Reliably misses \u2014 weak side", ""]), True)
    check("no axis value split onto a line of its own",
          [ln for ln in lines if ln in ("one**", "side", "axis", "- Reliably gets right \u2014 strong")], [])

    # these lines are prose, not table cells, so a pipe in them is left as typed
    axis = {"name": "a|xis", "strong": "str|ong", "weak": "we|ak", "patterns": ["s1"]}
    lines = _md_lines(_doc(summary={"text": "t", "axes": [axis]}))
    check("pipe left raw in the axis lines", _has_block(lines, [
        "**a|xis**",
        "- Reliably gets right \u2014 str|ong",
        "- Reliably misses \u2014 we|ak", ""]), True)


def test_markdown_three_column_pattern_tables():
    lines = _md_lines(_full_doc())
    # written by hand: each group is a heading, a three-column head, and one row per pattern,
    # closed by a blank line, then a line per conditional pattern naming its constraint and another blank line,
    # then the next heading
    for heading, row, conditions, following in (
            ("Strengths", "| name s1 | code | Verified |", [], "## Gaps"),
            ("Gaps", "| name g1 | code | Conditional |", ["- name g1 \u2014 Depends on: the load", ""],
             "## Ways of working"),
            ("Ways of working", "| name y1 | code | Verified |", [], "## What this means for your work")):
        check("%s table holds exactly the head and its row" % heading, _has_block(lines, [
            "## " + heading, "", "| Pattern | Source | Confidence |", "|---|---|---|", row, ""]
            + conditions + [following]), True)
    text = "\n".join(lines)
    for absent in ("desc s1", "desc g1", "desc y1", "speed", "rework",
                   "Description", "What it gives", "What it costs"):
        check("%s absent from the Markdown" % absent, absent in text, False)

    doc = _full_doc()
    doc["labels"] = {"pattern": "Pat|tern", "source": "Sou|rce", "confidence": "Con|fidence"}
    lines = _md_lines(doc)
    # written by hand: each pipe in a header label escaped with one backslash, so the head keeps three columns
    check("pattern table header labels escaped as cells",
          lines.count("| Pat\\|tern | Sou\\|rce | Con\\|fidence |"), 3)


def test_markdown_implications_list():
    lines = _md_lines(_full_doc())
    # written by hand: a heading, then each implication as its area in bold and its meaning after an em dash,
    # closed by a blank line before the report path
    check("implications list holds exactly its heading and its line", _has_block(lines, [
        "## What this means for your work", "",
        "- **reviews** \u2014 ask early", "",
        "Full report: `OUT`"]), True)
    implications = lines[lines.index("## What this means for your work"):]
    # a list of pattern names wrapped and broke the old table, so the related patterns are left to the HTML
    check("no pattern name in the implications lines",
          [ln for ln in implications if "name s1" in ln or "name g1" in ln], [])
    text = "\n".join(lines)
    check("related patterns and area labels absent from the Markdown",
          ("Related patterns" in text, "Area" in text), (False, False))
    check("meaning header absent from the Markdown", "What it means" in text, False)


def _detail_doc(labels=None, detail=("one", "two")):
    doc = _step_doc(_step("a", detail=list(detail)))
    if labels is not None:
        doc["labels"] = labels
    return doc


def test_markdown_list_separator():
    check("default list separator joins detail items", "1. a \u2014 one; two" in _md_lines(_detail_doc()), True)
    check("slash list separator joins detail items",
          "1. a \u2014 one / two" in _md_lines(_detail_doc({"list_separator": " / "})), True)
    check("ideographic list separator joins detail items",
          "1. a \u2014 one\u3001two" in _md_lines(_detail_doc({"list_separator": "\u3001"})), True)
    # a comma inside an item would read as two items if the items were joined by a comma
    check("a detail item holding a comma stays one item",
          "1. a \u2014 fast, careful; two" in _md_lines(_detail_doc(detail=("fast, careful", "two"))), True)
    # the joined details are prose, not a table cell, so a backslash in the separator is doubled and its pipe left as typed.
    # written by hand: space, two backslashes, pipe, space
    check("list separator escaped as prose",
          "1. a \u2014 one \\\\| two" in _md_lines(_detail_doc({"list_separator": " \\| "})), True)

    doc = _detail_doc({"separator": " + "})
    doc["strengths"][0]["sources"] = ["code", "prompts"]
    lines = _md_lines(doc)
    # the sources separator shows up in its own column, so the fixture did override it
    check("sources separator applied to the sources column", "| name s1 | code + prompts | Verified |" in lines, True)
    check("sources separator leaves the detail joiner alone", "1. a \u2014 one; two" in lines, True)


def test_markdown_report_file_label():
    # the label is prose, not a table cell, so its pipe tells md_text apart from md_cell
    doc = _doc(labels={"report_file": "C:\\dir re|port"})
    # written by hand: the label's backslash doubled and its pipe left as typed, the path in the span as passed
    check("report_file label doubles its backslash", _md_lines(doc)[-1], "C:\\\\dir re|port: `OUT`")
    last = rr.render_markdown(doc, rr.labels_for(doc), "C:\\out\\report.html").splitlines()[-1]
    # written by hand: two backslashes in the label, one in each place of the path
    check("only the label is doubled, never the path", last, "C:\\\\dir re|port: `C:\\out\\report.html`")
    check("report_file label pipe not escaped as a cell", "re\\|port" in last, False)


# ----- diagrams ------------------------------------------------------------------

def _step(text, **extra):
    s = {"text": text}
    s.update(extra)
    return s


def _lane(steps, **extra):
    lane = {"steps": steps}
    lane.update(extra)
    return lane


def _diagram(lanes, **extra):
    """A diagram with a caption and the lanes as given, valid whenever its lanes are."""
    d = {"caption": "a flow", "lanes": lanes}
    d.update(extra)
    return d


def _diagram_doc(d):
    return _doc(diagrams=[d])


def _lane_doc(lane):
    return _diagram_doc(_diagram([lane]))


def _step_doc(step):
    return _lane_doc(_lane([step]))


def _invalid_all(name, doc, problems):
    code, out, err, out_path = _main(doc)
    check(name + ": exits 2", code, 2)
    check(name + ": the problems named on stderr in order", err, SCHEMA_HEADER + "".join("- %s\n" % p for p in problems))
    check(name + ": stdout silent", out, "")
    check(name + ": no file written", os.path.exists(out_path), False)


def test_validation_diagram_shape():
    ok = _diagram([_lane([_step("a")])])
    _invalid("diagram without lanes", _diagram_doc(_without(ok, "lanes")), "diagrams[0]: 'lanes' is missing or empty")
    _invalid("diagram with empty lanes", _diagram_doc(_diagram([])), "diagrams[0]: 'lanes' is missing or empty")
    _invalid("lanes that is not a list", _diagram_doc(_diagram(_lane([_step("a")]))),
             "diagrams[0]: 'lanes' is missing or empty")
    # a diagram used to be text drawn as boxes, and that shape must be refused rather than render an empty figure
    _invalid("old ascii diagram shape", _diagram_doc({"caption": "a flow", "ascii": "+---+\n| a |\n+---+"}),
             "diagrams[0]: 'lanes' is missing or empty")
    # a bare value has neither key, so both are named, lanes first as the diagram is walked
    _invalid_all("diagram that is not an object", _doc(diagrams=["a flow"]),
                 ["diagrams[0]: 'lanes' is missing or empty", "diagrams[0]: 'caption' is missing or empty"])
    _invalid("diagram without caption", _diagram_doc(_without(ok, "caption")),
             "diagrams[0]: 'caption' is missing or empty")
    _invalid("diagram with empty caption", _diagram_doc(dict(ok, caption="")),
             "diagrams[0]: 'caption' is missing or empty")
    _invalid("diagrams that is not a list", _doc(diagrams=ok), "'diagrams' is not a list")
    check("fixture: minimal document has no diagrams key", "diagrams" in _doc(), False)
    check("no diagrams key valid", rr.validate(_doc()), [])
    check("empty diagrams list valid", rr.validate(_doc(diagrams=[])), [])


def test_validation_diagram_lanes():
    _invalid("lane without steps", _lane_doc(_without(_lane([_step("a")]), "steps")),
             "diagrams[0].lanes[0]: 'steps' is missing or empty")
    _invalid("lane with empty steps", _lane_doc(_lane([])), "diagrams[0].lanes[0]: 'steps' is missing or empty")
    _invalid("steps that is not a list", _lane_doc(_lane(_step("a"))), "diagrams[0].lanes[0]: 'steps' is missing or empty")
    _invalid("lane that is not an object", _lane_doc("lane one"), "diagrams[0].lanes[0]: 'steps' is missing or empty")

    # side by side lanes are told apart only by their titles, so each needs one once there are two
    _invalid("second of two lanes without a title",
             _diagram_doc(_diagram([_lane([_step("a")], title="lane one"), _lane([_step("b")])])),
             "diagrams[0].lanes[1]: 'title' is missing or empty")
    _invalid("first of two lanes with an empty title",
             _diagram_doc(_diagram([_lane([_step("a")], title=""), _lane([_step("b")], title="lane two")])),
             "diagrams[0].lanes[0]: 'title' is missing or empty")
    check("single lane without a title valid", rr.validate(_lane_doc(_lane([_step("a")]))), [])
    check("single lane with an empty title valid", rr.validate(_lane_doc(_lane([_step("a")], title=""))), [])

    def titled(n):
        return [_lane([_step("step %d" % i)], title="lane %d" % i) for i in range(1, n + 1)]

    _invalid("four lanes", _diagram_doc(_diagram(titled(4))),
             "diagrams[0]: a diagram holds at most three lanes side by side")
    # three is the boundary that passes
    check("three lanes valid", rr.validate(_diagram_doc(_diagram(titled(3)))), [])


def test_validation_diagram_steps():
    where = "diagrams[0].lanes[0].steps[0]"
    _invalid("step without text", _step_doc({"tone": "strong"}), where + ": 'text' is missing or empty")
    _invalid("step with empty text", _step_doc(_step("")), where + ": 'text' is missing or empty")
    _invalid("step text that is not a string", _step_doc(_step(5)), where + ": 'text' is missing or empty")
    _invalid("step that is not an object", _step_doc("step one"), where + ": 'text' is missing or empty")

    _invalid("tone outside the three", _step_doc(_step("a", tone="loud")),
             where + ": tone must be one of neutral, strong, weak")
    # a null tone is present, so it does not fall back to neutral
    _invalid("null tone", _step_doc(_step("a", tone=None)), where + ": tone must be one of neutral, strong, weak")
    for tone in ("neutral", "strong", "weak"):
        check("tone %s valid" % tone, rr.validate(_step_doc(_step("a", tone=tone))), [])
    check("no tone valid", rr.validate(_step_doc(_step("a"))), [])

    _invalid("detail that is not a list", _step_doc(_step("a", detail="one line")),
             where + ": 'detail' is not a list of text")
    _invalid("detail holding a non-string", _step_doc(_step("a", detail=["one", 2])),
             where + ": 'detail' is not a list of text")
    check("no detail valid", rr.validate(_step_doc(_step("a"))), [])
    check("empty detail valid", rr.validate(_step_doc(_step("a", detail=[]))), [])


def test_validation_diagram_paths():
    ok = _diagram([_lane([_step("a")])])
    _invalid("second diagram", _doc(diagrams=[ok, _without(ok, "caption")]), "diagrams[1]: 'caption' is missing or empty")
    _invalid("second lane", _diagram_doc(_diagram([_lane([_step("a")], title="one"), _lane([], title="two")])),
             "diagrams[0].lanes[1]: 'steps' is missing or empty")
    _invalid("second step", _lane_doc(_lane([_step("a"), _step("")])),
             "diagrams[0].lanes[0].steps[1]: 'text' is missing or empty")
    # a different index at each level, so a path built from the wrong counter names the wrong place
    deep = _diagram([_lane([_step("a")], title="one"), _lane([_step("b")], title="two"),
                     _lane([_step("c"), _step("d"), _step("e"), _step("f", tone="loud")], title="three")])
    _invalid("fourth step of the third lane of the second diagram", _doc(diagrams=[ok, deep]),
             "diagrams[1].lanes[2].steps[3]: tone must be one of neutral, strong, weak")


def test_validation_diagram_problem_order():
    doc = _without(_doc(implications="reviews", diagrams=[{"lanes": [_lane([_step("a")])]}]), "scope")
    # written by hand in the order the schema is walked: the implications, then the diagrams, then the scope
    _invalid_all("diagram problems between implications and scope", doc, [
        "'implications' is not a list",
        "diagrams[0]: 'caption' is missing or empty",
        "document: 'scope' is missing or empty"])

    lanes = [_lane([_step("a")], title="one"), _lane([_step("b")], title="two"), _lane([_step("c")], title="three"),
             _lane([{"text": "", "tone": "loud", "detail": "x"}])]
    # written by hand: the caption and the lane count, then the fourth lane's title, then its step field by field
    _invalid_all("problems within one diagram in walk order", _diagram_doc({"lanes": lanes}), [
        "diagrams[0]: 'caption' is missing or empty",
        "diagrams[0]: a diagram holds at most three lanes side by side",
        "diagrams[0].lanes[3]: 'title' is missing or empty",
        "diagrams[0].lanes[3].steps[0]: 'text' is missing or empty",
        "diagrams[0].lanes[3].steps[0]: tone must be one of neutral, strong, weak",
        "diagrams[0].lanes[3].steps[0]: 'detail' is not a list of text"])


# written by hand from the diagram in _full_doc: the caption leads, then two titled lanes,
# the first with a strong step carrying two details and a note on its arrow, then a weak step,
# the second with one step of no tone
_FULL_FIGURE = ('<figure class="flow"><figcaption>a flow</figcaption><div class="lanes n2">'
                '<div class="lane"><div class="lane-title">lane one</div>'
                '<div class="step strong"><div class="step-text">step one</div>'
                '<ul><li>detail one</li><li>detail two</li></ul></div>'
                '<div class="arrow"><span class="arrow-mark">&#8595;</span><span class="arrow-note">then</span></div>'
                '<div class="step weak"><div class="step-text">step two</div></div></div>'
                '<div class="lane"><div class="lane-title">lane two</div>'
                '<div class="step neutral"><div class="step-text">step three</div></div></div>'
                '</div></figure>')

_ARROW = '<div class="arrow"><span class="arrow-mark">&#8595;</span></div>'


def _one_step_figure(text, caption):
    return ('<figure class="flow"><figcaption>%s</figcaption><div class="lanes n1"><div class="lane">'
            '<div class="step neutral"><div class="step-text">%s</div></div></div></div></figure>' % (caption, text))


def test_diagram_html_structure():
    check("two-lane diagram", rr.diagram_html(_full_doc()["diagrams"][0]), _FULL_FIGURE)
    check("one untitled lane holding one step", rr.diagram_html(_diagram([_lane([_step("only")])])),
          _one_step_figure("only", "a flow"))

    for n in (1, 2, 3):
        got = rr.diagram_html(_diagram([_lane([_step("step %d" % i)], title="lane %d" % i) for i in range(1, n + 1)]))
        check("%d lanes: the lanes class carries the count" % n,
              got.startswith('<figure class="flow"><figcaption>a flow</figcaption>'
                             '<div class="lanes n%d"><div class="lane">' % n), True)
        check("%d lanes: one lane div per lane" % n, got.count('<div class="lane">'), n)
        check("%d lanes: each title in its lane" % n,
              [('<div class="lane"><div class="lane-title">lane %d</div>' % i) in got for i in range(1, n + 1)],
              [True] * n)

    check("no title renders no lane title", "lane-title" in rr.diagram_html(_diagram([_lane([_step("a")])])), False)
    check("empty title renders no lane title",
          "lane-title" in rr.diagram_html(_diagram([_lane([_step("a")], title="")])), False)

    for tone in ("neutral", "strong", "weak"):
        check("tone %s is the step class" % tone,
              '<div class="step %s"><div class="step-text">a</div></div>' % tone
              in rr.diagram_html(_diagram([_lane([_step("a", tone=tone)])])), True)
    check("no tone renders as neutral",
          '<div class="step neutral"><div class="step-text">a</div></div>'
          in rr.diagram_html(_diagram([_lane([_step("a")])])), True)

    check("detail listed under the text in order",
          '<div class="step neutral"><div class="step-text">a</div><ul><li>second</li><li>first</li></ul></div>'
          in rr.diagram_html(_diagram([_lane([_step("a", detail=["second", "first"])])])), True)
    check("no detail renders no list", "<ul>" in rr.diagram_html(_diagram([_lane([_step("a")])])), False)
    check("empty detail renders no list", "<ul>" in rr.diagram_html(_diagram([_lane([_step("a", detail=[])])])), False)


def test_diagram_html_arrows():
    lane = _lane([_step("s1", note="first"), _step("s2"), _step("s3", note="dropped")])
    got = rr.diagram_html(_diagram([lane]))
    # written by hand: an arrow between each pair of consecutive steps, labelled only when the step above has a note
    check("three steps joined by two arrows", '<div class="lane">%s</div>' % "".join([
        '<div class="step neutral"><div class="step-text">s1</div></div>',
        '<div class="arrow"><span class="arrow-mark">&#8595;</span><span class="arrow-note">first</span></div>',
        '<div class="step neutral"><div class="step-text">s2</div></div>',
        _ARROW,
        '<div class="step neutral"><div class="step-text">s3</div></div>']) in got, True)
    check("arrow count is one fewer than the steps", got.count('<div class="arrow">'), 2)
    check("arrow without a note has no note span", got.count('<span class="arrow-note">'), 1)
    # the last step has no arrow below it, so its note has nowhere to go
    check("note on the last step not shown", "dropped" in got, False)

    single = rr.diagram_html(_diagram([_lane([_step("only", note="dropped")])]))
    check("single-step lane has no arrow", 'class="arrow' in single, False)
    check("single-step lane note not shown", "dropped" in single, False)

    two = rr.diagram_html(_diagram([_lane([_step("a"), _step("b")], title="one"), _lane([_step("c")], title="two")]))
    check("arrows counted per lane, not across lanes", two.count('<div class="arrow">'), 1)


def test_diagram_html_placement():
    page = _html(_full_doc())
    summary = _section(page, "Summary")
    # written by hand: the close of the weak side's links, the weak side, the sides, the axis card,
    # and the axes block, then the figure
    check("figure follows the axes block at the end of the summary",
          summary.endswith('<a href="#g1">name g1</a></div></div></div></div></div>' + _FULL_FIGURE), True)
    check("figure rendered once on the page", page.count("<figure"), 1)

    first = _diagram([_lane([_step("first")])], caption="one")
    second = _diagram([_lane([_step("second")])], caption="two")
    check("several diagrams in document order inside the summary",
          _section(_html(_doc(diagrams=[first, second])), "Summary"),
          "<h2>Summary</h2><p>the summary</p></div>" + _one_step_figure("first", "one") + _one_step_figure("second", "two"))


def test_diagram_css():
    page = _html(_full_doc())
    style = page[page.index("<style>"):page.index("</style>")]
    for selector in ("figure", ".lanes", ".lane", ".lane-title", ".step", ".step.strong", ".step.weak",
                     ".step-text", ".arrow", ".arrow-mark", ".arrow-note", "figcaption"):
        check("CSS rule for %s" % selector, (selector + " {") in style, True)
    # lanes are compared side by side, one column per lane
    check("two lanes take two columns", ".lanes.n2 { grid-template-columns:1fr 1fr; }" in style, True)
    check("three lanes take three columns", ".lanes.n3 { grid-template-columns:1fr 1fr 1fr; }" in style, True)
    check("lanes stack on a narrow screen", ".lanes.n2, .lanes.n3 { grid-template-columns:1fr; }" in style, True)
    # a tone takes the same colours as the axes
    check("strong step takes the strong side colour",
          (".step.strong { background:var(--right-wash);" in style, ".side.strong { background:var(--right-wash); }" in style),
          (True, True))
    check("weak step takes the weak side colour",
          (".step.weak { background:var(--miss-wash);" in style, ".side.weak { background:var(--miss-wash); }" in style),
          (True, True))
    check("old preformatted figure rule gone", "figure pre" in style, False)
    check("no pre element on the page", "<pre" in page, False)


def test_diagram_escaping():
    m, e = _markup, _escaped
    d = _diagram([_lane([_step(m("step-text"), detail=[m("detail-one"), m("detail-two")], note=m("note")),
                         _step("last")], title=m("lane-title"))], caption=m("caption"))
    doc = _diagram_doc(d)
    check("fixture: markup diagram document is valid", rr.validate(doc), [])
    page = _html(doc)
    check("lane title escaped", '<div class="lane-title">%s</div>' % e("lane-title") in page, True)
    check("step text escaped", '<div class="step-text">%s</div>' % e("step-text") in page, True)
    check("each detail item escaped", "<ul><li>%s</li><li>%s</li></ul>" % (e("detail-one"), e("detail-two")) in page,
          True)
    check("note escaped", '<span class="arrow-note">%s</span>' % e("note") in page, True)
    check("caption escaped", "<figcaption>%s</figcaption>" % e("caption") in page, True)
    check("no raw script tag anywhere", "<script" in page, False)

    # validate rejects a tone outside the three, so the class attribute is checked on the renderer directly
    got = rr.diagram_html(_diagram([_lane([_step("a", tone=m("tone"))])]))
    check("tone escaped inside the class attribute", '<div class="step %s">' % e("tone") in got, True)
    # the value runs to the quote that closes the tag, so a raw quote inside it would show up here
    values = re.findall(r'<div class="step (.*?)"><div class="step-text">', got)
    check("a step class attribute found", len(values) > 0, True)
    check("no raw quote inside a step class attribute", [v for v in values if '"' in v], [])
    check("no raw script tag from the tone", "<script" in got, False)


def _md_diagram_lines(doc):
    """The Markdown lines between the summary text and the first group heading, for a document with no axes."""
    lines = _md_lines(doc)
    return lines[lines.index("the summary") + 2:lines.index("## Strengths")]


def test_markdown_diagram_list():
    d = _diagram([
        _lane([_step("step one", detail=["detail one", "detail two"], note="then"), _step("step two"),
               _step("step three", note="dropped")], title="lane one"),
        _lane([_step("step four", detail=[]), _step("step five")], title="lane two")])
    # written by hand: the caption in bold leads, then each lane under its italic title, numbered from 1,
    # the details after an em dash, an arrow only under a step whose note it carries,
    # and a blank line after each lane
    check("diagram listed lane by lane", _md_diagram_lines(_diagram_doc(d)), [
        "**a flow**",
        "",
        "*lane one*",
        "1. step one \u2014 detail one; detail two",
        "   \u2193 then",
        "2. step two",
        "3. step three",
        "",
        "*lane two*",
        "1. step four",
        "2. step five",
        ""])
    check("untitled lane has no title line", _md_diagram_lines(_diagram_doc(_diagram([_lane([_step("only")])]))),
          ["**a flow**", "", "1. only", ""])
    check("empty title has no title line",
          _md_diagram_lines(_diagram_doc(_diagram([_lane([_step("only")], title="")]))),
          ["**a flow**", "", "1. only", ""])
    # the numbering already gives the order, so an arrow with no note to carry would only lengthen the list
    check("no bare arrow line", [ln for ln in _md_lines(_diagram_doc(d)) if ln.strip() == "\u2193"], [])
    check("last-step note in no line", [ln for ln in _md_lines(_diagram_doc(d)) if "dropped" in ln], [])
    # a code fence is what the old text-drawn diagram needed, and the list form needs none
    check("no code fence anywhere", "```" in "\n".join(_md_lines(_full_doc())), False)


def test_markdown_diagram_escaping():
    d = _diagram([_lane([_step("C:\\dir text", detail=["C:\\dir one", "C:\\dir two"], note="C:\\dir note"),
                         _step("last")], title="C:\\dir title")], caption="C:\\dir caption")
    check("fixture: backslash diagram document is valid", rr.validate(_diagram_doc(d)), [])
    # written by hand: C, colon, two backslashes, then the rest of each value
    check("backslashes doubled in the diagram lines", _md_diagram_lines(_diagram_doc(d)), [
        "**C:\\\\dir caption**",
        "",
        "*C:\\\\dir title*",
        "1. C:\\\\dir text \u2014 C:\\\\dir one; C:\\\\dir two",
        "   \u2193 C:\\\\dir note",
        "2. last",
        ""])

    d = _diagram([_lane([_step("step\none", detail=["detail\none", "detail\ntwo"], note="then\nnext"),
                         _step("last")], title="lane\none")], caption="a\nflow")
    # written by hand: each line break becomes one space, so a bold span, a list item, or the caption stays on one line
    check("line breaks flattened in the diagram lines", _md_diagram_lines(_diagram_doc(d)), [
        "**a flow**",
        "",
        "*lane one*",
        "1. step one \u2014 detail one; detail two",
        "   \u2193 then next",
        "2. last",
        ""])

    # these lines are prose, not table cells, so a pipe in them is left as typed
    d = _diagram([_lane([_step("st|ep", detail=["de|tail one", "de|tail two"], note="th|en"), _step("last")],
                        title="la|ne")], caption="a |flow")
    check("pipes left raw in the diagram lines", _md_diagram_lines(_diagram_doc(d)), [
        "**a |flow**",
        "",
        "*la|ne*",
        "1. st|ep \u2014 de|tail one; de|tail two",
        "   \u2193 th|en",
        "2. last",
        ""])


def test_markdown_diagram_placement():
    lines = _md_lines(_full_doc())
    # written by hand: the axis block closed by its blank line, then the diagram, then the first group heading
    check("diagram between the axes block and the first group heading",
          lines[lines.index("**axis one**"):lines.index("## Strengths") + 1], [
              "**axis one**",
              "- Reliably gets right \u2014 strong side",
              "- Reliably misses \u2014 weak side",
              "",
              "**a flow**",
              "",
              "*lane one*",
              "1. step one \u2014 detail one; detail two",
              "   \u2193 then",
              "2. step two",
              "",
              "*lane two*",
              "1. step three",
              "",
              "## Strengths"])

    first = _diagram([_lane([_step("first")])], caption="one")
    second = _diagram([_lane([_step("second")])], caption="two")
    check("several diagrams in document order", _md_diagram_lines(_doc(diagrams=[first, second])),
          ["**one**", "", "1. first", "", "**two**", "", "1. second", ""])


# ----- confidence badges and conditions ------------------------------------------

_LOAD = {"level": "depends", "on": "the load"}


def test_badge_html():
    # written by hand: a verified badge is a bare span, since it has no constraint to show
    verified = rr.badge_html({"level": "verified"}, rr.LABELS)
    check("verified badge is a bare span", verified, '<span class="badge verified">Verified</span>')
    check("verified badge has no tabindex, title, or tip",
          ("tabindex" in verified, "title=" in verified, "data-tip" in verified), (False, False, False))

    depends = rr.badge_html(_LOAD, rr.LABELS)
    # written by hand: the short label as the text, the constraint in both the title and the tip
    check("depends badge carries its constraint", depends,
          '<span class="badge depends" tabindex="0" title="Depends on: the load"'
          ' data-tip="Depends on: the load">Conditional</span>')
    title = re.findall(r'title="(.*?)" data-tip="', depends)
    tip = re.findall(r'data-tip="(.*?)">', depends)
    check("title and tip hold the same text", (title, tip), (["Depends on: the load"], ["Depends on: the load"]))
    check("title is the constraint text", title == [rr.constraint_text(_LOAD, rr.LABELS)], True)

    m, e = _markup, _escaped
    lab = rr.labels_for({"labels": {"depends": m("label-depends"), "conditional": m("label-conditional"),
                                    "verified": m("label-verified")}})
    got = rr.badge_html({"level": "depends", "on": m("on")}, lab)
    # written by hand: the label, a colon and a space, then the constraint, each escaped, in both attributes
    tip_text = "%s: %s" % (e("label-depends"), e("on"))
    check("markup escaped in the depends badge", got,
          '<span class="badge depends" tabindex="0" title="%s" data-tip="%s">%s</span>' % (
              tip_text, tip_text, e("label-conditional")))
    check("markup escaped in the verified badge", rr.badge_html({"level": "verified"}, lab),
          '<span class="badge verified">%s</span>' % e("label-verified"))
    # the value runs to the quote that closes the attribute, so a raw quote inside it would show up here
    for name, pattern in (("title", r'title="(.*?)" data-tip="'), ("data-tip", r'data-tip="(.*?)">')):
        values = re.findall(pattern, got)
        # an empty match list would pass the raw-quote check without having looked at anything
        check("a %s attribute found" % name, len(values) > 0, True)
        check("no raw quote inside a %s attribute" % name, [v for v in values if '"' in v], [])
    check("no raw script tag in the badge", "<script" in got, False)


def test_constraint_and_confidence_text():
    check("verified has no constraint", rr.constraint_text({"level": "verified"}, rr.LABELS), "")
    # the level decides, so a stray on beside a verified level is not shown
    check("verified ignores a stray on", rr.constraint_text({"level": "verified", "on": "x"}, rr.LABELS), "")
    # written by hand: the depends label, a colon and a space, then what it depends on
    check("depends constraint", rr.constraint_text(_LOAD, rr.LABELS), "Depends on: the load")
    only = rr.labels_for({"labels": {"depends": "Only when"}})
    check("overridden depends label leads the constraint", rr.constraint_text(_LOAD, only), "Only when: the load")

    check("verified confidence text", rr.confidence_text({"level": "verified"}, rr.LABELS), "Verified")
    check("depends confidence text is the short label", rr.confidence_text(_LOAD, rr.LABELS), "Conditional")
    lab = rr.labels_for({"labels": {"verified": "Checked", "conditional": "Maybe"}})
    check("overridden verified label", rr.confidence_text({"level": "verified"}, lab), "Checked")
    check("overridden conditional label", rr.confidence_text(_LOAD, lab), "Maybe")
    # a column that held the constraint would grow to fit it, so the constraint never reaches the short label
    check("depends confidence text never holds the constraint",
          ("the load" in rr.confidence_text(_LOAD, rr.LABELS), "Depends on" in rr.confidence_text(_LOAD, rr.LABELS)),
          (False, False))


def test_conditional_label_localised():
    check("English conditional label", rr.LABELS["conditional"], "Conditional")
    doc = _full_doc()
    doc["labels"] = {"conditional": "\u689d\u4ef6\u5f0f", "depends": "\u53d6\u6c7a\u65bc"}
    page = _html(doc)
    gaps = _section(page, "Gaps")
    # written by hand: the localised short label as the badge text, the localised depends label in the tooltip
    check("localised badge in the HTML",
          '<td class="conf"><span class="badge depends" tabindex="0" title="\u53d6\u6c7a\u65bc: the load"'
          ' data-tip="\u53d6\u6c7a\u65bc: the load">\u689d\u4ef6\u5f0f</span></td>' in gaps, True)
    check("localised depends label heads the evidence block", "<h4>\u53d6\u6c7a\u65bc</h4><p>the load</p>" in gaps, True)
    check("English labels gone from the page", ("Conditional" in page, "Depends on" in page), (False, False))

    lines = _md_lines(doc)
    check("localised label in the Markdown confidence cell", "| name g1 | code | \u689d\u4ef6\u5f0f |" in lines, True)
    check("localised depends label in the Markdown condition line",
          "- name g1 \u2014 \u53d6\u6c7a\u65bc: the load" in lines, True)
    text = "\n".join(lines)
    check("English labels gone from the Markdown", ("Conditional" in text, "Depends on" in text), (False, False))


def test_evidence_depends_block():
    p = _pattern("s1", confidence={"level": "depends", "on": "the load"}, exceptions=[{"text": "exception of s1"}],
                 calibration="3 of 10 elsewhere", checked="read every script")
    check("fixture: depends strength document is valid", rr.validate(_doc(strengths=[p])), [])
    # written by hand: instances, exceptions, then what it depends on, then calibration and what was checked
    full = ('<details><summary>Evidence</summary>'
            '<h4>Instances</h4><ul><li>instance one of s1 <span class="ref">ref one of s1</span></li>'
            '<li>instance two of s1 <span class="ref">ref two of s1</span></li></ul>'
            '<h4>Exceptions</h4><ul><li>exception of s1</li></ul>'
            '<h4>Depends on</h4><p>the load</p>'
            '<h4>Against the rest of the repository</h4><p>3 of 10 elsewhere</p>'
            '<h4>What was checked</h4><p>read every script</p></details>')
    check("depends block between exceptions and calibration", rr.evidence_html(p, rr.LABELS), full)
    check("depends block rendered in the page", full in _section(_html(_doc(strengths=[p])), "Strengths"), True)

    # written by hand: a verified pattern holds only its instances
    check("verified pattern has no depends block", rr.evidence_html(_pattern("g1"), rr.LABELS),
          '<details><summary>Evidence</summary>'
          '<h4>Instances</h4><ul><li>instance one of g1 <span class="ref">ref one of g1</span></li>'
          '<li>instance two of g1 <span class="ref">ref two of g1</span></li></ul></details>')

    got = rr.evidence_html(_pattern("g1", confidence={"level": "depends", "on": _markup("on")}), rr.LABELS)
    check("on escaped in the depends block", "<h4>Depends on</h4><p>%s</p>" % _escaped("on") in got, True)
    check("no raw script tag in the evidence", "<script" in got, False)

    got = rr.evidence_html(_pattern("g1", confidence=_LOAD), rr.labels_for({"labels": {"depends": "Only when"}}))
    check("overridden depends label heads the block",
          ("<h4>Only when</h4><p>the load</p>" in got, "Depends on" in got), (True, False))


_HEAD = ["| Pattern | Source | Confidence |", "|---|---|---|"]


def test_markdown_conditions_list():
    doc = _doc(strengths=[_pattern("s1", exceptions=[], confidence={"level": "depends", "on": "load one"}),
                          _pattern("s2", exceptions=[]),
                          _pattern("s3", exceptions=[], confidence={"level": "depends", "on": "load three"})],
               gaps=[_pattern("g1")])
    check("fixture: conditions document is valid", rr.validate(doc), [])
    lines = _md_lines(doc)
    # written by hand: the conditional patterns of a group under its table in document order, the verified one left out,
    # then a blank line, and a group with no conditional pattern closed by its one blank line alone
    check("conditions listed per group under its table", lines[lines.index("## Strengths"):], [
        "## Strengths", ""] + _HEAD + [
        "| name s1 | code | Conditional |",
        "| name s2 | code | Verified |",
        "| name s3 | code | Conditional |",
        "",
        "- name s1 \u2014 Depends on: load one",
        "- name s3 \u2014 Depends on: load three",
        "",
        "## Gaps", ""] + _HEAD + [
        "| name g1 | code | Verified |",
        "",
        "Full report: `OUT`"])

    doc = _doc(strengths=[_pattern("s1", exceptions=[], confidence={"level": "depends", "on": "strength load"})],
               gaps=[_pattern("g1", confidence={"level": "depends", "on": "gap load"})])
    lines = _md_lines(doc)
    # written by hand: each group lists only its own conditional pattern
    check("each group lists only its own conditions", lines[lines.index("## Strengths"):], [
        "## Strengths", ""] + _HEAD + [
        "| name s1 | code | Conditional |", "",
        "- name s1 \u2014 Depends on: strength load", "",
        "## Gaps", ""] + _HEAD + [
        "| name g1 | code | Conditional |", "",
        "- name g1 \u2014 Depends on: gap load", "",
        "Full report: `OUT`"])

    doc = _doc(strengths=[_pattern("s1", exceptions=[], name="name\none",
                                   confidence={"level": "depends", "on": "load\none"})])
    lines = _md_lines(doc)
    # written by hand: each line break becomes one space, so the list item stays on one line
    check("line breaks flattened in a condition line", "- name one \u2014 Depends on: load one" in lines, True)
    check("no condition split onto a second line", [ln for ln in lines if ln.startswith("one")], [])

    doc = _doc(strengths=[_pattern("s1", exceptions=[], name="a\\|b",
                                   confidence={"level": "depends", "on": "C:\\dir|x"})])
    # a condition line is prose, not a table cell, so a pipe stays as typed while a backslash is doubled.
    # written by hand: a, two backslashes, pipe, b, then C, colon, two backslashes, dir, pipe, x
    check("condition line escaped as prose", "- a\\\\|b \u2014 Depends on: C:\\\\dir|x" in _md_lines(doc), True)

    doc = _doc(strengths=[_pattern("s1", exceptions=[], confidence=_LOAD)], labels={"depends": "Only when"})
    check("overridden depends label in the condition line", "- name s1 \u2014 Only when: the load" in _md_lines(doc),
          True)


# ----- axis links ----------------------------------------------------------------

def _axis_doc(patterns=None, labels=None):
    axis = {"name": "axis one", "strong": "strong side", "weak": "weak side"}
    if patterns is not None:
        axis["patterns"] = patterns
    doc = _doc(summary={"text": "the summary", "axes": [axis]},
               strengths=[_pattern("s1", exceptions=[]), _pattern("s2", exceptions=[])],
               gaps=[_pattern("g1"), _pattern("g2")],
               styles=[_pattern("y1", gives="g", costs="c"), _pattern("y2", gives="g", costs="c")])
    if labels is not None:
        doc["labels"] = labels
    return doc


def test_axis_link_split():
    # the ids are given out of document order and mixed across groups, so each side keeps the order as given
    doc = _axis_doc(["g2", "y1", "s2", "g1", "s1", "y2"])
    check("fixture: mixed axis document is valid", rr.validate(doc), [])
    page = _html(doc)
    # written by hand: strengths inside the strong side, gaps inside the weak side, and the axis closes straight after the sides
    check("axis links split by group", '<div class="axis"><h3>axis one</h3><div class="sides">'
          '<div class="side strong"><b>Reliably gets right</b>strong side'
          '<div class="links"><h4>Related patterns</h4><a href="#s2">name s2</a><br><a href="#s1">name s1</a></div></div>'
          '<div class="side weak"><b>Reliably misses</b>weak side'
          '<div class="links"><h4>Related patterns</h4><a href="#g2">name g2</a><br><a href="#g1">name g1</a></div></div>'
          '</div></div>' in page, True)
    check("one links block per side", page.count('<div class="links">'), 2)

    page = _html(_axis_doc(["s1"]))
    # written by hand: only the strong side has an id, so the weak side holds no links
    check("a group with no ids emits no links", '<div class="side strong"><b>Reliably gets right</b>strong side'
          '<div class="links"><h4>Related patterns</h4><a href="#s1">name s1</a></div></div>'
          '<div class="side weak"><b>Reliably misses</b>weak side</div></div></div>' in page, True)
    check("only the strong side has links", (page.count('<div class="links">'), "<h4>Ways of working</h4>" in page),
          (1, False))

    for name, doc in (("no patterns key", _axis_doc()), ("empty patterns", _axis_doc([]))):
        page = _html(doc)
        # written by hand: both sides hold only their label and text
        check(name + ": axis without patterns emits no links", '<div class="axis"><h3>axis one</h3><div class="sides">'
              '<div class="side strong"><b>Reliably gets right</b>strong side</div>'
              '<div class="side weak"><b>Reliably misses</b>weak side</div></div></div>' in page, True)
        check(name + ": no links block", '<div class="links">' in page, False)

    # validate rejects an unknown id, so the page is rendered directly
    doc = _axis_doc(["s1", "nope", "g1"])
    page = rr.render_html(doc, rr.labels_for(doc))
    check("an id in no group is dropped",
          ('<div class="links"><h4>Related patterns</h4><a href="#s1">name s1</a></div>' in page,
           '<div class="links"><h4>Related patterns</h4><a href="#g1">name g1</a></div>' in page,
           "nope" in page), (True, True, False))
    doc = _axis_doc(["nope"])
    check("only an id in no group emits no links", '<div class="links">' in rr.render_html(doc, rr.labels_for(doc)),
          False)

    m, e = _markup, _escaped
    page = _html(_axis_doc(["s1", "g1", "y1"], labels={"related": m("label-related"), "styles": m("label-styles")}))
    check("overridden related label heads both sides",
          page.count('<div class="links"><h4>%s</h4>' % e("label-related")), 2)
    summary = _section(page, "Summary")
    axes = summary[summary.index('<div class="axes">'):]
    # the styles label still heads its own section, so its absence from the axes is not a label that went missing
    check("overridden styles label kept out of the axes, heading the styles section",
          (e("label-styles") in axes, '<section class="styles"><h2>%s</h2>' % e("label-styles") in page), (False, True))
    check("no raw script tag from the link labels", "<script" in page, False)


def _axes_block(page):
    # the styles table names every style too, so an absence is only meaningful inside the axes block
    summary = _section(page, "Summary")
    return summary[summary.index('<div class="axes">'):]


def test_axis_omits_styles():
    doc = _axis_doc(["y1", "y2"])
    check("fixture: styles-only axis document is valid", rr.validate(doc), [])
    page = _html(doc)
    axes = _axes_block(page)
    # written by hand: a style belongs to neither side, so both sides hold only their label and text
    check("styles-only axis block holds the bare sides", axes,
          '<div class="axes"><div class="axis"><h3>axis one</h3><div class="sides">'
          '<div class="side strong"><b>Reliably gets right</b>strong side</div>'
          '<div class="side weak"><b>Reliably misses</b>weak side</div></div></div></div>')
    check("styles-only axis: no links block in the axes", '<div class="links">' in axes, False)
    styles = _section(page, "Ways of working")
    for pid in ("y1", "y2"):
        # the style still renders in its own table, so its absence from the axes is not a pattern that went missing
        check("styles-only axis: name %s absent from the axes, present in the styles section" % pid,
              ("name " + pid in axes, '<th scope="row" id="%s">name %s</th>' % (pid, pid) in styles), (False, True))

    doc = _axis_doc(["g2", "y1", "s2", "g1", "s1", "y2"])
    check("fixture: mixed axis document is valid", rr.validate(doc), [])
    page = _html(doc)
    axes = _axes_block(page)
    styles = _section(page, "Ways of working")
    for pid in ("y1", "y2"):
        check("mixed axis: %s absent from the axes, present in the styles section" % pid,
              ("name " + pid in axes, 'href="#%s"' % pid in axes,
               '<th scope="row" id="%s">name %s</th>' % (pid, pid) in styles), (False, False, True))
    # written by hand: every strength and gap id the axis names, each as its own link
    for pid in ("s1", "s2", "g1", "g2"):
        check("mixed axis: %s linked in the axes" % pid, '<a href="#%s">name %s</a>' % (pid, pid) in axes, True)


def _css_block(style, opener):
    """The block that opens at opener, up to its matching closing brace, or None when opener is absent."""
    start = style.find(opener)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(style)):
        if style[i] == "{":
            depth += 1
        elif style[i] == "}":
            depth -= 1
            if depth == 0:
                return style[start:i + 1]
    return None


def test_axis_frame_css():
    style = _style(_html(_doc()))
    # written by hand from the frame every card takes, the axis among them, and the grid that stacks the axis cards
    base = "section, .lead, .axis, figure.flow { border:1px solid var(--rule); border-radius:12px; padding:24px 28px 28px; }"
    narrow = "section, .lead, .axis, figure.flow { padding:16px 14px 18px; }"
    narrow_summary = "section.summary { padding:24px 0 0; }"
    check("shared card frame rule", base in style, True)
    check("axes grid rule", ".axes { display:grid; gap:20px; margin:20px 0 0; }" in style, True)

    opener = "@media (max-width:760px) {"
    block = _css_block(style, opener)
    # the slice must be the media block alone, or a rule after it would pass as inside it
    check("narrow media block sliced to its closing brace",
          (block is not None and block.startswith(opener), block is not None and block.endswith("min-width:640px; } }")),
          (True, True))
    check("narrower card padding inside the narrow media block", block is not None and narrow in block, True)
    check("narrower card padding written once", style.count(narrow), 1)
    # the two rules have the same specificity, so the narrow one only wins by coming later
    base_at, narrow_at = style.find(base), style.find(narrow)
    check("narrow card rule after the base card rule", (base_at >= 0, narrow_at > base_at), (True, True))

    # the base summary rule outranks the shared narrow selector, so this is the rule that shrinks the summary,
    # and it wins over the base summary rule only by coming later
    check("narrower summary padding inside the narrow media block", block is not None and narrow_summary in block, True)
    summary_at, narrow_summary_at = style.find("section.summary {"), style.find(narrow_summary)
    check("narrow summary rule after the base summary rule", (summary_at >= 0, narrow_summary_at > summary_at),
          (True, True))


def test_no_line_measure():
    # a comment can name a selector or a declaration, so comments are split off before the rules are read
    css = re.sub(r"/\*.*?\*/", "", _style(_html(_doc())), flags=re.S)
    # only innermost blocks match, so a rule inside a media query is read as its own selector and declarations
    rules = [(sel.strip(), decl) for sel, decl in re.findall(r"([^{}]*)\{([^{}]*)\}", css)]
    names = (".summary p", ".axis p", "figcaption", ".scopeline")
    check("every text selector found by the rule parse", [any(n in sel for sel, _ in rules) for n in names],
          [True] * len(names))
    # checked rule by rule, because main keeps a max-width and the media queries name one
    check("no rule for the text selectors declares max-width",
          [sel for sel, decl in rules if any(n in sel for n in names) and "max-width" in decl], [])
    check("no rule keys on :lang(", ":lang(" in css, False)

    # written by hand: each text selector keeps its rule, with no measure in it
    for name, text in ((".summary p", ".summary p { margin:0 0 14px; font-size:17px; }"),
                       (".axis p", ".axis p { margin:0 0 14px; }"),
                       ("figcaption", "figcaption { font-size:16px; font-weight:600; line-height:1.45; margin:0 0 14px; }"),
                       (".scopeline", ".scopeline { color:var(--muted); font-size:14px; margin:0; }")):
        check("rule without a measure kept for %s" % name, text in css, True)


def test_document_lang_attribute():
    doc = _doc(language="zh-TW")
    check("fixture: zh-TW document is valid", rr.validate(doc), [])
    page = _html(doc)
    # the browser reads the lang attribute on html to choose the fonts the text is drawn in
    check("zh-TW document carries lang=zh-TW on html",
          ('<html lang="zh-TW">' in page, 'lang="en"' in page), (True, False))
    # the stylesheet is the same for every document, so the attribute is the only thing that differs
    check("zh-TW document has the same stylesheet as an English one", _style(page), _style(_html(_doc())))

    check("full document carries lang=en on html", '<html lang="en">' in _html(_full_doc()), True)
    minimal = _doc()
    check("fixture: minimal document has no language key", "language" in minimal, False)
    check("document without a language carries lang=en on html", '<html lang="en">' in _html(minimal), True)


# ----- section and table classes -------------------------------------------------

def test_section_and_table_classes():
    page = _html(_full_doc())
    sections = re.findall(r'<section class="([^"]*)">', page)
    # written by hand: every section the full document renders, in page order
    check("each section carries its class",
          sections, ["summary", "strengths", "gaps", "styles", "implications", "scope", "appendix"])
    for cls, heading in (("summary", "Summary"), ("strengths", "Strengths"), ("gaps", "Gaps"),
                         ("styles", "Ways of working"), ("implications", "What this means for your work"),
                         ("scope", "Scope"), ("appendix", "Appendix")):
        # the summary's heading opens its lead card, so the heading does not follow the section tag directly
        opener = '<section class="summary"><div class="lead">' if cls == "summary" else '<section class="%s">' % cls
        check("%s class on the section of its heading" % cls, '%s<h2>%s</h2>' % (opener, heading) in page, True)
    # written by hand: the three pattern tables, then the implications and the scope tables
    tables = re.findall(r'<table class="([^"]*)">', page)
    check("each table carries its class", tables, ["patterns", "patterns", "patterns styles", "implications", "scope"])

    # the class names the group, not its label, so a translated heading keeps its icon and its column widths
    doc = _full_doc()
    doc["labels"] = {"strengths": "\u512a\u9ede"}
    check("class kept under an overridden heading", '<section class="strengths"><h2>\u512a\u9ede</h2>' in _html(doc),
          True)

    style = page[page.index("<style>"):page.index("</style>")]
    # an icon or a column width keyed to a class the page never uses would silently apply to nothing
    for cls in sections:
        check("icon rule for section.%s" % cls, ('section.%s { --icon:url("data:' % cls) in style, True)
    for cls in sorted(set(" ".join(tables).split())):
        check("column rule for table.%s" % cls, ("table.%s " % cls) in style, True)


# ----- cards and the lead --------------------------------------------------------

def test_card_rules_and_elements():
    page = _html(_full_doc())
    style = _style(page)
    check("sections keep their own top margin", "section { margin-top:24px; }" in style, True)
    # a card selector no rendered element matches would frame nothing, so each one is found in the page
    check("each card selector matches an element of the full page",
          ("<section" in page, '<div class="lead">' in page, '<div class="axis">' in page,
           '<figure class="flow">' in page),
          (True, True, True, True))


def test_summary_not_a_card():
    style = _style(_html(_doc()))
    # written by hand: the summary drops the card frame and keeps only a top rule
    summary = ("section.summary { margin-top:36px; padding:32px 0 0; border:0; "
               "border-top:1px solid var(--rule); border-radius:0; }")
    check("base summary rule unframes the section", summary in style, True)
    # specificity already lets it win, and coming later keeps it winning if the selector is ever loosened
    shared_at, summary_at = style.find("section, .lead, .axis, figure.flow { border:"), style.find(summary)
    check("base summary rule after the shared card rule", (shared_at >= 0, summary_at > shared_at), (True, True))


def test_lead_wrapper():
    page = _html(_full_doc())
    check("one lead on the page", page.count('<div class="lead">'), 1)
    check("summary section opens with the lead and its heading",
          page[page.index('<section class="summary">'):].startswith('<section class="summary"><div class="lead"><h2>'),
          True)
    start = page.index('<div class="lead">')
    # the lead holds no nested div, so its first closing tag is its own
    end = page.index("</div>", start)
    # written by hand: the heading and the two paragraphs of the full document's summary text, and nothing else
    check("lead holds the heading and the summary text only", page[start:end],
          '<div class="lead"><h2>Summary</h2><p>first paragraph</p><p>second paragraph</p>')
    # the axes and the figures are cards of their own, so they must follow the lead rather than sit in it
    check("lead closes before the axes block", page[end:].startswith('</div><div class="axes">'), True)
    check("figure after the lead closes", page.index('<figure class="flow">') > end, True)
    check("last lead paragraph drops its bottom margin", ".lead p:last-child { margin-bottom:0; }" in _style(page), True)

    doc = _doc()
    check("fixture: minimal document has no axes and no diagrams",
          ("axes" in doc["summary"], "diagrams" in doc), (False, False))
    # written by hand: the minimal summary text, with the lead closed and the section closed straight after it
    check("text-only summary still wrapped in the lead",
          '<section class="summary"><div class="lead"><h2>Summary</h2><p>the summary</p></div></section>' in _html(doc),
          True)


def test_last_row_table_rule():
    style = _style(_html(_doc()))
    # written by hand: the last row gives up the rule and the padding the card's own border replaces
    rule = "tbody tr:last-child > th, tbody tr:last-child > td { border-bottom:0; padding-bottom:0; }"
    check("last-row rule written once", style.count(rule), 1)
    # both rules have the same declarations to fight over, so the last-row one must come later to win
    base_at, rule_at = style.find("\nth, td {"), style.find(rule)
    check("last-row rule after the base cell rule", (base_at >= 0, rule_at > base_at), (True, True))


def test_heading_rules_reach_lead():
    page = _html(_full_doc())
    style = _style(page)
    # written by hand: the heading layout and its icon each name the lead's heading next to a section's
    check("heading layout rule names the lead heading", "section > h2, .lead > h2 { display:flex;" in style, True)
    check("heading icon rule names the lead heading", 'section > h2::before, .lead > h2::before { content:"";' in style,
          True)
    # the summary heading sits in the lead, so a section-only selector would leave it bare
    check("summary heading is a child of the lead, not of its section",
          ('<div class="lead"><h2>Summary</h2>' in page, '<section class="summary"><h2>' in page), (True, False))


def test_figure_margins():
    style = _style(_html(_doc()))
    # written by hand: the bare figure margin, then the flow figure's own
    check("figure base and flow margins", "figure { margin:0; } figure.flow { margin:20px 0 0; }" in style, True)
    # the class selector outranks the bare figure rule, so the timeline keeps its margin
    check("timeline figure keeps its margin", "figure.timeline { margin:24px 0 0;" in style, True)


# ----- timeline ------------------------------------------------------------------

def _tl_repo(name, start, end, **extra):
    r = {"name": name, "from": start, "to": end}
    r.update(extra)
    return r


def _timeline(*repositories, **extra):
    t = {"repositories": list(repositories)}
    t.update(extra)
    return t


def _tl_full():
    """A timeline with every part over 2026-01-01 to 2026-04-11, which is exactly 100 days,
    so a day's offset from the first date is its position in percent."""
    return _timeline(_tl_repo("repo-a", "2026-01-01", "2026-04-11", ai_from="2026-01-21", recent_from="2026-03-02"),
                     prompts={"from": "2026-03-22", "to": "2026-04-11"})


def _tl_html(timeline, labels=None):
    return rr.timeline_html(timeline, rr.labels_for({"labels": labels}))


def _seg(kind, left, width, start, end):
    return '<span class="tl-seg %s" style="left:%s%%;width:%s%%" title="%s \u2013 %s"></span>' % (
        kind, left, width, start, end)


def _tl_track(figure, name):
    """The track of the row whose name cell holds name, or None when no row has that name."""
    head = '<div class="tl-name">%s</div><div class="tl-track">' % name
    start = figure.find(head)
    if start < 0:
        return None
    start += len(head)
    return figure[start:figure.index("</div>", start)]


def _tl_names(figure):
    return re.findall(r'<div class="tl-name">([^<]*)</div>', figure)


def _tl_ticks(figure):
    """Each interior tick as (its extra class, its left, its label), in page order.
    The start and end ticks carry no left, so they are not matched."""
    return [(minor.strip(), left, label) for minor, left, label in
            re.findall(r'<span class="tl-tick( minor)?" style="left:([^"]*)%">([^<]*)</span>', figure)]


def _tl_legend(figure):
    head = '<figcaption class="tl-legend">'
    start = figure.index(head) + len(head)
    return figure[start:figure.index("</figcaption>", start)]


# written by hand from _tl_full, with every position a day offset from 2026-01-01 over the 100 days:
# thin from day 0 to day 60 (2026-03-02), close from day 60 to day 100, AI on day 20 (2026-01-21),
# then the prompts row from day 80 (2026-03-22) to day 100,
# then the axis with the first of February on day 31, and the first of March on day 59 as the second tick, so minor,
# while the first of April falls on day 90, past the edge, and is dropped
_TL_FULL_FIGURE = (
    '<figure class="timeline">'
    '<div class="tl-row"><div class="tl-name">repo-a</div><div class="tl-track">'
    '<span class="tl-seg thin" style="left:0.00%;width:60.00%" title="2026-01-01 \u2013 2026-03-02"></span>'
    '<span class="tl-seg close" style="left:60.00%;width:40.00%" title="2026-03-02 \u2013 2026-04-11"></span>'
    '<span class="tl-ai" style="left:20.00%" title="AI shows up: 2026-01-21"></span>'
    '</div></div>'
    '<div class="tl-row"><div class="tl-name">Prompts</div><div class="tl-track">'
    '<span class="tl-seg prompts" style="left:80.00%;width:20.00%" title="2026-03-22 \u2013 2026-04-11"></span>'
    '</div></div>'
    '<div class="tl-row tl-axis"><div class="tl-name"></div><div class="tl-track">'
    '<span class="tl-tick start">2026-01-01</span><span class="tl-tick end">2026-04-11</span>'
    '<span class="tl-tick" style="left:31.00%">2026-02</span>'
    '<span class="tl-tick minor" style="left:59.00%">2026-03</span>'
    '</div></div>'
    '<figcaption class="tl-legend">'
    '<span class="tl-key"><i class="tl-seg thin"></i>Read thinly</span>'
    '<span class="tl-key"><i class="tl-seg close"></i>Read closely</span>'
    '<span class="tl-key"><i class="tl-ai"></i>AI shows up</span>'
    '<span class="tl-key"><i class="tl-seg prompts"></i>Prompts</span>'
    '</figcaption></figure>')


def test_validation_timeline_shape():
    check("fixture: full timeline is valid", rr.validate(_doc(timeline=_tl_full())), [])
    check("fixture: full document with a timeline is valid", rr.validate(dict(_full_doc(), timeline=_tl_full())), [])
    check("no timeline key valid", rr.validate(_doc()), [])
    check("null timeline valid", rr.validate(_doc(timeline=None)), [])

    repo = _tl_repo("repo-a", "2026-01-01", "2026-04-11")
    _invalid("timeline that is a list", _doc(timeline=[repo]), "'timeline' is not an object")
    _invalid("timeline that is a string", _doc(timeline="2026"), "'timeline' is not an object")
    # an empty object is present, so it is checked rather than taken as no timeline
    _invalid("timeline without repositories", _doc(timeline={}), "timeline: 'repositories' is missing or empty")
    _invalid("timeline with empty repositories", _doc(timeline=_timeline()),
             "timeline: 'repositories' is missing or empty")
    _invalid("repositories that is not a list", _doc(timeline={"repositories": repo}),
             "timeline: 'repositories' is missing or empty")
    _invalid("repository that is not an object", _doc(timeline=_timeline("repo-a")),
             "timeline.repositories[0]: a repository must be an object")

    for name, value in (("missing", _without(repo, "name")), ("empty", dict(repo, name="")),
                        ("not a string", dict(repo, name=5))):
        _invalid("repository name " + name, _doc(timeline=_timeline(value)),
                 "timeline.repositories[0]: 'name' is missing or empty")
    # a different index, so a path built from the wrong counter names the wrong repository
    _invalid("second repository without a name", _doc(timeline=_timeline(repo, _without(repo, "name"))),
             "timeline.repositories[1]: 'name' is missing or empty")


def test_validation_timeline_dates():
    where = "timeline.repositories[0]"
    repo = _tl_repo("repo-a", "2026-01-01", "2026-04-11")
    # written by hand: a compact form fromisoformat can take, a month and a day without their leading zeros,
    # a day February 2026 does not have, a number, and an empty string
    bad = ("20260102", "2026-2-3", "2026-02-30", 20260102, "")
    for key in ("from", "to"):
        _invalid("repository without %s" % key, _doc(timeline=_timeline(_without(repo, key))),
                 "%s: '%s' is not a date written YYYY-MM-DD" % (where, key))
        for value in bad:
            _invalid("repository %s %r" % (key, value), _doc(timeline=_timeline(dict(repo, **{key: value}))),
                     "%s: '%s' is not a date written YYYY-MM-DD" % (where, key))

    _invalid("repository from after to", _doc(timeline=_timeline(_tl_repo("repo-a", "2026-04-12", "2026-04-11"))),
             where + ": 'from' is after 'to'")
    # the same day on both ends is a span of one day, the boundary that passes
    check("repository from equal to to valid",
          rr.validate(_doc(timeline=_timeline(_tl_repo("repo-a", "2026-04-11", "2026-04-11")))), [])

    check("no ai_from valid", rr.validate(_doc(timeline=_timeline(repo))), [])
    check("null ai_from valid", rr.validate(_doc(timeline=_timeline(dict(repo, ai_from=None)))), [])
    for value in bad:
        _invalid("ai_from %r" % (value,), _doc(timeline=_timeline(dict(repo, ai_from=value))),
                 where + ": 'ai_from' is not a date written YYYY-MM-DD")

    check("no recent_from valid", rr.validate(_doc(timeline=_timeline(repo))), [])
    # recent_from is the date of one of the repository's own changes, so a day before its span cannot be one
    _invalid("recent_from before from", _doc(timeline=_timeline(dict(repo, recent_from="2025-12-01"))),
             where + ": 'recent_from' is outside 'from' to 'to'")
    _invalid("recent_from after to", _doc(timeline=_timeline(dict(repo, recent_from="2026-05-01"))),
             where + ": 'recent_from' is outside 'from' to 'to'")
    # the span's own ends are dates of changes read, so both bounds are inclusive
    check("recent_from equal to from valid", rr.validate(_doc(timeline=_timeline(dict(repo, recent_from="2026-01-01")))), [])
    check("recent_from equal to to valid", rr.validate(_doc(timeline=_timeline(dict(repo, recent_from="2026-04-11")))), [])
    check("null recent_from valid", rr.validate(_doc(timeline=_timeline(dict(repo, recent_from=None)))), [])
    for value in bad:
        _invalid("recent_from %r" % (value,), _doc(timeline=_timeline(dict(repo, recent_from=value))),
                 where + ": 'recent_from' is not a date written YYYY-MM-DD")
    # a reversed span has no inside, so every recent_from would fall outside it unless the check is skipped,
    # and one defect should give one message
    for value in ("2026-04-01", "2026-04-11", "2026-04-12", "2026-05-01"):
        _invalid("recent_from %s on a reversed span" % value,
                 _doc(timeline=_timeline(_tl_repo("repo-a", "2026-04-12", "2026-04-11", recent_from=value))),
                 where + ": 'from' is after 'to'")

    # a well-formed date inside the repository's span, so the only thing wrong is where it sits
    _invalid("recent_from on the timeline itself", _doc(timeline=_timeline(repo, recent_from="2026-03-02")),
             "timeline: 'recent_from' belongs on each repository")
    check("null recent_from on the timeline itself valid", rr.validate(_doc(timeline=_timeline(repo, recent_from=None))), [])

    prompts = {"from": "2026-03-22", "to": "2026-04-11"}
    check("no prompts valid", rr.validate(_doc(timeline=_timeline(repo))), [])
    check("null prompts valid", rr.validate(_doc(timeline=_timeline(repo, prompts=None))), [])
    _invalid("prompts that is a string", _doc(timeline=_timeline(repo, prompts="2026-03-22")),
             "timeline: 'prompts' is not an object")
    _invalid("prompts that is a list", _doc(timeline=_timeline(repo, prompts=["2026-03-22", "2026-04-11"])),
             "timeline: 'prompts' is not an object")
    for key in ("from", "to"):
        _invalid("prompts without %s" % key, _doc(timeline=_timeline(repo, prompts=_without(prompts, key))),
                 "timeline.prompts: '%s' is not a date written YYYY-MM-DD" % key)
        _invalid("prompts %s on an impossible day" % key,
                 _doc(timeline=_timeline(repo, prompts=dict(prompts, **{key: "2026-02-30"}))),
                 "timeline.prompts: '%s' is not a date written YYYY-MM-DD" % key)
    _invalid("prompts from after to", _doc(timeline=_timeline(repo, prompts={"from": "2026-04-12", "to": "2026-04-11"})),
             "timeline.prompts: 'from' is after 'to'")
    check("prompts from equal to to valid",
          rr.validate(_doc(timeline=_timeline(repo, prompts={"from": "2026-04-11", "to": "2026-04-11"}))), [])


def test_validation_timeline_problem_order():
    timeline = _timeline(_tl_repo("repo-a", "2026-04-12", "2026-04-11"), 7,
                         {"name": "", "from": "2026-1-1", "to": "2026-02-30", "ai_from": "soon"},
                         recent_from="2026-03-01", prompts={"from": "2026-04-11", "to": "2026-04-01"})
    doc = _without(_doc(diagrams=[{"lanes": [_lane([_step("a")])]}], timeline=timeline), "scope")
    # written by hand in the order the schema is walked: the diagrams, then each repository field by field,
    # then the recent_from the timeline itself carries, then the prompts, then the scope
    _invalid_all("timeline problems between the diagrams and the scope", doc, [
        "diagrams[0]: 'caption' is missing or empty",
        "timeline.repositories[0]: 'from' is after 'to'",
        "timeline.repositories[1]: a repository must be an object",
        "timeline.repositories[2]: 'name' is missing or empty",
        "timeline.repositories[2]: 'from' is not a date written YYYY-MM-DD",
        "timeline.repositories[2]: 'to' is not a date written YYYY-MM-DD",
        "timeline.repositories[2]: 'ai_from' is not a date written YYYY-MM-DD",
        "timeline: 'recent_from' belongs on each repository",
        "timeline.prompts: 'from' is after 'to'",
        "document: 'scope' is missing or empty"])

    # on the second repository, so a path built from the wrong counter names the wrong one
    timeline = _timeline(_tl_repo("repo-a", "2026-01-01", "2026-04-11"),
                         _tl_repo("repo-b", "2026-01-01", "2026-04-11", ai_from="soon", recent_from="2026-05-01"))
    # written by hand in the order the validator reads the fields: ai_from before recent_from
    _invalid_all("a repository's ai_from before its recent_from", _doc(timeline=timeline), [
        "timeline.repositories[1]: 'ai_from' is not a date written YYYY-MM-DD",
        "timeline.repositories[1]: 'recent_from' is outside 'from' to 'to'"])

    # both dates malformed, so the order of the two date checks shows,
    # where the well-formed recent_from above only shows the span check coming after both
    timeline = _timeline(_tl_repo("repo-a", "2026-01-01", "2026-04-11"),
                         _tl_repo("repo-b", "2026-01-01", "2026-04-11", ai_from="soon", recent_from="2026-02-30"))
    # written by hand in the order the validator reads the fields: ai_from before recent_from
    _invalid_all("a repository's malformed ai_from before its malformed recent_from", _doc(timeline=timeline), [
        "timeline.repositories[1]: 'ai_from' is not a date written YYYY-MM-DD",
        "timeline.repositories[1]: 'recent_from' is not a date written YYYY-MM-DD"])


def test_timeline_html_full():
    check("full timeline figure", rr.timeline_html(_tl_full(), rr.LABELS), _TL_FULL_FIGURE)
    check("English timeline labels",
          [rr.LABELS[k] for k in ("timeline_thin", "timeline_close", "timeline_ai", "timeline_prompts")],
          ["Read thinly", "Read closely", "AI shows up", "Prompts"])


def test_timeline_thin_close_split():
    repo = _tl_repo("repo-a", "2026-01-01", "2026-04-11")
    # written by hand over the 100 days from 2026-01-01, where 2026-03-02 is day 60.
    # recent_from never enters the domain, or these positions would shift
    for name, recent, want in (
            ("no recent_from draws one close bar", None,
             _seg("close", "0.00", "100.00", "2026-01-01", "2026-04-11")),
            ("recent_from inside the span draws thin then close", "2026-03-02",
             _seg("thin", "0.00", "60.00", "2026-01-01", "2026-03-02")
             + _seg("close", "60.00", "40.00", "2026-03-02", "2026-04-11")),
            ("recent_from on the last day draws only thin", "2026-04-11",
             _seg("thin", "0.00", "100.00", "2026-01-01", "2026-04-11")),
            ("recent_from on the first day draws only close", "2026-01-01",
             _seg("close", "0.00", "100.00", "2026-01-01", "2026-04-11"))):
        extra = {"recent_from": recent} if recent else {}
        figure = _tl_html(_timeline(dict(repo, **extra)))
        check(name, _tl_track(figure, "repo-a"), want)
        check(name + ": the axis still ends on the repository's dates",
              ('<span class="tl-tick start">2026-01-01</span>' in figure, '<span class="tl-tick end">2026-04-11</span>' in figure),
              (True, True))

    # each repository carries its own recent_from, so each row is cut at its own boundary
    figure = _tl_html(_timeline(dict(repo, recent_from="2026-03-02"),
                                _tl_repo("repo-b", "2026-03-22", "2026-04-11", recent_from="2026-04-01"),
                                _tl_repo("repo-c", "2026-01-01", "2026-01-21")))
    # written by hand: repo-a is cut on day 60 (2026-03-02), repo-b runs from day 80 and is cut on day 90 (2026-04-01),
    # and repo-c, with no recent_from, is one close bar from day 0 to day 20
    check("each repository split by its own span", [_tl_track(figure, n) for n in ("repo-a", "repo-b", "repo-c")], [
        _seg("thin", "0.00", "60.00", "2026-01-01", "2026-03-02")
        + _seg("close", "60.00", "40.00", "2026-03-02", "2026-04-11"),
        _seg("thin", "80.00", "10.00", "2026-03-22", "2026-04-01")
        + _seg("close", "90.00", "10.00", "2026-04-01", "2026-04-11"),
        _seg("close", "0.00", "20.00", "2026-01-01", "2026-01-21")])
    check("a row per repository in document order, then the axis", _tl_names(figure), ["repo-a", "repo-b", "repo-c", ""])


def test_timeline_single_day():
    # a span of no days still has a one-day scale, so nothing is divided by zero
    figure = _tl_html(_timeline(_tl_repo("repo-a", "2026-04-11", "2026-04-11")))
    check("a one-day repository draws a zero-width close bar at the start",
          _tl_track(figure, "repo-a"), _seg("close", "0.00", "0.00", "2026-04-11", "2026-04-11"))
    check("a one-day axis holds only its two end ticks",
          ('<span class="tl-tick start">2026-04-11</span><span class="tl-tick end">2026-04-11</span></div>' in figure,
           _tl_ticks(figure)), (True, []))


def test_timeline_ai_marker():
    # written by hand: ai_from on day 0 moves the start of the domain back to it, so the bar starts on day 20
    figure = _tl_html(_timeline(_tl_repo("repo-a", "2026-01-21", "2026-04-11", ai_from="2026-01-01")))
    check("ai_from before the span extends the domain and marks its day", _tl_track(figure, "repo-a"),
          _seg("close", "20.00", "80.00", "2026-01-21", "2026-04-11")
          + '<span class="tl-ai" style="left:0.00%" title="AI shows up: 2026-01-01"></span>')
    check("the axis starts on ai_from", '<span class="tl-tick start">2026-01-01</span>' in figure, True)

    for name, repo in (("no ai_from", _tl_repo("repo-a", "2026-01-01", "2026-04-11")),
                       ("null ai_from", _tl_repo("repo-a", "2026-01-01", "2026-04-11", ai_from=None))):
        figure = _tl_html(_timeline(repo))
        check(name + " draws no marker and no key", "tl-ai" in figure, False)


def test_timeline_prompts_row():
    figure = _tl_html(_timeline(_tl_repo("repo-a", "2026-01-01", "2026-03-02"),
                                prompts={"from": "2026-03-22", "to": "2026-04-11"}))
    # written by hand: the prompts end on day 100, past the repository, so they set the end of the domain
    check("the prompts row holds one prompts segment", _tl_track(figure, "Prompts"),
          _seg("prompts", "80.00", "20.00", "2026-03-22", "2026-04-11"))
    check("the repository measured against the domain the prompts extend", _tl_track(figure, "repo-a"),
          _seg("close", "0.00", "60.00", "2026-01-01", "2026-03-02"))
    check("the axis ends on the last prompt", '<span class="tl-tick end">2026-04-11</span>' in figure, True)
    check("the prompts row follows every repository, before the axis", _tl_names(figure), ["repo-a", "Prompts", ""])

    figure = _tl_html(_timeline(_tl_repo("repo-a", "2026-01-01", "2026-04-11")))
    check("no prompts draws no prompts row", (_tl_track(figure, "Prompts"), "tl-seg prompts" in figure), (None, False))


def test_timeline_legend():
    check("the full legend in its fixed order", _tl_legend(_tl_html(_tl_full())), _tl_legend(_TL_FULL_FIGURE))
    repo = _tl_repo("repo-a", "2026-01-01", "2026-04-11")
    # written by hand: only the key for what the figure draws
    check("a close bar alone keys only close", _tl_legend(_tl_html(_timeline(repo))),
          '<span class="tl-key"><i class="tl-seg close"></i>Read closely</span>')
    check("a thin bar alone keys only thin", _tl_legend(_tl_html(_timeline(dict(repo, recent_from="2026-04-11")))),
          '<span class="tl-key"><i class="tl-seg thin"></i>Read thinly</span>')

    # the kinds are drawn close, then AI, then thin, then prompts, so a legend in drawing order would differ
    figure = _tl_html(_timeline(_tl_repo("repo-a", "2026-03-22", "2026-04-11", ai_from="2026-04-01", recent_from="2026-03-22"),
                                _tl_repo("repo-b", "2026-01-01", "2026-03-22", recent_from="2026-03-22"),
                                prompts={"from": "2026-01-01", "to": "2026-01-21"}))
    check("fixture: close is drawn before thin", (_tl_track(figure, "repo-a").startswith('<span class="tl-seg close"'),
                                                  _tl_track(figure, "repo-b").startswith('<span class="tl-seg thin"')),
          (True, True))
    check("the legend keys thin, close, AI, then prompts whatever the drawing order", _tl_legend(figure),
          '<span class="tl-key"><i class="tl-seg thin"></i>Read thinly</span>'
          '<span class="tl-key"><i class="tl-seg close"></i>Read closely</span>'
          '<span class="tl-key"><i class="tl-ai"></i>AI shows up</span>'
          '<span class="tl-key"><i class="tl-seg prompts"></i>Prompts</span>')


def test_timeline_tick_steps():
    # written by hand from calendar day counts, each tick's day over the span's days,
    # at each boundary of the step: 8 and 9 months, 24 and 25, 60 and 61
    for months, end, labels in (
            (8, "2026-09-01", ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]),
            (9, "2026-10-01", ["2026-04", "2026-07"]),
            (24, "2028-01-01", ["2026-04", "2026-07", "2026-10", "2027-01", "2027-04", "2027-07", "2027-10"]),
            (25, "2028-02-01", ["2026-07", "2027-01", "2027-07"]),
            (60, "2031-01-01", ["2027-01", "2027-07", "2028-01", "2028-07", "2029-01", "2029-07", "2030-01"]),
            (61, "2031-02-01", ["2027", "2028", "2029", "2030"])):
        figure = _tl_html(_timeline(_tl_repo("repo-a", "2026-01-01", end)))
        check("%d months: interior tick labels" % months, [label for _, _, label in _tl_ticks(figure)], labels)

    # written by hand: 1857 days from 2026-01-01 to 2031-02-01, with each first of January on day 365, 730, 1096, 1461,
    # and the one in 2031 on day 1826, past the edge.
    # the first is outside the middle, the second and fourth are odd-numbered, and only the third is a major tick
    check("a yearly axis in full", rr.timeline_html(_timeline(_tl_repo("repo-a", "2026-01-01", "2031-02-01")), rr.LABELS)
          .split('<div class="tl-row tl-axis">')[1].split("<figcaption")[0],
          '<div class="tl-name"></div><div class="tl-track">'
          '<span class="tl-tick start">2026-01-01</span><span class="tl-tick end">2031-02-01</span>'
          '<span class="tl-tick minor" style="left:19.66%">2027</span>'
          '<span class="tl-tick minor" style="left:39.31%">2028</span>'
          '<span class="tl-tick" style="left:59.02%">2029</span>'
          '<span class="tl-tick minor" style="left:78.68%">2030</span>'
          '</div></div>')


def test_timeline_tick_edges_and_minor():
    # every span here is 100 days, so a first of the month lands on its day offset as a percentage.
    # written by hand from calendar day counts
    for name, start, end, ticks in (
            # the first of February on exactly 12 is dropped, day 40 is a major tick, day 71 the second, so minor
            ("a tick on 12 dropped", "2026-01-20", "2026-04-30", [("", "40.00", "2026-03"), ("minor", "71.00", "2026-04")]),
            # 13 is kept, and 72 is not inside the middle, so the third tick is minor although it is even-numbered
            ("a tick on 13 kept, and one on 72 minor", "2026-01-19", "2026-04-29",
             [("minor", "13.00", "2026-02"), ("minor", "41.00", "2026-03"), ("minor", "72.00", "2026-04")]),
            # 28 is not inside the middle, so the first tick is minor, and 87 is kept but minor
            ("a tick on 28 minor, and one on 87 kept", "2026-01-04", "2026-04-14",
             [("minor", "28.00", "2026-02"), ("minor", "56.00", "2026-03"), ("minor", "87.00", "2026-04")]),
            # the first of June on exactly 88 is dropped
            ("a tick on 88 dropped", "2026-03-05", "2026-06-13", [("minor", "27.00", "2026-04"), ("minor", "57.00", "2026-05")])):
        check(name, _tl_ticks(_tl_html(_timeline(_tl_repo("repo-a", start, end)))), ticks)


def test_timeline_escaping():
    m, e = _markup, _escaped
    labels = _all_labels({"timeline_thin": m("thin"), "timeline_close": m("close"), "timeline_ai": m("ai"),
                          "timeline_prompts": m("prompts")})
    doc = _doc(timeline=_timeline(_tl_repo(m("repo"), "2026-01-01", "2026-04-11", ai_from="2026-01-21",
                                           recent_from="2026-03-02"),
                                  prompts={"from": "2026-03-22", "to": "2026-04-11"}),
               labels=labels)
    check("fixture: markup timeline document is valid", rr.validate(doc), [])
    page = _html(doc)
    check("repository name escaped", '<div class="tl-name">%s</div>' % e("repo") in page, True)
    check("prompts label escaped as a row name", '<div class="tl-name">%s</div>' % e("prompts") in page, True)
    check("AI label escaped in the marker title", 'title="%s: 2026-01-21"' % e("ai") in page, True)
    # written by hand: each key's swatch, then its escaped label
    check("every legend label escaped", _tl_legend(page),
          '<span class="tl-key"><i class="tl-seg thin"></i>%s</span>'
          '<span class="tl-key"><i class="tl-seg close"></i>%s</span>'
          '<span class="tl-key"><i class="tl-ai"></i>%s</span>'
          '<span class="tl-key"><i class="tl-seg prompts"></i>%s</span>' % (e("thin"), e("close"), e("ai"), e("prompts")))
    check("no raw script tag anywhere", "<script" in page, False)
    # the value runs to the quote that closes the attribute, so a raw quote inside it would show up here
    values = re.findall(r'<span class="tl-ai" style="[^"]*" title="(.*?)"></span>', page)
    check("a marker title attribute found", len(values) > 0, True)
    check("no raw quote inside a marker title attribute", [v for v in values if '"' in v], [])


def test_timeline_placement():
    with_timeline = dict(_full_doc(), timeline=_tl_full())
    page = _html(with_timeline)
    check("figure between the scope line and the summary section",
          '<p class="scopeline">repo-a over one year</p>' + _TL_FULL_FIGURE + '<section class="summary">' in page, True)
    check("figure rendered once", page.count('<figure class="timeline">'), 1)
    # taking the figure out leaves exactly the page a document without a timeline renders
    check("the rest of the page as without a timeline", page.replace(_TL_FULL_FIGURE, "", 1), _html(_full_doc()))
    check("no scope line puts the figure under the title",
          "<h1>whoami</h1>" + _TL_FULL_FIGURE + '<section class="summary">' in _html(_doc(timeline=_tl_full())), True)

    check("no timeline renders no figure", '<figure class="timeline">' in _html(_full_doc()), False)
    check("a null timeline renders as none", _html(dict(_full_doc(), timeline=None)), _html(_full_doc()))

    code, out, err, out_path = _main(_doc(timeline=_tl_full()))
    check("a valid timeline exits 0", (code, err), (0, ""))
    with open(out_path, encoding="utf-8") as f:
        check("the written page holds the figure", _TL_FULL_FIGURE in f.read(), True)


def test_timeline_labels_localised():
    labels = {"timeline_thin": "\u8f15\u8b80", "timeline_close": "\u7d30\u8b80", "timeline_ai": "AI \u51fa\u73fe",
              "timeline_prompts": "\u63d0\u793a"}
    page = _html(_doc(timeline=_tl_full(), labels=labels))
    # written by hand: each key's swatch, then its localised label, in the fixed order
    check("localised legend", _tl_legend(page),
          '<span class="tl-key"><i class="tl-seg thin"></i>\u8f15\u8b80</span>'
          '<span class="tl-key"><i class="tl-seg close"></i>\u7d30\u8b80</span>'
          '<span class="tl-key"><i class="tl-ai"></i>AI \u51fa\u73fe</span>'
          '<span class="tl-key"><i class="tl-seg prompts"></i>\u63d0\u793a</span>')
    check("localised prompts row", _tl_track(page, "\u63d0\u793a"),
          _seg("prompts", "80.00", "20.00", "2026-03-22", "2026-04-11"))
    check("localised AI marker title", 'title="AI \u51fa\u73fe: 2026-01-21"' in page, True)
    check("English timeline labels gone",
          [s for s in ("Read thinly", "Read closely", "AI shows up", ">Prompts<") if s in page], [])


def test_timeline_absent_from_markdown():
    # the Markdown summary leaves the timeline to the scope line
    for name, doc in (("minimal", _doc()), ("full", _full_doc())):
        with_timeline = dict(doc, timeline=_tl_full())
        check(name + ": Markdown unchanged by a timeline", _md_lines(with_timeline), _md_lines(doc))
        text = "\n".join(_md_lines(with_timeline))
        check(name + ": no timeline date or label in the Markdown",
              [s for s in ("2026-03-02", "2026-01-21", "Read thinly", "Read closely", "AI shows up") if s in text], [])


# ----- visible text and the plain-language checks --------------------------------

def _visible_pattern(pid):
    """A pattern filling every field visible_text reads, each value naming the pattern,
    and every evidence field holding the word hidden, which visible_text must never yield."""
    return _pattern(pid, gives="gives " + pid, costs="costs " + pid,
                    confidence={"level": "depends", "on": "on " + pid},
                    instances=[{"text": "hidden instance", "ref": "hidden ref"}, "hidden bare instance"],
                    exceptions=[{"text": "hidden exception", "ref": "hidden exception ref"}],
                    calibration="hidden calibration", checked="hidden checked")


def _visible_doc():
    """A valid document filling every field visible_text reads, with a different count at each level,
    so a path built from the wrong counter names the wrong place.
    Every field visible_text must leave out holds the word hidden."""
    return {
        "language": "hidden language",
        "title": "the title",
        "scope_line": "the scope line",
        "labels": _all_labels({"summary": "hidden label"}),
        "summary": {"text": "the summary",
                    "axes": [{"name": "axis 0", "description": "axis 0 description",
                              "strong": "axis 0 strong", "weak": "axis 0 weak", "patterns": ["s1"]},
                             {"name": "axis 1", "description": "axis 1 description",
                              "strong": "axis 1 strong", "weak": "axis 1 weak"}]},
        "diagrams": [{"caption": "caption 0",
                      "lanes": [{"title": "lane 0.0",
                                 "steps": [{"text": "step 0.0.0", "note": "note 0.0.0", "tone": "strong",
                                            "detail": ["detail 0.0.0 a", "detail 0.0.0 b"]},
                                           {"text": "step 0.0.1"}]}]},
                     {"caption": "caption 1",
                      "lanes": [{"title": "lane 1.0", "steps": [{"text": "step 1.0.0"}]},
                                {"title": "lane 1.1",
                                 "steps": [{"text": "step 1.1.0", "detail": ["detail 1.1.0"]},
                                           {"text": "step 1.1.1", "note": "note 1.1.1"}]}]}],
        "strengths": [_visible_pattern("s1")],
        "gaps": [_visible_pattern("g1"), _visible_pattern("g2")],
        "styles": [_visible_pattern("y1")],
        "implications": [{"area": "area 0", "meaning": "meaning 0", "patterns": ["g1"]},
                         {"area": "area 1", "meaning": "meaning 1"}],
        "scope": {"hidden scope key": "hidden scope value", "periods": ["hidden period"]},
        "dissolved": [{"name": "hidden dissolved", "evidence": "hidden evidence"}],
        "events": [{"text": "hidden event", "ref": "hidden event ref"}],
        "timeline": _timeline(_tl_repo("hidden repository", "2026-01-01", "2026-04-11")),
    }


def test_visible_text_walk():
    doc = _visible_doc()
    check("fixture: visible text document is valid", rr.validate(doc), [])
    got = rr.visible_text(doc)
    # written by hand in walk order: the title and the scope line, the summary and each axis,
    # each diagram's caption, lane title, and each step's text, note, then details,
    # then every pattern group, then the implications
    check("every visible field in walk order", got, [
        ("document", "the title"), ("document", "the scope line"),
        ("summary", "the summary"),
        ("summary.axes[0]", "axis 0"), ("summary.axes[0]", "axis 0 description"),
        ("summary.axes[0]", "axis 0 strong"), ("summary.axes[0]", "axis 0 weak"),
        ("summary.axes[1]", "axis 1"), ("summary.axes[1]", "axis 1 description"),
        ("summary.axes[1]", "axis 1 strong"), ("summary.axes[1]", "axis 1 weak"),
        ("diagrams[0]", "caption 0"),
        ("diagrams[0].lanes[0]", "lane 0.0"),
        ("diagrams[0].lanes[0].steps[0]", "step 0.0.0"), ("diagrams[0].lanes[0].steps[0]", "note 0.0.0"),
        ("diagrams[0].lanes[0].steps[0]", "detail 0.0.0 a"), ("diagrams[0].lanes[0].steps[0]", "detail 0.0.0 b"),
        ("diagrams[0].lanes[0].steps[1]", "step 0.0.1"),
        ("diagrams[1]", "caption 1"),
        ("diagrams[1].lanes[0]", "lane 1.0"),
        ("diagrams[1].lanes[0].steps[0]", "step 1.0.0"),
        ("diagrams[1].lanes[1]", "lane 1.1"),
        ("diagrams[1].lanes[1].steps[0]", "step 1.1.0"), ("diagrams[1].lanes[1].steps[0]", "detail 1.1.0"),
        ("diagrams[1].lanes[1].steps[1]", "step 1.1.1"), ("diagrams[1].lanes[1].steps[1]", "note 1.1.1"),
        ("strengths[0]", "name s1"), ("strengths[0]", "desc s1"), ("strengths[0]", "gives s1"),
        ("strengths[0]", "costs s1"), ("strengths[0]", "on s1"),
        ("gaps[0]", "name g1"), ("gaps[0]", "desc g1"), ("gaps[0]", "gives g1"),
        ("gaps[0]", "costs g1"), ("gaps[0]", "on g1"),
        ("gaps[1]", "name g2"), ("gaps[1]", "desc g2"), ("gaps[1]", "gives g2"),
        ("gaps[1]", "costs g2"), ("gaps[1]", "on g2"),
        ("styles[0]", "name y1"), ("styles[0]", "desc y1"), ("styles[0]", "gives y1"),
        ("styles[0]", "costs y1"), ("styles[0]", "on y1"),
        ("implications[0]", "area 0"), ("implications[0]", "meaning 0"),
        ("implications[1]", "area 1"), ("implications[1]", "meaning 1")])
    # the evidence, the appendix, the scope, and the timeline are where identifiers belong
    check("no hidden field yielded", [text for _, text in got if "hidden" in text], [])
    check("no place outside the visible fields",
          [where for where, _ in got if where.split("[")[0].split(".")[0] not in (
              "document", "summary", "diagrams", "strengths", "gaps", "styles", "implications")], [])


def test_visible_text_skips():
    # a value of the wrong type is validate's to name, so visible_text yields only the text
    doc = {"title": 5, "scope_line": None,
           "summary": {"text": ["the summary"],
                       "axes": [{"name": "axis", "description": 7, "strong": None, "weak": "weak"}]},
           "diagrams": [{"caption": 3,
                         "lanes": [{"title": {"text": "lane"},
                                    "steps": [{"text": "step", "note": 4, "detail": ["one", 2, None, "three"]}]}]}],
           "strengths": [{"name": "name", "description": 8, "gives": ["g"], "costs": None, "confidence": {"on": 9}}],
           "implications": [{"area": 1, "meaning": "meaning"}]}
    # written by hand: only the string values, in walk order
    check("non-string values skipped", rr.visible_text(doc), [
        ("summary.axes[0]", "axis"), ("summary.axes[0]", "weak"),
        ("diagrams[0].lanes[0].steps[0]", "step"), ("diagrams[0].lanes[0].steps[0]", "one"),
        ("diagrams[0].lanes[0].steps[0]", "three"),
        ("strengths[0]", "name"),
        ("implications[0]", "meaning")])

    # validate walks the text even when it has already named a bad shape, so a bad shape must not raise here.
    # written by hand: a bare value is passed over and keeps its index, so the object after it names the next place
    for name, doc, want in (
            ("summary not an object", {"title": "t", "summary": "fixed in abc1234"}, [("document", "t")]),
            ("axis not an object", {"summary": {"text": "t", "axes": ["axis", 5, {"name": "real"}]}},
             [("summary", "t"), ("summary.axes[2]", "real")]),
            ("diagram not an object", {"diagrams": ["a flow", {"caption": "real"}]}, [("diagrams[1]", "real")]),
            ("lane not an object", {"diagrams": [{"caption": "c", "lanes": ["lane", {"title": "real"}]}]},
             [("diagrams[0]", "c"), ("diagrams[0].lanes[1]", "real")]),
            ("step not an object", {"diagrams": [{"lanes": [{"steps": ["step", {"text": "real"}]}]}]},
             [("diagrams[0].lanes[0].steps[1]", "real")]),
            ("detail not a list",
             {"diagrams": [{"lanes": [{"steps": [{"text": "s", "note": "n", "detail": "one line"}]}]}]},
             [("diagrams[0].lanes[0].steps[0]", "s"), ("diagrams[0].lanes[0].steps[0]", "n")]),
            ("pattern not an object", {"gaps": ["a bare string", {"name": "real"}]}, [("gaps[1]", "real")]),
            ("confidence not an object", {"styles": [{"name": "real", "confidence": "the load"}]},
             [("styles[0]", "real")]),
            ("implication not an object", {"implications": ["reviews", {"area": "real"}]},
             [("implications[1]", "real")])):
        check(name + ": walked without raising", rr.visible_text(doc), want)


def test_visible_text_bad_containers():
    # a truthy container that cannot be iterated raised here once, which turned validate's exit 2 into a traceback.
    # written by hand: the bad container yields nothing, and the walk goes on to the field after it
    for name, doc, want in (
            ("diagrams a number", {"title": "t", "diagrams": 5, "gaps": [{"name": "after"}]},
             [("document", "t"), ("gaps[0]", "after")]),
            ("gaps a number", {"strengths": [{"name": "before"}], "gaps": 5, "styles": [{"name": "after"}]},
             [("strengths[0]", "before"), ("styles[0]", "after")]),
            ("implications a number", {"title": "t", "implications": 5}, [("document", "t")]),
            ("axes a boolean", {"summary": {"text": "t", "axes": True}, "diagrams": [{"caption": "after"}]},
             [("summary", "t"), ("diagrams[0]", "after")]),
            ("lanes a number", {"diagrams": [{"caption": "c", "lanes": 5}, {"caption": "after"}]},
             [("diagrams[0]", "c"), ("diagrams[1]", "after")]),
            ("steps a number", {"diagrams": [{"lanes": [{"title": "l", "steps": 5}, {"title": "after"}]}]},
             [("diagrams[0].lanes[0]", "l"), ("diagrams[0].lanes[1]", "after")]),
            # a string is truthy, so without the list check it reaches the step's field list and raises there
            ("detail a string", {"diagrams": [{"lanes": [{"steps": [{"text": "s", "detail": "x"}, {"text": "after"}]}]}]},
             [("diagrams[0].lanes[0].steps[0]", "s"), ("diagrams[0].lanes[0].steps[1]", "after")])):
        check(name + ": walked without raising", rr.visible_text(doc), want)

    # end to end, the shape is named once and the run exits 2 rather than raising
    _invalid("diagrams that is a number", _doc(diagrams=5), "'diagrams' is not a list")
    _invalid("gaps that is a number", _doc(gaps=5), "'gaps' is not a list")
    _invalid("implications that is a number", _doc(implications=5), "'implications' is not a list")
    _invalid("axes that is a boolean", _doc(summary={"text": "t", "axes": True}), "summary: 'axes' is not a list")


def test_sha_pattern():
    # written by hand: forty hex characters holding a digit, the longest a SHA is
    full = "0123456789abcdef0123456789abcdef01234567"
    check("fixture: forty characters", len(full), 40)
    for name, text, want in (
            ("seven hex characters", "abc1234", "abc1234"),
            ("forty hex characters", full, full),
            ("hex letters with one digit", "deadbeef1", "deadbeef1"),
            # CJK text puts no word boundary before a SHA
            ("between CJK characters", "\u4fee\u6b63abc1234\u7684", "abc1234"),
            ("after an underscore", "_abc1234", "abc1234"),
            ("before a hyphen", "abc1234-5", "abc1234")):
        found = rr.SHA.search(text)
        check("SHA matches %s" % name, found.group(0) if found else None, want)
    for name, text in (
            ("a word of hex letters", "facade"),
            ("a year", "2026"),
            ("seven hex letters with no digit", "deadbee"),
            ("six characters", "abc123"),
            ("forty-one characters", full + "8"),
            ("fifty characters", full + "89abcdef01"),
            ("uppercase", "ABC1234"),
            ("a leading capital", "Abc1234"),
            ("a trailing letter", "abc1234x"),
            ("a leading letter", "Xabc1234"),
            ("a hex literal", "0xdeadbeef1"),
            # a run of digits alone is a date, a count, or an id, so a SHA needs a hex letter too
            ("a compact date", "20260924"),
            ("seven digits", "1234567"),
            ("forty digits", "1234567890" * 4)):
        check("SHA does not match %s" % name, rr.SHA.search(text), None)


def _sha_problem(where, sha):
    return "%s: '%s' looks like a commit SHA; say what happened, and keep identifiers in an instance's ref" % (where, sha)


def test_validation_sha():
    axis = {"name": "a", "strong": "fixed in abc1234", "weak": "w"}
    for name, doc, where in (
            ("an axis side", _doc(summary={"text": "t", "axes": [axis]}), "summary.axes[0]"),
            ("a diagram step detail", _detail_doc(detail=("one", "see abc1234")), "diagrams[0].lanes[0].steps[0]"),
            ("a pattern constraint",
             _doc(gaps=[_pattern("g1", confidence={"level": "depends", "on": "the revert in abc1234"})]), "gaps[0]"),
            ("an implication meaning", _doc(implications=[{"area": "reviews", "meaning": "as abc1234 shows"}]),
             "implications[0]")):
        check("a SHA in %s named with its place" % name, rr.validate(doc), [_sha_problem(where, "abc1234")])
    _invalid("a SHA in the summary", _doc(summary={"text": "fixed in deadbeef1"}), _sha_problem("summary", "deadbeef1"))

    # one field is one thing to rewrite, so only its first SHA is named
    check("two SHAs in one field give one problem", rr.validate(_doc(summary={"text": "abc1234 then def5678"})),
          [_sha_problem("summary", "abc1234")])
    # the implications are given before the title, so problems in the document's key order would come out reversed
    check("two fields give two problems in walk order",
          rr.validate(_doc(implications=[{"area": "reviews", "meaning": "see def5678"}], title="after abc1234")),
          [_sha_problem("document", "abc1234"), _sha_problem("implications[0]", "def5678")])

    # the evidence, the appendix, and the scope are where identifiers belong, so a SHA there is what the rule asks for
    for name, doc in (
            ("an instance text and ref", _doc(strengths=[_pattern("s1", exceptions=[], instances=[
                {"text": "fixed in abc1234", "ref": "abc1234"}, {"text": "two", "ref": "def5678"}])])),
            ("an exception",
             _doc(strengths=[_pattern("s1", exceptions=[{"text": "reverted in abc1234", "ref": "abc1234"}])])),
            ("calibration and checked", _doc(strengths=[_pattern("s1", exceptions=[],
                                                                 calibration="3 of 10 since abc1234",
                                                                 checked="read abc1234")])),
            ("a scope value", _doc(scope={"repositories": "repo-a at abc1234", "commits": ["abc1234", "def5678"]})),
            ("an event", _doc(events=[{"text": "reverted abc1234", "ref": "abc1234"}])),
            ("a dissolved candidate", _doc(dissolved=[{"name": "abc1234", "evidence": "refuted by def5678"}]))):
        check("a SHA in %s valid" % name, rr.validate(doc), [])


def test_validation_labels_completeness():
    check("every label but the title valid", rr.validate(_doc(labels=_all_labels({}))), [])
    check("every label and the title valid", rr.validate(_doc(labels=_all_labels({"title": "whoami for repo-a"}))), [])

    _invalid("one label missing", _doc(labels=_without(_all_labels({}), "gaps")),
             "labels: gaps missing; give every label or none")
    labels = _all_labels({})
    # removed out of LABELS order, so a list in removal order would differ
    for key in ("timeline_ai", "summary", "colon"):
        del labels[key]
    check("several labels missing, named in LABELS order", rr.validate(_doc(labels=labels)),
          ["labels: summary, colon, timeline_ai missing; give every label or none"])

    check("empty labels valid", rr.validate(_doc(labels={})), [])
    check("null labels valid", rr.validate(_doc(labels=None)), [])
    # labels_for drops a label that is not text, so it falls back to English as a label left out does
    for name, value in (("number", 5), ("null", None), ("list", ["Gaps"])):
        check("a %s label counts as missing" % name, rr.validate(_doc(labels=_all_labels({"gaps": value}))),
              ["labels: gaps missing; give every label or none"])

    # labels_for reads the labels as an object, so a bare value must be refused before rendering reaches it
    _invalid("labels that is a list", _doc(labels=["Summary"]), "'labels' is not an object")
    _invalid("labels that is a string", _doc(labels="Summary"), "'labels' is not an object")


def test_validation_sha_and_labels_problem_order():
    # the fields are given in the reverse of the order they are reported in
    doc = _without(_doc(labels=_without(_all_labels({}), "gaps"),
                        implications=[{"area": "reviews", "meaning": "see def5678"}],
                        summary={"text": "fixed in abc1234"},
                        timeline=_timeline("repo-a")), "scope")
    # written by hand in the order validate reports: the timeline, then each SHA in walk order,
    # then the labels, then the scope
    _invalid_all("SHA and labels problems between the timeline and the scope", doc, [
        "timeline.repositories[0]: a repository must be an object",
        _sha_problem("summary", "abc1234"),
        _sha_problem("implications[0]", "def5678"),
        "labels: gaps missing; give every label or none",
        "document: 'scope' is missing or empty"])


def test_colon_label():
    check("English colon label", rr.LABELS["colon"], ": ")
    m, e = _markup, _escaped
    wide = rr.labels_for({"labels": {"colon": "\uff1a"}})
    # written by hand: the depends label, the full-width colon with no space after it, then the constraint
    check("colon joins the constraint", rr.constraint_text(_LOAD, wide), "Depends on\uff1athe load")
    check("colon reaches the badge title and tip", rr.badge_html(_LOAD, wide),
          '<span class="badge depends" tabindex="0" title="Depends on\uff1athe load"'
          ' data-tip="Depends on\uff1athe load">Conditional</span>')
    tip = "Depends on%sthe load" % e("colon")
    check("markup in the colon escaped in the badge",
          rr.badge_html(_LOAD, rr.labels_for({"labels": {"colon": m("colon")}})),
          '<span class="badge depends" tabindex="0" title="%s" data-tip="%s">Conditional</span>' % (tip, tip))

    doc = _full_doc()
    doc["labels"] = _all_labels({"colon": "\uff1a"})
    check("fixture: full-width colon document is valid", rr.validate(doc), [])
    check("colon reaches the badge on the page",
          'title="Depends on\uff1athe load" data-tip="Depends on\uff1athe load"' in _section(_html(doc), "Gaps"), True)
    lines = _md_lines(doc)
    check("colon reaches the Markdown condition line", "- name g1 \u2014 Depends on\uff1athe load" in lines, True)
    check("colon reaches the report path line", lines[-1], "Full report\uff1a`OUT`")

    # written by hand from _tl_full: the AI marker on day 20, titled with the label, the colon, then the date
    check("colon reaches the AI marker title",
          '<span class="tl-ai" style="left:20.00%" title="AI shows up\uff1a2026-01-21"></span>'
          in _tl_html(_tl_full(), {"colon": "\uff1a"}), True)
    check("markup in the colon escaped in the AI marker title",
          '<span class="tl-ai" style="left:20.00%" title="AI shows up' + e("colon") + '2026-01-21"></span>'
          in _tl_html(_tl_full(), {"colon": m("colon")}), True)

    # the colon is prose ahead of the code span, so its backslash is doubled while the path stays as passed.
    # written by hand: two backslashes, a colon, and a space, then the path with single backslashes
    doc = _doc(labels=_all_labels({"colon": "\\: "}))
    check("backslash in the colon doubled in the report path line",
          rr.render_markdown(doc, rr.labels_for(doc), "C:\\out\\report.html").splitlines()[-1],
          "Full report\\\\: `C:\\out\\report.html`")

    # written by hand: the English output as the default colon writes it
    check("default colon in the English output",
          (rr.constraint_text(_LOAD, rr.LABELS), _md_lines(_full_doc())[-1],
           'title="AI shows up: 2026-01-21"' in _tl_html(_tl_full())),
          ("Depends on: the load", "Full report: `OUT`", True))
    # the default spelled out in the labels renders exactly what no labels render
    explicit = dict(_full_doc(), labels=_all_labels({}))
    check("default colon leaves the Markdown unchanged", _md_lines(explicit), _md_lines(_full_doc()))
    check("default colon leaves the page unchanged", _html(explicit), _html(_full_doc()))


def test_markdown_section_order():
    # written by hand: the scope and its line lead, then the summary, the strength table, and the report path
    check("scope section leads the Markdown", _md_lines(_doc(scope_line="repo-a over one year")), [
        "## Scope", "", "repo-a over one year", "",
        "## Summary", "", "the summary", "",
        "## Strengths", "",
        "| Pattern | Source | Confidence |",
        "|---|---|---|",
        "| name s1 | code | Verified |", "",
        "Full report: `OUT`"])
    for name, doc in (("no scope line", _doc()), ("empty scope line", _doc(scope_line=""))):
        lines = _md_lines(doc)
        check(name + ": no scope section, and the summary leads", (lines[0], "## Scope" in lines), ("## Summary", False))

    lines = _md_lines(_full_doc())
    # written by hand: every heading the full document prints, in order
    check("headings in order, the scope first", [ln for ln in lines if ln.startswith("## ")], [
        "## Scope", "## Summary", "## Strengths", "## Gaps", "## Ways of working", "## What this means for your work"])
    check("scope line printed once", lines.count("repo-a over one year"), 1)
    # the page puts its scope table after the implications, and the Markdown must not follow it there
    check("implications followed directly by the report path",
          lines[lines.index("## What this means for your work"):], [
              "## What this means for your work", "",
              "- **reviews** \u2014 ask early", "",
              "Full report: `OUT`"])


def test_diagram_markdown_direct():
    # a lab built by hand rather than by labels_for, so the joiner can only come from the lab passed in
    lab = dict(rr.LABELS, list_separator=" + ")
    d = {"caption": "the flow",
         "lanes": [{"title": "first lane", "steps": [{"text": "a", "detail": ["x", "y"], "note": "then"},
                                                     {"text": "b", "note": ""},
                                                     {"text": "c", "note": "dropped"}]},
                   {"title": "second lane", "steps": [{"text": "d"}, {"text": "e", "detail": ["z"]}]}]}
    got = rr.diagram_markdown(d, lab)
    # written by hand: the caption in bold and a blank line, then each lane under its italic title,
    # the details after an em dash joined by the lab's separator,
    # an arrow only under a step with a note and a step after it, and a blank line closing each lane
    check("diagram lines from a custom lab", got, [
        "**the flow**", "",
        "*first lane*",
        "1. a \u2014 x + y",
        "   \u2193 then",
        "2. b",
        "3. c",
        "",
        "*second lane*",
        "1. d",
        "2. e \u2014 z",
        ""])
    check("the caption leads and does not follow the lanes", [i for i, ln in enumerate(got) if "the flow" in ln], [0])
    for name, caption in (("no caption", {}), ("empty caption", {"caption": ""})):
        check(name + " renders no caption lines",
              rr.diagram_markdown(dict(caption, lanes=[{"steps": [{"text": "a"}]}]), lab), ["1. a", ""])


def test_markdown_implications_lines():
    doc = _doc(implications=[{"area": "zeta", "meaning": "the last letter", "patterns": ["s1"]},
                             {"area": "two\nlines", "meaning": "ask\nearly"},
                             {"area": "C:\\a|b", "meaning": "D:\\c|d", "patterns": ["s1"]}])
    check("fixture: implications document is valid", rr.validate(doc), [])
    lines = _md_lines(doc)
    section = lines[lines.index("## What this means for your work"):lines.index("Full report: `OUT`")]
    # written by hand: one line per implication in document order, not sorted, each line break a space,
    # each backslash doubled, and each pipe left as typed, since a list line is not a table cell
    check("one line per implication in document order", section, [
        "## What this means for your work", "",
        "- **zeta** \u2014 the last letter",
        "- **two lines** \u2014 ask early",
        "- **C:\\\\a|b** \u2014 D:\\\\c|d",
        ""])
    # the related patterns are left to the HTML, and a list needs no table head
    check("no pattern name or id in the implications lines", [ln for ln in section if "s1" in ln], [])
    check("no table line in the implications lines", [ln for ln in section if ln.startswith("|")], [])


# ----- colour scheme switch ------------------------------------------------------

# written by hand from the switch's format: the three radios in the order auto, light, dark, with only auto checked
_SWITCH = ('<div class="theme" role="radiogroup" aria-label="Colour scheme">'
           '<input type="radio" name="theme" id="theme-auto" checked><label for="theme-auto">Auto</label>'
           '<input type="radio" name="theme" id="theme-light"><label for="theme-light">Light</label>'
           '<input type="radio" name="theme" id="theme-dark"><label for="theme-dark">Dark</label>'
           '</div>')

_THEME_KEYS = ("theme", "theme_auto", "theme_light", "theme_dark")

# written by hand: every custom property the page reads, in the order each theme declares them
_TOKENS = ["--paper", "--sheet", "--ink", "--muted", "--rule", "--right", "--right-wash", "--miss", "--miss-wash",
           "--focus", "--tip", "--tip-ink", "--tl-bar", "--tl-prompt"]


def _style(page):
    return page[page.index("<style>") + len("<style>"):page.index("</style>")]


def test_theme_labels():
    check("English theme labels", {k: rr.LABELS[k] for k in _THEME_KEYS},
          {"theme": "Colour scheme", "theme_auto": "Auto", "theme_light": "Light", "theme_dark": "Dark"})
    check("every label with the theme labels valid", rr.validate(_doc(labels=_all_labels({}))), [])
    _invalid("theme label missing", _doc(labels=_without(_all_labels({}), "theme_dark")),
             "labels: theme_dark missing; give every label or none")
    labels = _all_labels({})
    # removed out of LABELS order, so a list in removal order would differ
    for key in ("theme_dark", "theme", "theme_light", "theme_auto"):
        del labels[key]
    check("all four theme labels missing, named in LABELS order", rr.validate(_doc(labels=labels)),
          ["labels: theme, theme_auto, theme_light, theme_dark missing; give every label or none"])


def test_theme_switch():
    check("English switch", rr.theme_switch(rr.LABELS), _SWITCH)
    check("only auto checked", rr.theme_switch(rr.LABELS).count(" checked"), 1)

    m, e = _markup, _escaped
    lab = rr.labels_for({"labels": {k: m("label-" + k) for k in _THEME_KEYS}})
    # written by hand: each label escaped where it sits, the group's name inside aria-label="..."
    check("every theme label escaped", rr.theme_switch(lab),
          '<div class="theme" role="radiogroup" aria-label="%s">'
          '<input type="radio" name="theme" id="theme-auto" checked><label for="theme-auto">%s</label>'
          '<input type="radio" name="theme" id="theme-light"><label for="theme-light">%s</label>'
          '<input type="radio" name="theme" id="theme-dark"><label for="theme-dark">%s</label>'
          '</div>' % (e("label-theme"), e("label-theme_auto"), e("label-theme_light"), e("label-theme_dark")))

    labels = {"theme": "\u914d\u8272", "theme_auto": "\u81ea\u52d5", "theme_light": "\u6dfa\u8272",
              "theme_dark": "\u6df1\u8272"}
    doc = _full_doc()
    doc["labels"] = _all_labels(labels)
    check("fixture: localised theme labels document is valid", rr.validate(doc), [])
    page = _html(doc)
    check("localised switch on the page",
          '<div class="theme" role="radiogroup" aria-label="\u914d\u8272">'
          '<input type="radio" name="theme" id="theme-auto" checked><label for="theme-auto">\u81ea\u52d5</label>'
          '<input type="radio" name="theme" id="theme-light"><label for="theme-light">\u6dfa\u8272</label>'
          '<input type="radio" name="theme" id="theme-dark"><label for="theme-dark">\u6df1\u8272</label>'
          '</div>' in page, True)
    check("English theme labels gone",
          [s for s in ("Colour scheme", ">Auto<", ">Light<", ">Dark<") if s in page], [])


def test_theme_switch_placement():
    check("switch first in main, before the title",
          "<main>" + _SWITCH + "<h1>whoami: repo-a</h1>" in _html(_full_doc()), True)
    minimal = _html(_doc())
    check("switch first in main on a minimal page", "<main>" + _SWITCH + "<h1>whoami</h1>" in minimal, True)
    check("switch rendered once", minimal.count('<div class="theme"'), 1)
    # the timeline sits under the title, so the switch still leads
    check("switch ahead of the title with a timeline",
          "<main>" + _SWITCH + "<h1>whoami</h1>" in _html(_doc(timeline=_tl_full())), True)

    code, out, err, out_path = _main(_doc())
    check("a valid document exits 0", (code, err), (0, ""))
    with open(out_path, encoding="utf-8") as f:
        check("the written page holds the switch", "<main>" + _SWITCH + "<h1>whoami</h1>" in f.read(), True)
    # the switch is page chrome, so the terminal summary never shows it
    md = _md_lines(_full_doc())
    # an empty summary would pass the absence check without having looked at anything
    check("fixture: Markdown lines found", len(md) > 0, True)
    # written by hand: every piece of the switch that could leak, the group, its three labels, and its name
    pieces = ('class="theme"', ">Auto<", ">Light<", ">Dark<", 'aria-label="Colour scheme"', "Colour scheme")
    check("switch pieces absent from the Markdown", [(p, ln) for p in pieces for ln in md if p in ln], [])


def test_theme_tokens():
    check("light theme leads with its colour scheme", rr.LIGHT.startswith("color-scheme:light;"), True)
    check("dark theme leads with its colour scheme", rr.DARK.startswith("color-scheme:dark;"), True)
    check("light theme tokens, each once", re.findall(r"(--[\w-]+):", rr.LIGHT), _TOKENS)
    check("dark theme tokens, each once", re.findall(r"(--[\w-]+):", rr.DARK), _TOKENS)
    # a token read by the page but set by neither theme would fall back to nothing in both,
    # and --icon is set per section rather than per theme
    check("every token the page reads comes from the themes",
          sorted(set(re.findall(r"var\((--[\w-]+)\)", rr.CSS)) - {"--icon"}), sorted(_TOKENS))
    check("the two themes differ", rr.LIGHT == rr.DARK, False)


def test_theme_css():
    style = _style(_html(_doc()))
    check("stylesheet opens with the themes", style.startswith(rr.THEMES), True)
    # written by hand: the system rule for each scheme, then a forced rule per scheme that a checked radio turns on
    check("themes in their four rules", rr.THEMES,
          ":root { " + rr.LIGHT + " }\n"
          "@media (prefers-color-scheme: dark) { :root { " + rr.DARK + " } }\n"
          ":root:has(#theme-light:checked) { " + rr.LIGHT + " }\n"
          ":root:has(#theme-dark:checked) { " + rr.DARK + " }\n")

    # the bodies are read back off the page, so a forced rule that drifted from its system rule shows up here
    bodies = {}
    for key, pattern in (("system light", r"^:root \{ (.*?) \}\n"),
                         ("system dark", r"@media \(prefers-color-scheme: dark\) \{ :root \{ (.*?) \} \}"),
                         ("forced light", r":root:has\(#theme-light:checked\) \{ (.*?) \}"),
                         ("forced dark", r":root:has\(#theme-dark:checked\) \{ (.*?) \}")):
        found = re.search(pattern, style, re.S)
        bodies[key] = found.group(1) if found else None
    check("system light rule holds the light tokens", bodies["system light"], rr.LIGHT)
    check("system dark rule holds the dark tokens", bodies["system dark"], rr.DARK)
    check("forced light repeats the system light tokens", bodies["forced light"], bodies["system light"])
    check("forced dark repeats the system dark tokens", bodies["forced dark"], bodies["system dark"])
    # equal-specificity rules resolve by order, so the system dark rule must follow the light one it overrides
    starts = [style.find(s) for s in (":root {", "@media (prefers-color-scheme: dark)",
                                      ":root:has(#theme-light:checked)", ":root:has(#theme-dark:checked)")]
    check("the four rules in order", (starts[0], starts == sorted(starts)), (0, True))

    # each forced rule names a radio the switch renders, or no click could turn it on
    page = _html(_doc())
    for key in ("light", "dark"):
        check("forced %s rule matches a rendered radio" % key,
              ("#theme-%s:checked" % key in style, '<input type="radio" name="theme" id="theme-%s">' % key in page),
              (True, True))
    check("switch hidden when printed", "@media print { .theme { display:none; } }" in style, True)


# written by hand from where the stylesheet draws each colour: text on a background, at the WCAG 2 AA minimum for body text
_TEXT_PAIRS = (("ink", "sheet"), ("muted", "sheet"), ("ink", "paper"),
               ("right", "sheet"), ("miss", "sheet"),
               ("ink", "right-wash"), ("muted", "right-wash"), ("right", "right-wash"),
               ("ink", "miss-wash"), ("muted", "miss-wash"), ("miss", "miss-wash"),
               ("tip-ink", "tip"))
# the timeline's bars and prompt marks are shapes rather than text, so they need only the non-text minimum
_SHAPE_PAIRS = (("tl-bar", "sheet"), ("tl-prompt", "sheet"))


def _theme_colours(theme):
    return dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6});", theme))


# the renderer computes no contrast, so the WCAG 2 arithmetic lives here,
# and the known answers in test_theme_contrast check it against published figures
def _luminance(hex_colour):
    def linear(byte):
        c = byte / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)


def _contrast(fg, bg):
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _contrast_failures(theme):
    colours = _theme_colours(theme)
    return [(fg, bg, round(_contrast(colours[fg], colours[bg]), 2))
            for pairs, least in ((_TEXT_PAIRS, 4.5), (_SHAPE_PAIRS, 3.0))
            for fg, bg in pairs if _contrast(colours[fg], colours[bg]) < least]


def test_theme_contrast():
    # written by hand: the published extremes, and the grey commonly cited as the lightest that passes AA on white
    check("black on white", round(_contrast("#000000", "#ffffff"), 2), 21.0)
    check("white on white", round(_contrast("#ffffff", "#ffffff"), 2), 1.0)
    check("#767676 on white", round(_contrast("#767676", "#ffffff"), 2), 4.54)
    check("order of the two colours does not matter", round(_contrast("#ffffff", "#767676"), 2), 4.54)
    # written by hand: the amber label on its wash before it was darkened, which this check was written to catch
    check("old amber on its wash", round(_contrast("#b25a0a", "#fdefd6"), 2), 4.24)

    for name, theme in (("light", rr.LIGHT), ("dark", rr.DARK)):
        # a token in any other notation would drop out of the parse and could not be measured
        check(name + ": every token a #rrggbb colour", sorted(_theme_colours(theme)), sorted(t[2:] for t in _TOKENS))
        check(name + ": every pair readable", _contrast_failures(theme), [])

    # hypothetical palettes, each the real one with a single colour swapped, so the check is shown to reject them
    old_amber = re.sub(r"--miss:#[0-9a-f]{6};", "--miss:#b25a0a;", rr.LIGHT)
    check("amber too light for its wash fails", _contrast_failures(old_amber), [("miss", "miss-wash", 4.24)])
    black_ink = re.sub(r"--ink:#[0-9a-f]{6};", "--ink:#000000;", rr.DARK)
    check("black ink on the dark theme fails on every background",
          _contrast_failures(black_ink),
          [("ink", "sheet", 1.45), ("ink", "paper", 1.28), ("ink", "right-wash", 2.66), ("ink", "miss-wash", 2.52)])
    # a bar at 3.03 passes the shape minimum it would fail as text, and one at 2.61 fails both
    faint_bar = re.sub(r"--tl-bar:#[0-9a-f]{6};", "--tl-bar:#949494;", rr.LIGHT)
    # a passing case proves nothing unless the swap happened, so the swapped colour is looked for too
    check("a bar just over the shape minimum passes",
          ("--tl-bar:#949494;" in faint_bar, _contrast_failures(faint_bar)), (True, []))
    fainter_bar = re.sub(r"--tl-bar:#[0-9a-f]{6};", "--tl-bar:#a0a0a0;", rr.LIGHT)
    check("a bar under the shape minimum fails", _contrast_failures(fainter_bar), [("tl-bar", "sheet", 2.61)])


# ----- a cp1252 console ----------------------------------------------------------

def test_non_ascii_on_cp1252_console():
    text = "\u7e41\u9ad4\u4e2d\u6587 summary"
    name = "\u6a21\u5f0f s1"
    d = tempfile.mkdtemp()
    report = os.path.join(d, "report.json")
    out_path = os.path.join(d, "report.html")
    with open(report, "w", encoding="utf-8") as f:
        json.dump(_doc(summary={"text": text}, strengths=[_pattern("s1", exceptions=[], name=name)]), f,
                  ensure_ascii=False)
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run([sys.executable, _SCRIPT, report, "--out", out_path], capture_output=True, env=env, timeout=60)
    check("subprocess exits 0", proc.returncode, 0)
    check("subprocess stderr empty", proc.stderr, b"")
    lines = proc.stdout.decode("utf-8").splitlines()
    check("summary printed as UTF-8", lines[2], text)
    check("pattern name printed as UTF-8", "| %s | code | Verified |" % name in lines, True)
    with open(out_path, encoding="utf-8") as f:
        page = f.read()
    check("HTML file holds the non-ASCII text", ("<p>%s</p>" % text in page, name in page), (True, True))


def test_diagram_on_cp1252_console():
    # the arrow between two steps is outside cp1252, so a console left at cp1252 fails on the first diagram
    tmp = tempfile.mkdtemp()
    report = os.path.join(tmp, "report.json")
    out_path = os.path.join(tmp, "report.html")
    with open(report, "w", encoding="utf-8") as f:
        json.dump(_diagram_doc(_diagram([_lane([_step("step one", note="then"), _step("step two")])])), f)
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run([sys.executable, _SCRIPT, report, "--out", out_path], capture_output=True, env=env, timeout=60)
    check("subprocess exits 0", proc.returncode, 0)
    check("subprocess stderr empty", proc.stderr, b"")
    lines = proc.stdout.decode("utf-8").splitlines()
    check("arrow with its note printed as UTF-8", "   \u2193 then" in lines, True)


_TESTS = (test_valid_document_renders, test_validation_document, test_validation_pattern_fields,
          test_validation_instances, test_validation_exceptions, test_validation_styles, test_validation_references,
          test_validation_shapes, test_validation_lists_every_problem, test_invalid_json, test_missing_report,
          test_escaping, test_html_loads_nothing, test_pattern_tables, test_confidence_labels,
          test_evidence_in_details, test_appendix, test_related_links, test_summary_title_scope,
          test_empty_groups_render_no_section, test_labels_for,
          test_labels_in_html, test_separator_joins_sources, test_markdown_cells, test_markdown_tables,
          test_markdown_prose_backslashes, test_markdown_report_path, test_markdown_axis_lines,
          test_markdown_axis_escaping, test_markdown_three_column_pattern_tables,
          test_markdown_implications_list, test_markdown_list_separator, test_markdown_report_file_label,
          test_validation_diagram_shape, test_validation_diagram_lanes, test_validation_diagram_steps,
          test_validation_diagram_paths, test_validation_diagram_problem_order, test_diagram_html_structure,
          test_diagram_html_arrows, test_diagram_html_placement, test_diagram_css, test_diagram_escaping,
          test_markdown_diagram_list, test_markdown_diagram_escaping, test_markdown_diagram_placement,
          test_badge_html, test_constraint_and_confidence_text, test_conditional_label_localised,
          test_evidence_depends_block, test_markdown_conditions_list, test_axis_link_split,
          test_axis_omits_styles, test_axis_frame_css, test_no_line_measure, test_document_lang_attribute,
          test_section_and_table_classes,
          test_card_rules_and_elements, test_summary_not_a_card, test_lead_wrapper, test_last_row_table_rule,
          test_heading_rules_reach_lead, test_figure_margins,
          test_validation_timeline_shape, test_validation_timeline_dates, test_validation_timeline_problem_order,
          test_timeline_html_full, test_timeline_thin_close_split, test_timeline_single_day, test_timeline_ai_marker,
          test_timeline_prompts_row, test_timeline_legend, test_timeline_tick_steps, test_timeline_tick_edges_and_minor,
          test_timeline_escaping, test_timeline_placement, test_timeline_labels_localised,
          test_timeline_absent_from_markdown,
          test_visible_text_walk, test_visible_text_skips, test_visible_text_bad_containers,
          test_sha_pattern, test_validation_sha,
          test_validation_labels_completeness, test_validation_sha_and_labels_problem_order, test_colon_label,
          test_markdown_section_order, test_diagram_markdown_direct, test_markdown_implications_lines,
          test_theme_labels, test_theme_switch, test_theme_switch_placement,
          test_theme_tokens, test_theme_css, test_theme_contrast,
          test_non_ascii_on_cp1252_console, test_diagram_on_cp1252_console)


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
