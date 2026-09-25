"""Render a whoami report from its JSON document into one self-contained HTML file and a Markdown summary.

    python render_report.py <report.json> --out <report.html>

The HTML is written to --out, and the Markdown summary is printed to stdout for the session.
Exit status 2 means the document does not match the schema in report-schema.md, and nothing was written.
"""

import argparse
import datetime
import html
import json
import os
import re
import sys

INVALID = 2
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# seven to forty hex characters holding both a digit and a letter, so words such as "facade" and numbers such as
# "2026" do not match, bounded by lookarounds because CJK text puts no word boundary before a SHA
SHA = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9A-Za-z])")

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
    "colon": ": ",
    "conditional": "Conditional",
    "timeline_thin": "Read thinly",
    "timeline_close": "Read closely",
    "timeline_ai": "AI shows up",
    "timeline_prompts": "Prompts",
    "theme": "Colour scheme",
    "theme_auto": "Auto",
    "theme_light": "Light",
    "theme_dark": "Dark",
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

    if doc.get("timeline") is not None:
        problems += validate_timeline(doc["timeline"])

    # the plain-language rule was broken in two trial runs, so the one identifier that can be told
    # from ordinary words is checked rather than asked for
    for where, text in visible_text(doc):
        found = SHA.search(text)
        if found:
            problems.append("%s: '%s' looks like a commit SHA; say what happened, and keep identifiers in an instance's ref"
                            % (where, found.group(0)))

    labels = doc.get("labels")
    if labels is not None and not isinstance(labels, dict):
        problems.append("'labels' is not an object")
    elif labels:
        missing = [key for key in LABELS if key != "title" and not isinstance(labels.get(key), str)]
        # a label left out falls back to English, which in a report written in another language reads as a slip
        if missing:
            problems.append("labels: %s missing; give every label or none" % ", ".join(missing))

    need(doc, "scope", "document", dict)
    return problems


def visible_text(doc):
    """Every field a reader sees before expanding anything, as (where, text).
    The evidence, the appendix, and the scope table are left out, since they are where identifiers belong."""
    # validate reports a container of the wrong shape on its own, so the walk skips it rather than raise
    listed = lambda value: value if isinstance(value, list) else []
    fields = [("document", doc.get(key)) for key in ("title", "scope_line")]
    summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
    fields.append(("summary", summary.get("text")))
    for i, axis in enumerate(listed(summary.get("axes"))):
        if isinstance(axis, dict):
            fields += [("summary.axes[%d]" % i, axis.get(k)) for k in ("name", "description", "strong", "weak")]
    for i, d in enumerate(listed(doc.get("diagrams"))):
        if not isinstance(d, dict):
            continue
        fields.append(("diagrams[%d]" % i, d.get("caption")))
        for j, lane in enumerate(listed(d.get("lanes"))):
            if not isinstance(lane, dict):
                continue
            where = "diagrams[%d].lanes[%d]" % (i, j)
            fields.append((where, lane.get("title")))
            for k, step in enumerate(listed(lane.get("steps"))):
                if isinstance(step, dict):
                    step_where = "%s.steps[%d]" % (where, k)
                    detail = listed(step.get("detail"))
                    fields += [(step_where, x) for x in [step.get("text"), step.get("note")] + detail]
    for group in ("strengths", "gaps", "styles"):
        for i, p in enumerate(listed(doc.get(group))):
            if isinstance(p, dict):
                where = "%s[%d]" % (group, i)
                confidence = p.get("confidence") if isinstance(p.get("confidence"), dict) else {}
                fields += [(where, p.get(k)) for k in ("name", "description", "gives", "costs")]
                fields.append((where, confidence.get("on")))
    for i, row in enumerate(listed(doc.get("implications"))):
        if isinstance(row, dict):
            fields += [("implications[%d]" % i, row.get(k)) for k in ("area", "meaning")]
    return [(where, text) for where, text in fields if isinstance(text, str)]


