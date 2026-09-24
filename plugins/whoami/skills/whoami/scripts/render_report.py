"""Render a whoami report from its JSON document into one self-contained HTML file and a Markdown summary.

    python render_report.py <report.json> --out <report.html>

The HTML is written to --out, and the Markdown summary is printed to stdout for the session.
Exit status 2 means the document does not match the schema in report-schema.md, and nothing was written.
"""

import argparse
import html
import json
import os
import sys

INVALID = 2

SOURCES = ("code", "prompts", "instructions")
LEVELS = ("verified", "depends")
TONES = ("neutral", "strong", "weak")

LABELS = {
    "title": "whoami",
    "summary": "Summary",
    "strong": "Reliably gets right",
    "weak": "Reliably misses",
    "strengths": "Strengths",
    "gaps": "Gaps",
    "styles": "Ways of working",
    "implications": "What this means for your work",
    "scope": "Scope",
    "appendix": "Appendix",
    "dissolved": "Candidates the analysis refuted",
    "events": "Single events noticed along the way",
    "pattern": "Pattern",
    "description": "Description",
    "source": "Source",
    "confidence": "Confidence",
    "evidence": "Evidence",
    "gives": "What it gives",
    "costs": "What it costs",
    "area": "Area",
    "meaning": "What it means",
    "related": "Related patterns",
    "verified": "Verified",
    "depends": "Depends on",
    "instances": "Instances",
    "exceptions": "Exceptions",
    "calibration": "Against the rest of the repository",
    "checked": "What was checked",
    "report_file": "Full report",
    "code": "code",
    "prompts": "prompts",
    "instructions": "instructions",
    "separator": ", ",
    "list_separator": "; ",
}


def validate(doc):
    problems = []

    def need(obj, key, where, kind=str):
        value = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(value, kind) or (kind in (str, list) and not value):
            problems.append("%s: '%s' is missing or empty" % (where, key))
            return None
        return value

    if not isinstance(doc, dict):
        return ["the document is not a JSON object"]

    ids = set()
    for group in ("strengths", "gaps", "styles"):
        patterns = doc.get(group, [])
        if not isinstance(patterns, list):
            problems.append("'%s' is not a list" % group)
            continue
        for i, p in enumerate(patterns):
            where = "%s[%d]" % (group, i)
            # a model can emit a bare string where an object belongs, and that must list a problem, not raise
            if not isinstance(p, dict):
                problems.append("%s: a pattern must be an object" % where)
                continue
            pid = need(p, "id", where)
            if pid in ids:
                problems.append("%s: id '%s' is used twice" % (where, pid))
            ids.add(pid)
            need(p, "name", where)
            need(p, "description", where)
            for s in need(p, "sources", where, list) or []:
                if s not in SOURCES:
                    problems.append("%s: source '%s' is not one of %s" % (where, s, ", ".join(SOURCES)))
            confidence = need(p, "confidence", where, dict)
            if confidence is not None:
                if confidence.get("level") not in LEVELS:
                    problems.append("%s: confidence level must be one of %s" % (where, ", ".join(LEVELS)))
                # a pattern that depends on something must say what, or the column reads as a hedge
                if confidence.get("level") == "depends" and not confidence.get("on"):
                    problems.append("%s: a 'depends' confidence needs 'on'" % where)
            if not isinstance(p.get("instances"), list) or len(p["instances"]) < 2:
                problems.append("%s: a pattern needs at least two instances" % where)
            if group == "strengths" and not isinstance(p.get("exceptions"), list):
                problems.append("%s: a strength needs 'exceptions', empty only when none were found" % where)
            if group == "styles":
                need(p, "gives", where)
                need(p, "costs", where)

    summary = need(doc, "summary", "document", dict)
    if summary is not None:
        need(summary, "text", "summary")
        axes = summary.get("axes", [])
        if not isinstance(axes, list):
            problems.append("summary: 'axes' is not a list")
            axes = []
        for i, axis in enumerate(axes):
            where = "summary.axes[%d]" % i
            if not isinstance(axis, dict):
                problems.append("%s: an axis must be an object" % where)
                continue
            for key in ("name", "strong", "weak"):
                need(axis, key, where)
            for ref in axis.get("patterns", []):
                if ref not in ids:
                    problems.append("%s: pattern '%s' does not exist" % (where, ref))

    implications = doc.get("implications", [])
    if not isinstance(implications, list):
        problems.append("'implications' is not a list")
        implications = []
    for i, row in enumerate(implications):
        where = "implications[%d]" % i
        if not isinstance(row, dict):
            problems.append("%s: an implication must be an object" % where)
            continue
        need(row, "area", where)
        need(row, "meaning", where)
        for ref in row.get("patterns", []):
            if ref not in ids:
                problems.append("%s: pattern '%s' does not exist" % (where, ref))

    diagrams = doc.get("diagrams", [])
    if not isinstance(diagrams, list):
        problems.append("'diagrams' is not a list")
        diagrams = []
    for i, d in enumerate(diagrams):
        where = "diagrams[%d]" % i
        # a text-drawn diagram misaligns once it holds CJK, so a diagram is only accepted as structure
        lanes = need(d, "lanes", where, list) or []
        # the Markdown summary lists each lane's steps as a numbered list, and a caption or a lane title
        # is the only line that keeps one list from running on into the next
        need(d, "caption", where)
        if len(lanes) > 3:
            problems.append("%s: a diagram holds at most three lanes side by side" % where)
        for j, lane in enumerate(lanes):
            lane_where = "%s.lanes[%d]" % (where, j)
            if len(lanes) > 1:
                need(lane, "title", lane_where)
            for k, step in enumerate(need(lane, "steps", lane_where, list) or []):
                step_where = "%s.steps[%d]" % (lane_where, k)
                need(step, "text", step_where)
                if not isinstance(step, dict):
                    continue
                if step.get("tone", "neutral") not in TONES:
                    problems.append("%s: tone must be one of %s" % (step_where, ", ".join(TONES)))
                detail = step.get("detail", [])
                if not isinstance(detail, list) or not all(isinstance(x, str) for x in detail):
                    problems.append("%s: 'detail' is not a list of text" % step_where)

    need(doc, "scope", "document", dict)
    return problems


