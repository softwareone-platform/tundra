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
    # the related column repeats the pattern name, so it is flattened and escaped there too
    check("pipe escaped and newline flattened in an implication row", "| a\\|rea break | a\\|b two |" in lines, True)
    check("no row split onto a second line", [ln for ln in lines if ln.startswith("two") or ln.startswith("break")], [])

    doc = _doc(strengths=[_pattern("s1", exceptions=[], name="a\\|b"),
                          _pattern("s2", exceptions=[], confidence={"level": "depends", "on": "C:\\dir"})],
               implications=[{"area": "reviews", "meaning": "ask early", "patterns": ["s1"]}],
               labels={"area": "x|\\area", "related": "x|\\related"})
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
    check("backslash doubled before the pipe is escaped in the related column",
          "| reviews | a\\\\\\|b |" in lines, True)
    # written by hand: x, backslash, pipe, two backslashes, then the label's own word
    check("implications header labels escaped as cells",
          "| x\\|\\\\area | x\\|\\\\related |" in lines, True)


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
    # related patterns are shown by name, since an id means nothing to the reader
    check("implications table lists pattern names", _has_block(lines, [
        "## What this means for your work", "", "| Area | Related patterns |", "|---|---|",
        "| reviews | name s1; name g1 |"]), True)
    check("implications carry no pattern ids", "| reviews | s1; g1 |" in lines, False)
    check("scope line under its label", _has_block(lines, ["## Scope", "", "repo-a over one year"]), True)
    # written by hand: each lane under its bold title, a numbered step per line with its details in parentheses,
    # an arrow between steps carrying the note when there is one, a blank line after each lane, then the caption
    check("diagram listed lane by lane", _has_block(lines, [
        "**lane one**",
        "1. step one (detail one; detail two)",
        "   \u2193 then",
        "2. step two", "",
        "**lane two**",
        "1. step three", "",
        "*a flow*", ""]), True)
    check("ends with the report path", lines[-1], "Full report: `OUT`")


def test_markdown_prose_backslashes():
    # every heading this document prints is overridden,
    # so a heading left without md_text shows up as a single backslash.
    # each value also holds a pipe, which md_cell would escape,
    # so a heading or prose line switched to the table-cell escape shows up too
    labels = {key: "C:\\dir |" + key for key in ("summary", "strengths", "implications", "scope")}
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
    # and only then the diagram's first lane, so nothing else sits between the summary and the diagram
    check("axis block sits between the summary text and the diagram", lines[:lines.index("**lane one**")], [
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
        "**lane one**"]), True)

    doc = _full_doc()
    doc["labels"] = {"strong": "\u5f37\u9805", "weak": "\u5f31\u9805"}
    lines = _md_lines(doc)
    check("overridden side labels lead the list lines", _has_block(lines, [
        "**axis one**", "- \u5f37\u9805 \u2014 strong side", "- \u5f31\u9805 \u2014 weak side", ""]), True)
    check("English side labels replaced", [ln for ln in lines if "Reliably" in ln], [])


def test_markdown_axis_escaping():
    axis = {"name": "C:\\dir axis", "strong": "C:\\dir strong", "weak": "C:\\dir weak", "patterns": ["s1"]}
    doc = _doc(summary={"text": "t", "axes": [axis]},
               labels={"strong": "C:\\dir good", "weak": "C:\\dir bad"})
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


def test_markdown_two_column_implications_table():
    lines = _md_lines(_full_doc())
    # written by hand: a heading, a two-column head, one row per implication, closed by a blank line before the scope
    check("implications table holds exactly the head and its row", _has_block(lines, [
        "## What this means for your work", "",
        "| Area | Related patterns |", "|---|---|",
        "| reviews | name s1; name g1 |", "",
        "## Scope"]), True)
    text = "\n".join(lines)
    check("meaning absent from the Markdown", "ask early" in text, False)
    check("meaning header absent from the Markdown", "What it means" in text, False)


def _implication_doc(labels=None, s1_name="name s1"):
    doc = _doc(strengths=[_pattern("s1", exceptions=[], name=s1_name)], gaps=[_pattern("g1")],
               implications=[{"area": "reviews", "meaning": "ask early", "patterns": ["s1", "g1"]}])
    if labels is not None:
        doc["labels"] = labels
    return doc


def test_markdown_list_separator():
    check("default list separator joins related names", "| reviews | name s1; name g1 |" in _md_lines(_implication_doc()),
          True)
    check("slash list separator joins related names",
          "| reviews | name s1 / name g1 |" in _md_lines(_implication_doc({"list_separator": " / "})), True)
    check("ideographic list separator joins related names",
          "| reviews | name s1\u3001name g1 |" in _md_lines(_implication_doc({"list_separator": "\u3001"})), True)
    # a comma inside a name would read as two names if the names were joined by a comma
    check("a name holding a comma stays one name",
          "| reviews | fast, careful; name g1 |" in _md_lines(_implication_doc(s1_name="fast, careful")), True)
    # the joined value is one cell, so a pipe in the separator must not open a new column.
    # written by hand: space, backslash, pipe, space
    check("pipe in the list separator escaped as a cell",
          "| reviews | name s1 \\| name g1 |" in _md_lines(_implication_doc({"list_separator": " | "})), True)

    doc = _implication_doc({"separator": " + "})
    doc["strengths"][0]["sources"] = ["code", "prompts"]
    lines = _md_lines(doc)
    # the sources separator shows up in its own column, so the fixture did override it
    check("sources separator applied to the sources column", "| name s1 | code + prompts | Verified |" in lines, True)
    check("sources separator leaves the related column alone", "| reviews | name s1; name g1 |" in lines, True)


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
          "<h2>Summary</h2><p>the summary</p>" + _one_step_figure("first", "one") + _one_step_figure("second", "two"))


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
    # written by hand: each lane under its bold title, numbered from 1, details in parentheses,
    # an arrow between steps carrying the note when there is one, a blank line after each lane, then the caption
    check("diagram listed lane by lane", _md_diagram_lines(_diagram_doc(d)), [
        "**lane one**",
        "1. step one (detail one; detail two)",
        "   \u2193 then",
        "2. step two",
        "   \u2193",
        "3. step three",
        "",
        "**lane two**",
        "1. step four",
        "   \u2193",
        "2. step five",
        "",
        "*a flow*",
        ""])
    check("untitled lane has no title line", _md_diagram_lines(_diagram_doc(_diagram([_lane([_step("only")])]))),
          ["1. only", "", "*a flow*", ""])
    check("empty title has no title line",
          _md_diagram_lines(_diagram_doc(_diagram([_lane([_step("only")], title="")]))), ["1. only", "", "*a flow*", ""])
    check("last-step note in no line", [ln for ln in _md_lines(_diagram_doc(d)) if "dropped" in ln], [])
    # a code fence is what the old text-drawn diagram needed, and the list form needs none
    check("no code fence anywhere", "```" in "\n".join(_md_lines(_full_doc())), False)