def validate_timeline(timeline):
    problems = []

    def day(obj, key, where, required=True):
        value = obj.get(key)
        if value is None and not required:
            return None
        # fromisoformat alone also takes forms such as 20260102, which a reader of the JSON would not expect
        if isinstance(value, str) and DATE.match(value):
            try:
                return datetime.date.fromisoformat(value)
            except ValueError:
                pass
        problems.append("%s: '%s' is not a date written YYYY-MM-DD" % (where, key))
        return None

    def span(obj, where):
        start, end = day(obj, "from", where), day(obj, "to", where)
        if start and end and start > end:
            problems.append("%s: 'from' is after 'to'" % where)

    if not isinstance(timeline, dict):
        return ["'timeline' is not an object"]
    repositories = timeline.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        problems.append("timeline: 'repositories' is missing or empty")
        repositories = []
    for i, repo in enumerate(repositories):
        where = "timeline.repositories[%d]" % i
        if not isinstance(repo, dict):
            problems.append("%s: a repository must be an object" % where)
            continue
        if not isinstance(repo.get("name"), str) or not repo["name"]:
            problems.append("%s: 'name' is missing or empty" % where)
        span(repo, where)
        day(repo, "ai_from", where, required=False)
    day(timeline, "recent_from", "timeline", required=False)
    prompts = timeline.get("prompts")
    if prompts is not None:
        if isinstance(prompts, dict):
            span(prompts, "timeline.prompts")
        else:
            problems.append("timeline: 'prompts' is not an object")
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
    # a table column that holds a whole constraint grows to fit it, so the column holds a short label
    # and the constraint travels with it as a tooltip and in the evidence
    if confidence["level"] == "verified":
        return lab["verified"]
    return lab["conditional"]


def constraint_text(confidence, lab):
    return "%s%s%s" % (lab["depends"], lab["colon"], confidence["on"]) if confidence["level"] == "depends" else ""


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
    if p["confidence"]["level"] == "depends":
        parts.append("<h4>%s</h4><p>%s</p>" % (esc(lab["depends"]), esc(p["confidence"]["on"])))
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
            '<td class="conf">%s</td>' % badge_html(p["confidence"], lab),
        ]
        rows.append("<tr>%s</tr>" % "".join(cells))
    return '<table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (
        "patterns styles" if style else "patterns", "".join("<th>%s</th>" % esc(h) for h in head), "".join(rows))


def badge_html(confidence, lab):
    level = confidence["level"]
    if level == "verified":
        return '<span class="badge verified">%s</span>' % esc(confidence_text(confidence, lab))
    tip = esc(constraint_text(confidence, lab))
    # tabindex lets a keyboard reach the tooltip, which a title attribute alone never shows
    return '<span class="badge depends" tabindex="0" title="%s" data-tip="%s">%s</span>' % (
        tip, tip, esc(confidence_text(confidence, lab)))


def pattern_links(refs, names):
    return "<br>".join('<a href="#%s">%s</a>' % (esc(r), esc(names[r])) for r in refs if r in names)


def scope_rows(scope):
    rows = []
    for key, value in scope.items():
        if isinstance(value, list):
            value = "; ".join(json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v) for v in value)
        rows.append('<tr><th scope="row">%s</th><td>%s</td></tr>' % (esc(key), esc(value)))
    return "".join(rows)


LIGHT = ("color-scheme:light; --paper:#eef1f4; --sheet:#ffffff; --ink:#17202b; --muted:#4f5a68; --rule:#dde2e8;\n"
         "  --right:#157a45; --right-wash:#dcf5e6; --miss:#a65306; --miss-wash:#fdefd6; --focus:#2563eb; --tip:#17202b; --tip-ink:#ffffff;\n"
         "  --tl-bar:#5b6b7f; --tl-prompt:#2f6fe0;")
DARK = ("color-scheme:dark; --paper:#1b2027; --sheet:#232a33; --ink:#eef2f6; --muted:#bcc5d0; --rule:#38414d;\n"
        "  --right:#72e0a4; --right-wash:#1f5c3d; --miss:#f5b54a; --miss-wash:#634a1a; --focus:#8ab4ff; --tip:#eef2f6; --tip-ink:#17202b;\n"
        "  --tl-bar:#a9b7c6; --tl-prompt:#8ab4ff;")