def labels_for(doc):
    merged = dict(LABELS)
    merged.update({k: v for k, v in (doc.get("labels") or {}).items() if isinstance(v, str)})
    return merged


def esc(text):
    return html.escape(str(text), quote=True)


def names_by_id(doc):
    return {p["id"]: p["name"] for g in ("strengths", "gaps", "styles") for p in doc.get(g, [])}


def confidence_text(confidence, lab):
    if confidence["level"] == "verified":
        return lab["verified"]
    return "%s: %s" % (lab["depends"], confidence["on"])


def sources_text(sources, lab):
    return lab["separator"].join(lab.get(s, s) for s in sources)


def instance_items(items):
    out = []
    for item in items:
        if isinstance(item, dict):
            ref = item.get("ref")
            out.append("<li>%s%s</li>" % (
                esc(item.get("text", "")),
                ' <span class="ref">%s</span>' % esc(ref) if ref else ""))
        else:
            out.append("<li>%s</li>" % esc(item))
    return "".join(out)


def evidence_html(p, lab):
    parts = ["<h4>%s</h4><ul>%s</ul>" % (esc(lab["instances"]), instance_items(p.get("instances", [])))]
    if p.get("exceptions"):
        parts.append("<h4>%s</h4><ul>%s</ul>" % (esc(lab["exceptions"]), instance_items(p["exceptions"])))
    for key in ("calibration", "checked"):
        if p.get(key):
            parts.append("<h4>%s</h4><p>%s</p>" % (esc(lab[key]), esc(p[key])))
    return "<details><summary>%s</summary>%s</details>" % (esc(lab["evidence"]), "".join(parts))


def pattern_table(patterns, lab, style=False):
    head = [lab["pattern"], lab["description"]]
    if style:
        head += [lab["gives"], lab["costs"]]
    head += [lab["source"], lab["confidence"]]
    rows = []
    for p in patterns:
        # the evidence sits under the description, so the table keeps its narrow columns readable
        cells = ['<th scope="row" id="%s">%s</th>' % (esc(p["id"]), esc(p["name"])),
                 "<td>%s%s</td>" % (esc(p["description"]), evidence_html(p, lab))]
        if style:
            cells += ["<td>%s</td>" % esc(p["gives"]), "<td>%s</td>" % esc(p["costs"])]
        level = p["confidence"]["level"]
        cells += [
            '<td class="nowrap">%s</td>' % esc(sources_text(p["sources"], lab)),
            '<td class="conf"><span class="badge %s">%s</span></td>' % (level, esc(confidence_text(p["confidence"], lab))),
        ]
        rows.append("<tr>%s</tr>" % "".join(cells))
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
        "".join("<th>%s</th>" % esc(h) for h in head), "".join(rows))