def test_markdown_diagram_escaping():
    d = _diagram([_lane([_step("C:\\dir text", detail=["C:\\dir one", "C:\\dir two"], note="C:\\dir note"),
                         _step("last")], title="C:\\dir title")], caption="C:\\dir caption")
    check("fixture: backslash diagram document is valid", rr.validate(_diagram_doc(d)), [])
    # written by hand: C, colon, two backslashes, then the rest of each value
    check("backslashes doubled in the diagram lines", _md_diagram_lines(_diagram_doc(d)), [
        "**C:\\\\dir title**",
        "1. C:\\\\dir text (C:\\\\dir one; C:\\\\dir two)",
        "   \u2193 C:\\\\dir note",
        "2. last",
        "",
        "*C:\\\\dir caption*",
        ""])

    d = _diagram([_lane([_step("step\none", detail=["detail\none", "detail\ntwo"], note="then\nnext"),
                         _step("last")], title="lane\none")], caption="a\nflow")
    # written by hand: each line break becomes one space, so a bold span, a list item, or the caption stays on one line
    check("line breaks flattened in the diagram lines", _md_diagram_lines(_diagram_doc(d)), [
        "**lane one**",
        "1. step one (detail one; detail two)",
        "   \u2193 then next",
        "2. last",
        "",
        "*a flow*",
        ""])

    # these lines are prose, not table cells, so a pipe in them is left as typed
    d = _diagram([_lane([_step("st|ep", detail=["de|tail one", "de|tail two"], note="th|en"), _step("last")],
                        title="la|ne")], caption="a |flow")
    check("pipes left raw in the diagram lines", _md_diagram_lines(_diagram_doc(d)), [
        "**la|ne**",
        "1. st|ep (de|tail one; de|tail two)",
        "   \u2193 th|en",
        "2. last",
        "",
        "*a |flow*",
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
              "**lane one**",
              "1. step one (detail one; detail two)",
              "   \u2193 then",
              "2. step two",
              "",
              "**lane two**",
              "1. step three",
              "",
              "*a flow*",
              "",
              "## Strengths"])

    first = _diagram([_lane([_step("first")])], caption="one")
    second = _diagram([_lane([_step("second")])], caption="two")
    check("several diagrams in document order", _md_diagram_lines(_doc(diagrams=[first, second])),
          ["1. first", "", "*one*", "", "1. second", "", "*two*", ""])


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
    # written by hand: strengths inside the strong side, gaps inside the weak side, ways of working after both sides
    check("axis links split by group", '<div class="axis"><h3>axis one</h3><div class="sides">'
          '<div class="side strong"><b>Reliably gets right</b>strong side'
          '<div class="links"><h4>Related patterns</h4><a href="#s2">name s2</a><br><a href="#s1">name s1</a></div></div>'
          '<div class="side weak"><b>Reliably misses</b>weak side'
          '<div class="links"><h4>Related patterns</h4><a href="#g2">name g2</a><br><a href="#g1">name g1</a></div></div>'
          '</div><div class="links"><h4>Ways of working</h4><a href="#y1">name y1</a><br><a href="#y2">name y2</a></div>'
          '</div>' in page, True)
    check("one links block per group", page.count('<div class="links">'), 3)

    page = _html(_axis_doc(["s1"]))
    # written by hand: only the strong side has an id, so the weak side and the space below the sides hold no links
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
    check("overridden styles label heads the ways of working links",
          '</div><div class="links"><h4>%s</h4><a href="#y1">name y1</a></div></div>' % e("label-styles") in page, True)
    check("no raw script tag from the link labels", "<script" in page, False)


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
        check("%s class on the section of its heading" % cls, '<section class="%s"><h2>%s</h2>' % (cls, heading) in page,
              True)
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
          test_markdown_two_column_implications_table, test_markdown_list_separator, test_markdown_report_file_label,
          test_validation_diagram_shape, test_validation_diagram_lanes, test_validation_diagram_steps,
          test_validation_diagram_paths, test_validation_diagram_problem_order, test_diagram_html_structure,
          test_diagram_html_arrows, test_diagram_html_placement, test_diagram_css, test_diagram_escaping,
          test_markdown_diagram_list, test_markdown_diagram_escaping, test_markdown_diagram_placement,
          test_badge_html, test_constraint_and_confidence_text, test_conditional_label_localised,
          test_evidence_depends_block, test_markdown_conditions_list, test_axis_link_split,
          test_section_and_table_classes,
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