# the switch overrides the system setting in either direction, and Auto leaves the system in charge.
# A checked radio is read by :has(), so the page needs no script and remembers nothing between openings.
THEMES = (":root { %s }\n@media (prefers-color-scheme: dark) { :root { %s } }\n"
          ":root:has(#theme-light:checked) { %s }\n:root:has(#theme-dark:checked) { %s }\n") % (LIGHT, DARK, LIGHT, DARK)

CSS = THEMES + """* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.7 "Segoe UI Variable Text","Segoe UI",-apple-system,"PingFang TC","Microsoft JhengHei","Noto Sans CJK TC",sans-serif; }
main { max-width:1080px; margin:32px auto; padding:44px 52px 56px; background:var(--sheet); border-radius:12px; }
h1 { font:400 34px/1.25 "Iowan Old Style","Palatino Linotype","Book Antiqua",Georgia,"Noto Serif CJK TC","Songti TC",serif; margin:0 0 10px; letter-spacing:-.01em; }
.scopeline { color:var(--muted); font-size:14px; margin:0; max-width:96ch; }
h1, .scopeline { overflow-wrap:anywhere; }
section { margin-top:40px; padding-top:28px; border-top:1px solid var(--rule); }
section > h2 { display:flex; align-items:center; gap:10px; margin:0 0 18px; font-size:21px; font-weight:600; line-height:1.3; }
section > h2::before { content:""; flex:none; width:24px; height:24px; background:var(--muted);
  -webkit-mask:var(--icon) center/contain no-repeat; mask:var(--icon) center/contain no-repeat; }
section.strengths > h2::before { background:var(--right); } section.gaps > h2::before { background:var(--miss); }
section.summary { --icon:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='9'/%3E%3Ccircle cx='12' cy='12' r='4.5'/%3E%3Ccircle cx='12' cy='12' r='0.8'/%3E%3C/svg%3E"); }
section.strengths { --icon:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='9'/%3E%3Cpath d='M8 12.5l2.8 2.8L16.5 9'/%3E%3C/svg%3E"); }
section.gaps { --icon:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 3.5l9 16H3z'/%3E%3Cpath d='M12 10v4.5'/%3E%3Cpath d='M12 17.3v.2'/%3E%3C/svg%3E"); }
section.styles { --icon:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 4v16M6 20h12M5 7h14'/%3E%3Cpath d='M5 7l-2.5 5.5h5z'/%3E%3Cpath d='M19 7l-2.5 5.5h5z'/%3E%3C/svg%3E"); }
section.implications { --icon:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='9'/%3E%3Cpath d='M8 12h8M13 8.5l3.5 3.5-3.5 3.5'/%3E%3C/svg%3E"); }
section.scope { --icon:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='10.5' cy='10.5' r='6.5'/%3E%3Cpath d='M15.5 15.5L20 20'/%3E%3C/svg%3E"); }
section.appendix { --icon:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='4' width='18' height='5' rx='1'/%3E%3Cpath d='M5 9v10h14V9M10 13h4'/%3E%3C/svg%3E"); }
.summary p { max-width:70ch; margin:0 0 14px; font-size:17px; }
h3 { font:400 23px/1.35 "Iowan Old Style","Palatino Linotype","Book Antiqua",Georgia,"Noto Serif CJK TC","Songti TC",serif; margin:0 0 6px; }
h4 { margin:14px 0 4px; font-size:13px; font-weight:600; color:var(--muted); }
.axes { display:grid; gap:20px; margin:30px 0 8px; }
/* a frame keeps an axis's name, description and two sides together, which a second axis made hard to see */
.axis { border:1px solid var(--rule); border-radius:12px; padding:20px 22px 22px; }
.axis p { margin:0 0 14px; max-width:70ch; }
.sides { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.side { border-radius:10px; padding:14px 18px 16px; }
.side.strong { background:var(--right-wash); } .side.weak { background:var(--miss-wash); }
.side b { display:block; font-size:13px; font-weight:700; margin-bottom:4px; }
.side.strong b { color:var(--right); } .side.weak b { color:var(--miss); }
.axis .links { margin-top:12px; font-size:13px; line-height:1.8; }
.axis .links h4 { margin:0 0 2px; }
.side .links { padding-top:10px; border-top:1px solid color-mix(in srgb, currentColor 22%, transparent); }
figure { margin:36px 0 0; }
figcaption { font-size:16px; font-weight:600; line-height:1.45; margin:0 0 14px; max-width:70ch; }
/* the measure is in ch, the width of a Latin digit, and a CJK character takes about two,
   so a CJK report fills the column where a Latin one keeps its line length */
:is(.summary p, .axis p, figcaption, .scopeline):is(:lang(zh), :lang(ja), :lang(ko)) { max-width:none; }
.lanes { display:grid; gap:24px; } .lanes.n2 { grid-template-columns:1fr 1fr; } .lanes.n3 { grid-template-columns:1fr 1fr 1fr; }
.lane { display:flex; flex-direction:column; align-items:stretch; }
.lane-title { font-weight:600; font-size:14px; margin-bottom:8px; color:var(--muted); }
.step { border:1px solid var(--rule); border-radius:10px; padding:10px 14px; background:var(--sheet); }
.step.strong { background:var(--right-wash); border-color:transparent; }
.step.weak { background:var(--miss-wash); border-color:transparent; }
.step-text { font-weight:600; } .step ul { margin:4px 0 0; padding-left:18px; font-size:14px; color:var(--muted); }
.arrow { display:flex; align-items:stretch; gap:14px; padding-left:24px; min-height:48px; }
/* a drawn line stretches with a wrapping note, where a glyph stays one character tall */
.arrow-mark { position:relative; flex:none; width:2px; margin:4px 0 11px; background:var(--muted); font-size:0; }
.arrow-mark::after { content:""; position:absolute; left:-5px; bottom:-9px; border-style:solid; border-width:9px 6px 0; border-color:var(--muted) transparent transparent; }
.arrow-note { align-self:center; padding:6px 0; font-size:13px; color:var(--muted); }
table { width:100%; border-collapse:collapse; font-size:15px; table-layout:fixed; }
th, td { text-align:left; vertical-align:top; padding:12px 14px 12px 0; border-bottom:1px solid var(--rule); overflow-wrap:anywhere; }
thead th { font-size:13px; font-weight:600; color:var(--muted); padding-top:0; }
tbody th { font-weight:600; }
table.patterns thead th:nth-child(1) { width:24%; } table.patterns thead th:nth-last-child(2) { width:11%; } table.patterns thead th:last-child { width:13%; }
table.styles thead th:nth-child(1) { width:18%; } table.styles thead th:nth-child(3), table.styles thead th:nth-child(4) { width:17%; }
table.implications thead th:nth-child(1) { width:24%; } table.implications thead th:nth-child(3) { width:30%; }
table.scope tbody th { width:24%; }
td details { margin-top:6px; }
.badge { font-size:13px; white-space:nowrap; }
/* solid against dashed tells the two apart without relying on colour */
.badge { display:inline-block; border:1px solid currentColor; border-radius:6px; padding:0 7px; }
.badge.verified { color:var(--right); }
.badge.depends { position:relative; color:var(--miss); border-style:dashed; cursor:help; }
.badge.depends:hover::after, .badge.depends:focus::after { content:attr(data-tip); position:absolute; right:0; top:calc(100% + 6px); z-index:5;
  width:max-content; max-width:280px; white-space:normal; padding:8px 10px; border-radius:8px; background:var(--tip); color:var(--tip-ink); font-size:13px; line-height:1.5; }
details summary { cursor:pointer; color:var(--muted); font-size:13px; }
details[open] > summary { margin-bottom:4px; }
details ul { margin:4px 0; padding-left:18px; }
.ref { color:var(--muted); font-size:12px; font-family:ui-monospace,"Cascadia Mono",Consolas,monospace; }
a { color:inherit; text-decoration-color:var(--rule); text-underline-offset:3px; }
a:hover { text-decoration-color:currentColor; }
summary:focus-visible, a:focus-visible, .badge:focus-visible { outline:2px solid var(--focus); outline-offset:2px; border-radius:4px; }
figure.timeline { margin:24px 0 0; font-size:13px; color:var(--muted); }
.tl-row { display:grid; grid-template-columns:11rem 1fr; gap:14px; align-items:center; min-height:24px; }
.tl-name { color:var(--ink); line-height:1.35; overflow-wrap:anywhere; }
.tl-track { position:relative; height:12px; }
/* a span of a day or two would draw nothing at this scale, so every segment keeps a visible width */
.tl-seg { position:absolute; top:2px; height:8px; min-width:6px; border-radius:4px; }
.tl-seg.close { background:var(--tl-bar); }
/* hatched against solid tells the thin sample from the close reading without relying on colour */
.tl-seg.thin { border:1px solid var(--tl-bar); background:repeating-linear-gradient(135deg, var(--tl-bar) 0 1.5px, transparent 1.5px 5px); }
.tl-seg.prompts { background:var(--tl-prompt); }
.tl-ai { position:absolute; top:-4px; height:20px; border-left:2px solid var(--ink); margin-left:-1px; }
.tl-ai::before { content:""; position:absolute; left:-5px; top:-3px; width:8px; height:8px; background:var(--ink); transform:rotate(45deg); }
.tl-axis { min-height:20px; } .tl-axis .tl-track { height:18px; border-top:1px solid var(--rule); }
.tl-tick { position:absolute; top:2px; transform:translateX(-50%); white-space:nowrap; font-size:12px; }
.tl-tick.start { left:0; transform:none; } .tl-tick.end { right:0; transform:none; }
.tl-legend { display:flex; flex-wrap:wrap; gap:4px 18px; margin:6px 0 0 calc(11rem + 14px); font-size:13px; font-weight:400; color:var(--muted); max-width:none; }
.tl-key { display:inline-flex; align-items:center; gap:7px; }
.tl-key .tl-seg { position:static; display:inline-block; width:22px; }
.tl-key .tl-ai { position:relative; top:0; height:14px; margin:0 3px 0 5px; }
.theme { float:right; display:flex; margin:4px 0 12px 20px; border:1px solid var(--rule); border-radius:8px; overflow:hidden; font-size:13px; }
/* the radios stay in the page for the keyboard and screen readers, and their labels are what a reader sees and clicks */
.theme input { position:absolute; opacity:0; width:1px; height:1px; }
.theme label { padding:3px 11px; color:var(--muted); cursor:pointer; line-height:1.6; }
.theme label + input + label { border-left:1px solid var(--rule); }
.theme input:checked + label { background:var(--rule); color:var(--ink); font-weight:600; }
.theme input:focus-visible + label { outline:2px solid var(--focus); outline-offset:-2px; }
@media print { .theme { display:none; } }
@media (max-width:900px) { .sides, .lanes.n2, .lanes.n3 { grid-template-columns:1fr; } }
@media (max-width:560px) { .tl-row { grid-template-columns:1fr; gap:2px; margin-bottom:6px; } .tl-axis .tl-name { display:none; }
  .tl-legend { margin-left:0; } .tl-tick.minor { display:none; } }
@media (max-width:760px) { main { margin:0; padding:28px 16px 48px; border-radius:0; } .axis { padding:14px 14px 16px; }
  .tablewrap { overflow-x:auto; } table.patterns, table.implications { min-width:640px; } }
"""