def pattern_links(refs, names):
    return "<br>".join('<a href="#%s">%s</a>' % (esc(r), esc(names[r])) for r in refs if r in names)


def scope_rows(scope):
    rows = []
    for key, value in scope.items():
        if isinstance(value, list):
            value = "; ".join(json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v) for v in value)
        rows.append('<tr><th scope="row">%s</th><td>%s</td></tr>' % (esc(key), esc(value)))
    return "".join(rows)


CSS = """
:root { --bg:#fbfaf7; --fg:#1f2328; --muted:#5b6470; --line:#d9dce1; --card:#ffffff;
  --strong:#1f7a4d; --strong-bg:#e6f4ec; --weak:#a4461b; --weak-bg:#fbeee6; --dep:#8a6200; --dep-bg:#fbf3dc; }
@media (prefers-color-scheme: dark) { :root { --bg:#16181c; --fg:#e6e8eb; --muted:#9aa3ad; --line:#30353c; --card:#1d2025;
  --strong:#6fd19f; --strong-bg:#173325; --weak:#f0a37c; --weak-bg:#3a2419; --dep:#e6c46a; --dep-bg:#352d14; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.6 system-ui,-apple-system,"Segoe UI","Noto Sans TC","Microsoft JhengHei",sans-serif; }
main { max-width:1100px; margin:0 auto; padding:32px 16px 64px; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:20px; margin:40px 0 12px; border-bottom:1px solid var(--line); padding-bottom:6px; }
h4 { margin:12px 0 4px; font-size:13px; color:var(--muted); }
.scopeline, .ref { color:var(--muted); font-size:13px; }
.summary p { font-size:16px; }
.axes { display:grid; gap:16px; }
.axis { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }
.axis h3 { margin:0 0 10px; font-size:16px; }
.sides { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.side { border-radius:8px; padding:12px; } .side.strong { background:var(--strong-bg); } .side.weak { background:var(--weak-bg); }
.side b { display:block; font-size:12px; letter-spacing:.04em; margin-bottom:4px; }
.side.strong b { color:var(--strong); } .side.weak b { color:var(--weak); }
.axis .links { margin-top:10px; font-size:13px; color:var(--muted); }
figure { margin:16px 0; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; overflow-x:auto; }
figure.flow { padding:16px; }
.lanes { display:grid; gap:20px; } .lanes.n2 { grid-template-columns:1fr 1fr; } .lanes.n3 { grid-template-columns:1fr 1fr 1fr; }
.lane { display:flex; flex-direction:column; align-items:stretch; }
.lane-title { font-weight:600; font-size:14px; margin-bottom:8px; color:var(--muted); }
.step { border:1px solid var(--line); border-radius:8px; padding:10px 14px; background:var(--bg); }
.step.strong { background:var(--strong-bg); border-color:transparent; } .step.weak { background:var(--weak-bg); border-color:transparent; }
.step-text { font-weight:600; } .step ul { margin:6px 0 0; padding-left:18px; font-size:14px; }
.arrow { display:flex; align-items:center; gap:10px; padding:4px 0 4px 24px; min-height:30px; }
.arrow-mark { font-size:18px; color:var(--muted); line-height:1; } .arrow-note { font-size:13px; color:var(--muted); }
figcaption { color:var(--muted); font-size:13px; margin-top:8px; }
.tablewrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px; }
th, td { text-align:left; vertical-align:top; padding:10px 12px; border-top:1px solid var(--line); }
thead th { border-top:none; font-size:13px; color:var(--muted); font-weight:600; }
tbody th { font-weight:600; min-width:160px; }
td.nowrap { white-space:nowrap; } td.conf { min-width:120px; }
td details { margin-top:6px; }
/* the implications table is the only one whose third cell is its last, and that cell lists pattern names that wrap badly when narrow */
tbody td:nth-child(3):last-child { min-width:260px; }
.badge { display:inline-block; border-radius:999px; padding:2px 10px; font-size:12px; white-space:nowrap; }
.badge.verified { background:var(--strong-bg); color:var(--strong); } .badge.depends { background:var(--dep-bg); color:var(--dep); white-space:normal; }
details summary { cursor:pointer; color:var(--muted); font-size:13px; }
details ul { margin:4px 0; padding-left:18px; }
a { color:inherit; }
@media (max-width:700px) { .sides, .lanes.n2, .lanes.n3 { grid-template-columns:1fr; } }
"""