def render_html(doc, lab):
    names = names_by_id(doc)
    summary = doc["summary"]
    body = [theme_switch(lab), '<h1>%s</h1>' % esc(doc.get("title") or lab["title"])]
    if doc.get("scope_line"):
        body.append('<p class="scopeline">%s</p>' % esc(doc["scope_line"]))
    if doc.get("timeline"):
        body.append(timeline_html(doc["timeline"], lab))

    body.append('<section class="summary"><h2>%s</h2>' % esc(lab["summary"]))
    body += ["<p>%s</p>" % esc(par) for par in str(summary["text"]).split("\n\n")]
    groups = {p["id"]: g for g in ("strengths", "gaps", "styles") for p in doc.get(g, [])}
    axes = []
    for axis in summary.get("axes", []):
        # each side lists the patterns of its own group, and a way of working is left to its table,
        # because it is neither what the person gets right nor what they miss
        refs = {g: [r for r in axis.get("patterns", []) if groups.get(r) == g] for g in ("strengths", "gaps")}
        links = lambda ids: '<div class="links"><h4>%s</h4>%s</div>' % (
            esc(lab["related"]), pattern_links(ids, names)) if ids else ""
        axes.append(
            '<div class="axis"><h3>%s</h3>%s<div class="sides"><div class="side strong"><b>%s</b>%s%s</div>'
            '<div class="side weak"><b>%s</b>%s%s</div></div></div>' % (
                esc(axis["name"]),
                "<p>%s</p>" % esc(axis["description"]) if axis.get("description") else "",
                esc(lab["strong"]), esc(axis["strong"]), links(refs["strengths"]),
                esc(lab["weak"]), esc(axis["weak"]), links(refs["gaps"])))
    if axes:
        body.append('<div class="axes">%s</div>' % "".join(axes))
    for d in doc.get("diagrams", []):
        body.append(diagram_html(d))
    body.append("</section>")

    for group, style in (("strengths", False), ("gaps", False), ("styles", True)):
        if doc.get(group):
            body.append('<section class="%s"><h2>%s</h2><div class="tablewrap">%s</div></section>' % (
                group, esc(lab[group]), pattern_table(doc[group], lab, style)))

    if doc.get("implications"):
        rows = "".join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td></tr>' % (
            esc(r["area"]), esc(r["meaning"]), pattern_links(r.get("patterns", []), names)) for r in doc["implications"])
        body.append('<section class="implications"><h2>%s</h2><div class="tablewrap"><table class="implications"><thead><tr><th>%s</th><th>%s</th><th>%s</th></tr></thead>'
                    '<tbody>%s</tbody></table></div></section>' % (
                        esc(lab["implications"]), esc(lab["area"]), esc(lab["meaning"]), esc(lab["related"]), rows))

    body.append('<section class="scope"><h2>%s</h2><div class="tablewrap"><table class="scope"><tbody>%s</tbody></table></div></section>' % (
        esc(lab["scope"]), scope_rows(doc["scope"])))

    appendix = []
    if doc.get("dissolved"):
        appendix.append("<h4>%s</h4><ul>%s</ul>" % (esc(lab["dissolved"]), "".join(
            "<li><b>%s</b>: %s</li>" % (esc(d.get("name", "")), esc(d.get("evidence", ""))) for d in doc["dissolved"])))
    if doc.get("events"):
        appendix.append("<h4>%s</h4><ul>%s</ul>" % (esc(lab["events"]), instance_items(doc["events"])))
    if appendix:
        body.append('<section class="appendix"><h2>%s</h2><details><summary>%s</summary>%s</details></section>' % (
            esc(lab["appendix"]), esc(lab["appendix"]), "".join(appendix)))

    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>%s</title><style>%s</style></head><body><main>%s</main></body></html>\n") % (
        esc(doc.get("language", "en")), esc(doc.get("title") or lab["title"]), CSS, "".join(body))