def render_html(doc, lab):
    names = names_by_id(doc)
    summary = doc["summary"]
    body = ['<h1>%s</h1>' % esc(doc.get("title") or lab["title"])]
    if doc.get("scope_line"):
        body.append('<p class="scopeline">%s</p>' % esc(doc["scope_line"]))

    body.append('<section class="summary"><h2>%s</h2>' % esc(lab["summary"]))
    body += ["<p>%s</p>" % esc(par) for par in str(summary["text"]).split("\n\n")]
    axes = []
    for axis in summary.get("axes", []):
        axes.append(
            '<div class="axis"><h3>%s</h3>%s<div class="sides"><div class="side strong"><b>%s</b>%s</div>'
            '<div class="side weak"><b>%s</b>%s</div></div>%s</div>' % (
                esc(axis["name"]),
                "<p>%s</p>" % esc(axis["description"]) if axis.get("description") else "",
                esc(lab["strong"]), esc(axis["strong"]), esc(lab["weak"]), esc(axis["weak"]),
                '<div class="links">%s<br>%s</div>' % (esc(lab["related"]), pattern_links(axis.get("patterns", []), names))
                if axis.get("patterns") else ""))
    if axes:
        body.append('<div class="axes">%s</div>' % "".join(axes))
    for d in doc.get("diagrams", []):
        body.append(diagram_html(d))
    body.append("</section>")

    for group, style in (("strengths", False), ("gaps", False), ("styles", True)):
        if doc.get(group):
            body.append('<section><h2>%s</h2><div class="tablewrap">%s</div></section>' % (
                esc(lab[group]), pattern_table(doc[group], lab, style)))

    if doc.get("implications"):
        rows = "".join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td></tr>' % (
            esc(r["area"]), esc(r["meaning"]), pattern_links(r.get("patterns", []), names)) for r in doc["implications"])
        body.append('<section><h2>%s</h2><div class="tablewrap"><table><thead><tr><th>%s</th><th>%s</th><th>%s</th></tr></thead>'
                    '<tbody>%s</tbody></table></div></section>' % (
                        esc(lab["implications"]), esc(lab["area"]), esc(lab["meaning"]), esc(lab["related"]), rows))

    body.append('<section><h2>%s</h2><div class="tablewrap"><table><tbody>%s</tbody></table></div></section>' % (
        esc(lab["scope"]), scope_rows(doc["scope"])))

    appendix = []
    if doc.get("dissolved"):
        appendix.append("<h4>%s</h4><ul>%s</ul>" % (esc(lab["dissolved"]), "".join(
            "<li><b>%s</b>: %s</li>" % (esc(d.get("name", "")), esc(d.get("evidence", ""))) for d in doc["dissolved"])))
    if doc.get("events"):
        appendix.append("<h4>%s</h4><ul>%s</ul>" % (esc(lab["events"]), instance_items(doc["events"])))
    if appendix:
        body.append("<section><h2>%s</h2><details><summary>%s</summary>%s</details></section>" % (
            esc(lab["appendix"]), esc(lab["appendix"]), "".join(appendix)))

    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>%s</title><style>%s</style></head><body><main>%s</main></body></html>\n") % (
        esc(doc.get("language", "en")), esc(doc.get("title") or lab["title"]), CSS, "".join(body))


def diagram_html(d):
    # boxes laid out by CSS wrap their own text, where a text-drawn box assumes a width per character that a CJK fallback font does not keep
    lanes = []
    for lane in d.get("lanes", []):
        parts = []
        steps = lane.get("steps", [])
        for i, step in enumerate(steps):
            detail = "".join("<li>%s</li>" % esc(x) for x in step.get("detail", []))
            parts.append('<div class="step %s"><div class="step-text">%s</div>%s</div>' % (
                esc(step.get("tone", "neutral")), esc(step.get("text", "")), "<ul>%s</ul>" % detail if detail else ""))
            if i < len(steps) - 1:
                note = step.get("note")
                parts.append('<div class="arrow"><span class="arrow-mark">&#8595;</span>%s</div>' % (
                    '<span class="arrow-note">%s</span>' % esc(note) if note else ""))
        title = '<div class="lane-title">%s</div>' % esc(lane["title"]) if lane.get("title") else ""
        lanes.append('<div class="lane">%s%s</div>' % (title, "".join(parts)))
    caption = "<figcaption>%s</figcaption>" % esc(d["caption"]) if d.get("caption") else ""
    return '<figure class="flow"><div class="lanes n%d">%s</div>%s</figure>' % (len(lanes), "".join(lanes), caption)