def theme_switch(lab):
    options = "".join('<input type="radio" name="theme" id="theme-%s"%s><label for="theme-%s">%s</label>' % (
        key, " checked" if key == "auto" else "", key, esc(lab["theme_" + key])) for key in ("auto", "light", "dark"))
    return '<div class="theme" role="radiogroup" aria-label="%s">%s</div>' % (esc(lab["theme"]), options)


def timeline_html(timeline, lab):
    day = datetime.date.fromisoformat
    repositories = timeline["repositories"]
    recent = day(timeline["recent_from"]) if timeline.get("recent_from") else None
    prompts = timeline.get("prompts")
    dates = [day(r[k]) for r in repositories for k in ("from", "to", "ai_from") if r.get(k)]
    if prompts:
        dates += [day(prompts["from"]), day(prompts["to"])]
    lo, hi = min(dates), max(dates)
    total = max((hi - lo).days, 1)
    pos = lambda d: (d - lo).days * 100.0 / total

    def segment(kind, start, end):
        return '<span class="tl-seg %s" style="left:%.2f%%;width:%.2f%%" title="%s – %s"></span>' % (
            kind, pos(start), pos(end) - pos(start), start, end)

    def row(name, track):
        return '<div class="tl-row"><div class="tl-name">%s</div><div class="tl-track">%s</div></div>' % (esc(name), track)

    rows, kinds = [], set()
    for r in repositories:
        start, end = day(r["from"]), day(r["to"])
        parts = []
        # the older changes are a thin sample spread across the history, so they are drawn apart from the newest,
        # which were all read, rather than as one bar that claims the whole span was read alike
        if recent and recent > start:
            parts.append(segment("thin", start, min(recent, end)))
            kinds.add("thin")
        if not recent or recent < end:
            parts.append(segment("close", max(start, recent) if recent else start, end))
            kinds.add("close")
        if r.get("ai_from"):
            parts.append('<span class="tl-ai" style="left:%.2f%%" title="%s%s%s"></span>' % (
                pos(day(r["ai_from"])), esc(lab["timeline_ai"]), esc(lab["colon"]), esc(r["ai_from"])))
            kinds.add("ai")
        rows.append(row(r["name"], "".join(parts)))
    if prompts:
        rows.append(row(lab["timeline_prompts"], segment("prompts", day(prompts["from"]), day(prompts["to"]))))
        kinds.add("prompts")

    months = (hi.year - lo.year) * 12 + hi.month - lo.month
    step = 1 if months <= 8 else 3 if months <= 24 else 6 if months <= 60 else 12
    ticks = ['<span class="tl-tick start">%s</span>' % lo, '<span class="tl-tick end">%s</span>' % hi]
    y, m, n = lo.year, lo.month, 0
    while True:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        mark = datetime.date(y, m, 1)
        if mark >= hi:
            break
        # an end date takes about a tenth of a wide track and a quarter of a phone-width one,
        # so a tick that near an end is dropped, or kept only for wide screens
        at = pos(mark)
        if (m - 1) % step == 0 and 12 < at < 88:
            narrow = n % 2 or not 28 < at < 72
            ticks.append('<span class="tl-tick%s" style="left:%.2f%%">%s</span>' % (
                " minor" if narrow else "", at, y if step == 12 else "%d-%02d" % (y, m)))
            n += 1
    rows.append('<div class="tl-row tl-axis"><div class="tl-name"></div><div class="tl-track">%s</div></div>' % "".join(ticks))

    swatches = {"thin": '<i class="tl-seg thin"></i>', "close": '<i class="tl-seg close"></i>',
                "ai": '<i class="tl-ai"></i>', "prompts": '<i class="tl-seg prompts"></i>'}
    legend = "".join('<span class="tl-key">%s%s</span>' % (swatches[k], esc(lab["timeline_" + k]))
                     for k in ("thin", "close", "ai", "prompts") if k in kinds)
    return '<figure class="timeline">%s<figcaption class="tl-legend">%s</figcaption></figure>' % ("".join(rows), legend)


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
    # the caption leads as the diagram's title, since a reader meets the boxes before anything placed under them
    caption = "<figcaption>%s</figcaption>" % esc(d["caption"]) if d.get("caption") else ""
    return '<figure class="flow">%s<div class="lanes n%d">%s</div></figure>' % (caption, len(lanes), "".join(lanes))


def diagram_markdown(d, lab):
    # a terminal cannot draw a box whose border lines up across CJK text, so the summary lists the steps instead
    one = lambda text: md_text(text).replace("\n", " ")
    # the caption leads, as in the HTML, so a reader knows what the lists are before reading them
    lines = ["**%s**" % one(d["caption"]), ""] if d.get("caption") else []
    for lane in d.get("lanes", []):
        if lane.get("title"):
            lines.append("*%s*" % one(lane["title"]))
        steps = lane.get("steps", [])
        for i, step in enumerate(steps):
            detail = one(lab["list_separator"]).join(one(x) for x in step.get("detail", []))
            lines.append("%d. %s%s" % (i + 1, one(step.get("text", "")), " \u2014 %s" % detail if detail else ""))
            # the numbering already gives the order, so an arrow is drawn only where it carries a note
            if i < len(steps) - 1 and step.get("note"):
                lines.append("   \u2193 %s" % one(step["note"]))
        lines.append("")
    return lines


def md_cell(text):
    # a pipe or a line break inside a cell would split the Markdown table,
    # and a backslash would escape the character after it, as in a Windows path
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def md_text(text):
    # the document is plain text, so a backslash in prose is literal and must not escape what follows it
    return str(text).replace("\\", "\\\\")