def diagram_markdown(d):
    # a terminal cannot draw a box whose border lines up across CJK text, so the summary lists the steps instead
    lines = []
    for lane in d.get("lanes", []):
        if lane.get("title"):
            lines.append("**%s**" % md_text(lane["title"]).replace("\n", " "))
        steps = lane.get("steps", [])
        for i, step in enumerate(steps):
            text = md_text(step.get("text", "")).replace("\n", " ")
            detail = "; ".join(md_text(x).replace("\n", " ") for x in step.get("detail", []))
            lines.append("%d. %s%s" % (i + 1, text, " (%s)" % detail if detail else ""))
            if i < len(steps) - 1:
                lines.append("   \u2193 %s" % md_text(step["note"]).replace("\n", " ") if step.get("note") else "   \u2193")
        lines.append("")
    if d.get("caption"):
        lines += ["*%s*" % md_text(d["caption"]).replace("\n", " "), ""]
    return lines


def md_cell(text):
    # a pipe or a line break inside a cell would split the Markdown table,
    # and a backslash would escape the character after it, as in a Windows path
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def md_text(text):
    # the document is plain text, so a backslash in prose is literal and must not escape what follows it
    return str(text).replace("\\", "\\\\")


def render_markdown(doc, lab, out_path):
    names = names_by_id(doc)
    summary = doc["summary"]
    lines = ["## %s" % md_text(lab["summary"]), "", md_text(summary["text"]), ""]
    # a terminal table misaligns once a long cell wraps, so the summary keeps long text out of tables
    # and leaves descriptions, trade-offs, and meanings to the HTML
    for a in summary.get("axes", []):
        # a line break would end the bold span or the list item early, so each axis value stays on one line
        one = lambda text: md_text(text).replace("\n", " ")
        lines += ["**%s**" % one(a["name"]),
                  "- %s — %s" % (one(lab["strong"]), one(a["strong"])),
                  "- %s — %s" % (one(lab["weak"]), one(a["weak"])), ""]
    for d in doc.get("diagrams", []):
        lines += diagram_markdown(d)
    for group in ("strengths", "gaps", "styles"):
        if not doc.get(group):
            continue
        lines += ["## %s" % md_text(lab[group]), "",
                  "| %s | %s | %s |" % (md_cell(lab["pattern"]), md_cell(lab["source"]), md_cell(lab["confidence"])),
                  "|---|---|---|"]
        lines += ["| %s | %s | %s |" % (md_cell(p["name"]), md_cell(sources_text(p["sources"], lab)),
                                       md_cell(confidence_text(p["confidence"], lab))) for p in doc[group]]
        lines.append("")
    if doc.get("implications"):
        lines += ["## %s" % md_text(lab["implications"]), "",
                  "| %s | %s |" % (md_cell(lab["area"]), md_cell(lab["related"])), "|---|---|"]
        # pattern names often hold a comma, so the names are joined by a separator of their own
        lines += ["| %s | %s |" % (md_cell(r["area"]),
                                  md_cell(lab["list_separator"].join(names[x] for x in r.get("patterns", []) if x in names)))
                  for r in doc["implications"]]
        lines.append("")
    if doc.get("scope_line"):
        lines += ["## %s" % md_text(lab["scope"]), "", md_text(doc["scope_line"]), ""]
    # a code span keeps a Windows path whole, where plain Markdown reads "\." as an escaped dot
    lines.append("%s: `%s`" % (md_text(lab["report_file"]), out_path))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    # a Windows console defaults to cp1252, which cannot encode most reports written outside English
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    try:
        with open(args.report, encoding="utf-8") as handle:
            doc = json.load(handle)
    except OSError as error:
        sys.stderr.write("the report could not be read: %s\n" % error)
        return INVALID
    except ValueError as error:
            sys.stderr.write("the report is not valid JSON: %s\n" % error)
            return INVALID

    problems = validate(doc)
    if problems:
        sys.stderr.write("the report does not match the schema, so nothing was written:\n")
        sys.stderr.write("".join("- %s\n" % p for p in problems))
        return INVALID

    lab = labels_for(doc)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_html(doc, lab))
    sys.stdout.write(render_markdown(doc, lab, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