def render_markdown(doc, lab, out_path):
    summary = doc["summary"]
    lines = []
    # the scope leads, as the timeline does in the HTML, because the conclusion rests on that material and no more
    if doc.get("scope_line"):
        lines += ["## %s" % md_text(lab["scope"]), "", md_text(doc["scope_line"]), ""]
    lines += ["## %s" % md_text(lab["summary"]), "", md_text(summary["text"]), ""]
    # a terminal table misaligns once a long cell wraps, so the summary keeps long text out of tables
    # and leaves descriptions and trade-offs to the HTML
    for a in summary.get("axes", []):
        # a line break would end the bold span or the list item early, so each axis value stays on one line
        one = lambda text: md_text(text).replace("\n", " ")
        lines += ["**%s**" % one(a["name"]),
                  "- %s — %s" % (one(lab["strong"]), one(a["strong"])),
                  "- %s — %s" % (one(lab["weak"]), one(a["weak"])), ""]
    for d in doc.get("diagrams", []):
        lines += diagram_markdown(d, lab)
    for group in ("strengths", "gaps", "styles"):
        if not doc.get(group):
            continue
        lines += ["## %s" % md_text(lab[group]), "",
                  "| %s | %s | %s |" % (md_cell(lab["pattern"]), md_cell(lab["source"]), md_cell(lab["confidence"])),
                  "|---|---|---|"]
        lines += ["| %s | %s | %s |" % (md_cell(p["name"]), md_cell(sources_text(p["sources"], lab)),
                                       md_cell(confidence_text(p["confidence"], lab))) for p in doc[group]]
        lines.append("")
        # a terminal has no tooltip, so each short conditional label is spelled out under its table
        conditions = ["- %s — %s" % (md_text(p["name"]).replace("\n", " "), md_text(constraint_text(p["confidence"], lab)).replace("\n", " "))
                      for p in doc[group] if p["confidence"]["level"] == "depends"]
        if conditions:
            lines += conditions + [""]
    if doc.get("implications"):
        # the meaning is the implication itself, and a list item wraps where a table cell would break the table,
        # so the summary gives each meaning and leaves the related patterns to the HTML
        lines += ["## %s" % md_text(lab["implications"]), ""]
        lines += ["- **%s** — %s" % (md_text(r["area"]).replace("\n", " "), md_text(r["meaning"]).replace("\n", " "))
                  for r in doc["implications"]]
        lines.append("")
    # a code span keeps a Windows path whole, where plain Markdown reads "\." as an escaped dot
    lines.append("%s%s`%s`" % (md_text(lab["report_file"]), md_text(lab["colon"]), out_path))
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
