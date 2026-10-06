"""Render a remind-me report as a local HTML page and print its summary for the session.

The model writes only what needs judgement: each session's topics, what got done, and what is still open.
Everything countable comes from the digest the collector wrote, so no figure on the page is the model's:
the times, the token usage, the pull request states, the folders, and the commands that reopen a session.

Usage:
    python render.py <report.json> --digest <digest.json> --out <report.html> [--open]

Exit status 2 lists what in the report does not match the schema in report-schema.md, and nothing is written.
"""

import argparse
import html
import json
import os
import pathlib
import sys
import urllib.parse
import webbrowser

EXIT_OK = 0
EXIT_SCHEMA = 2

LABELS = ("sessions", "repositories", "prompts", "pull_requests", "tokens", "tokens_cached", "output",
          "open", "done", "topics", "actions", "running", "all_open", "timeline_hint",
          "resume", "copy_resume", "new_session", "copy_terminal", "copied", "theme", "theme_light", "theme_dark",
          "uncommitted", "unpushed", "nothing_open", "as_of", "language_hint", "resume_last", "here", "active", "session_list", "copy_path", "show_all",
          "decision", "action", "question")
KINDS = ("decision", "action", "question")
# a kind with more open items than this shows its first ones and folds the rest behind a button
OVERVIEW_LIMIT = 10

e = html.escape


def problems_in(report, digest):
    """Everything in the report that the page cannot be built from, as readable lines."""
    found = []
    for key in ("day", "language", "headline", "labels", "sessions"):
        if key not in report:
            found.append("missing top-level field: %s" % key)
    if found:
        return found
    if report["day"] != digest.get("day"):
        found.append("report day %s is not the digest's day %s" % (report["day"], digest.get("day")))
    missing = [label for label in LABELS if not str(report["labels"].get(label, "")).strip()]
    if missing:
        found.append("labels missing: %s" % ", ".join(missing))
    known = {session["id"] for group in digest.get("repositories", []) for session in group["sessions"]}
    for session_id, body in report["sessions"].items():
        if session_id not in known:
            found.append("session %s is not in the digest" % session_id)
            continue
        if not body.get("topics"):
            found.append("session %s has no topics" % session_id)
        for item in body.get("open", []):
            if item.get("kind") not in KINDS:
                found.append("session %s: open item kind must be one of %s" % (session_id, ", ".join(KINDS)))
            if not str(item.get("text", "")).strip():
                found.append("session %s: an open item has no text" % session_id)
    for session_id in sorted(known - set(report["sessions"])):
        found.append("session %s from the digest has no entry in the report" % session_id)
    return found


def tokens(usage_buckets):
    """Input the plan pays for in full (uncached and cache writes), cache reads apart, and output."""
    totals = {"charged": 0, "cached": 0, "output": 0}
    for bucket in usage_buckets:
        totals["charged"] += bucket.get("input_tokens", 0) + bucket.get("cache_creation_input_tokens", 0)
        totals["cached"] += bucket.get("cache_read_input_tokens", 0)
        totals["output"] += bucket.get("output_tokens", 0)
    return totals


def buckets(sessions):
    return [bucket for session in sessions for bucket in session["usage"].values()]


def compact(number):
    units = ("", "k", "M", "B")
    value, unit = float(number), 0
    # move up a unit whenever rounding to one decimal would reach 1000, so 999,999 reads 1M rather than 1000k
    while unit < len(units) - 1 and round(value, 1) >= 1000:
        value /= 1000
        unit += 1
    if unit == 0:
        return str(number)
    return ("%.1f" % value).rstrip("0").rstrip(".") + units[unit]


def windows():
    return sys.platform.startswith("win")


def resume_command(cwd, session_id):
    """Resume from the session's own folder: Claude Code is folder-scoped, and a resume from elsewhere runs in the wrong one."""
    if windows():
        return "Set-Location -LiteralPath '%s'; claude --resume %s" % (cwd.replace("'", "''"), session_id)
    return "cd '%s' && claude --resume %s" % (cwd.replace("'", "'\\''"), session_id)


def terminal_command(cwd):
    """The fallback when opening Claude Code is not allowed: a plain terminal in the right folder."""
    if windows():
        return 'wt.exe -d "%s"' % cwd
    if sys.platform == "darwin":
        return "open -a Terminal '%s'" % cwd.replace("'", "'\\''")
    return "cd '%s'" % cwd.replace("'", "'\\''")


def deep_link(cwd, prompt=None):
    """A claude-cli:// link opens a new terminal session in the folder; a prompt is only filled in, never sent."""
    query = {"cwd": cwd}
    if prompt:
        query["q"] = prompt
    return "claude-cli://open?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


def all_sessions(digest):
    return [session for group in digest["repositories"] for session in group["sessions"]]


def figures(digest):
    sessions = all_sessions(digest)
    # only the pull requests created that day count: a session also names old ones it labelled or compared against
    new = {key for session in sessions for key in session.get("pull_requests_new", [])}
    states = digest.get("pull_requests", {})
    return {
        "sessions": len(sessions),
        "repositories": len(digest["repositories"]),
        "prompts": sum(1 for session in sessions for prompt in session["prompts"] if prompt["kind"] in ("prompt", "command")),
        "pull_requests": len(new),
        "completed": sum(1 for key in new if states.get(key, {}).get("status") in ("completed", "merged")),
        "tokens": tokens(buckets(sessions)),
    }


def mix(sessions):
    total = {"read": 0, "write": 0, "fresh": 0, "out": 0}
    for bucket in buckets(sessions):
        total["read"] += bucket.get("cache_read_input_tokens", 0)
        total["write"] += bucket.get("cache_creation_input_tokens", 0)
        total["fresh"] += bucket.get("input_tokens", 0)
        total["out"] += bucket.get("output_tokens", 0)
    return total


def name_of(group):
    return os.path.basename(group["path"].rstrip("\\/")) or group["path"]


def body_of(report, session):
    return report["sessions"][session["id"]]


def open_count(group, report):
    return sum(len(body_of(report, session).get("open", [])) for session in group["sessions"])


def ordered(digest, report):
    """Repositories with the most left open come first, because that is what the page is read for."""
    return sorted(digest["repositories"], key=lambda group: (-open_count(group, report), name_of(group).lower()))


def readable(stamp):
    return stamp[:16].replace("T", " ") if stamp else ""


def minutes(clock):
    hours, mins = clock.split(":")
    return int(hours) * 60 + int(mins)


def stretches(session):
    return session.get("active") or [[session["first"], session["last"]]]


def rows_for(group):
    """Sessions that ran at the same time get a row each, so no bar hides another."""
    rows = []
    for session in group["sessions"]:
        spans = [(minutes(session["first"]), minutes(session["last"]))]
        for row in rows:
            if all(end < start2 or end2 < start for start, end in spans for start2, end2 in row["spans"]):
                row["spans"].extend(spans)
                row["sessions"].append(session)
                break
        else:
            rows.append({"spans": list(spans), "sessions": [session]})
    return rows


def counts(items, labels):
    found = [(kind, sum(1 for item in items if item["kind"] == kind)) for kind in KINDS]
    return '<span class="counts">%s</span>' % "".join(
        '<span class="count %s">%s %d</span>' % (kind, e(labels[kind]), number) for kind, number in found if number)


def open_list(items, labels):
    if not items:
        return '<p class="none">%s</p>' % e(labels["nothing_open"])
    rows = []
    for item, where in items:
        since = ' <span class="since">%s</span>' % e(item["since"]) if item.get("since") else ""
        place = ' <span class="where">%s</span>' % e(where) if where else ""
        rows.append('<li><span class="kind %s">%s</span><span class="what">%s%s%s</span></li>'
                    % (item["kind"], e(labels[item["kind"]]), e(item["text"]), since, place))
    return '<ol class="open">%s</ol>' % "".join(rows)


def button_link(href, text, hint):
    return '<a class="btn" href="%s" title="%s">%s</a>' % (e(href, quote=True), e(hint, quote=True), e(text))


def copy_button(command, text, labels):
    return ('<button type="button" class="btn ghost copy" data-copy="%s" data-done="%s" title="%s">%s</button>'
            % (e(command, quote=True), e(labels["copied"], quote=True), e(command, quote=True), e(text)))


def section(title, content):
    return '<section class="part"><h3>%s</h3>%s</section>' % (e(title), content)


def session_panel(group, session, report, labels):
    body = body_of(report, session)
    usage = tokens(session["usage"].values())
    times = ", ".join("%s&ndash;%s" % (e(a), e(b)) for a, b in stretches(session))
    head = ('<header class="panel-head"><h2>%s <span class="span">%s&ndash;%s</span></h2>%s%s<p class="meta"><span>%s %s</span><span>%s %s</span><span>%s %s</span><span>%s</span></p></header>'
            % (e(name_of(group)), e(session["first"]), e(session["last"]), counts(body.get("open", []), labels),
               '<span class="chip live">%s</span>' % e(labels["running"]) if session.get("running") else "",
               e(labels["active"]), times, compact(usage["charged"]), e(labels["tokens"]), compact(usage["output"]), e(labels["output"]), e(session["id"])))
    parts = [head,
             section(labels["topics"], '<ul class="topics">%s</ul>' % "".join("<li>%s</li>" % e(topic) for topic in body["topics"])),
             section(labels["open"], open_list([(item, None) for item in body.get("open", [])], labels))]
    if body.get("done"):
        parts.append(section(labels["done"], '<ul class="done">%s</ul>' % "".join("<li>%s</li>" % e(line) for line in body["done"])))
    if session.get("cwd") and not session.get("running"):
        resume = resume_command(session["cwd"], session["id"])
        actions = (button_link(deep_link(session["cwd"], "/resume " + session["id"]), labels["resume"], session["cwd"])
                   + copy_button(resume, labels["copy_resume"], labels)
                   + button_link(deep_link(session["cwd"]), labels["new_session"], session["cwd"])
                   + copy_button(terminal_command(session["cwd"]), labels["copy_terminal"], labels)
                   + copy_button(session["cwd"], labels["copy_path"], labels))
        parts.append(section(labels["actions"], '<div class="buttons">%s</div>' % actions))
    return "".join(parts)


def repository_panel(group, report, labels):
    items = [(item, session) for session in group["sessions"] for item in body_of(report, session).get("open", [])]
    live = group.get("live") or {}
    chips = []
    if live.get("current_branch"):
        chips.append('<span class="chip">%s</span>' % e(live["current_branch"]))
    if live.get("uncommitted"):
        chips.append('<span class="chip warn">%s %d</span>' % (e(labels["uncommitted"]), live["uncommitted"]))
    unpushed = sum(branch.get("unpushed") or 0 for branch in live.get("branches", []))
    if unpushed:
        chips.append('<span class="chip warn">%s %d</span>' % (e(labels["unpushed"]), unpushed))
    sessions = "".join(
        '<li><button type="button" class="linkish" data-show="s-%s"><b>%s&ndash;%s</b> %s</button></li>'
        % (e(session["id"], quote=True), e(session["first"]), e(session["last"]), e(" / ".join(body_of(report, session)["topics"])))
        for session in group["sessions"])
    open_items = "".join(
        '<li><span class="kind %s">%s</span><button type="button" class="linkish what" data-show="s-%s">%s <span class="where">%s&ndash;%s</span></button></li>'
        % (item["kind"], e(labels[item["kind"]]), e(session["id"], quote=True), e(item["text"]), e(session["first"]), e(session["last"]))
        for item, session in items)
    resumable = [session for session in group["sessions"] if session.get("cwd") and not session.get("running")]
    actions = ""
    if resumable:
        # the latest session that has stopped, since a running one is already open somewhere
        last = max(resumable, key=lambda session: session["last"])
        actions += (button_link(deep_link(last["cwd"], "/resume " + last["id"]), labels["resume_last"], "%s %s-%s" % (last["cwd"], last["first"], last["last"]))
                    + copy_button(resume_command(last["cwd"], last["id"]), labels["copy_resume"], labels))
    actions += (button_link(deep_link(group["path"]), labels["new_session"], group["path"])
                + copy_button(terminal_command(group["path"]), labels["copy_terminal"], labels)
                + copy_button(group["path"], labels["copy_path"], labels))
    if group.get("current"):
        chips.append('<span class="chip here">%s</span>' % e(labels["here"]))
    return ('<header class="panel-head"><h2>%s</h2>%s%s<p class="meta path">%s</p></header>%s%s%s'
            % (e(name_of(group)), counts([item for item, _ in items], labels), "".join(chips), e(group["path"]),
               section(labels["open"], '<ol class="open">%s</ol>' % open_items if items else '<p class="none">%s</p>' % e(labels["nothing_open"])),
               section(labels["session_list"], '<ul class="sessions">%s</ul>' % sessions),
               section(labels["actions"], '<div class="buttons">%s</div>' % actions) if os.path.isabs(group["path"]) else ""))


def overview_panel(digest, report, labels):
    """What the page opens on: everything left open, across repositories, by the kind of thing it asks of the person."""
    blocks = []
    for kind in KINDS:
        rows = []
        for group in ordered(digest, report):
            for session in group["sessions"]:
                for item in body_of(report, session).get("open", []):
                    if item["kind"] == kind:
                        rows.append('<li><button type="button" class="linkish what" data-show="s-%s">%s <span class="where">%s %s&ndash;%s</span></button></li>'
                                    % (e(session["id"], quote=True), e(item["text"]), e(name_of(group)), e(session["first"]), e(session["last"])))
        if rows:
            more = ""
            if len(rows) > OVERVIEW_LIMIT:
                rows = rows[:OVERVIEW_LIMIT] + [row.replace("<li>", '<li class="more" hidden>', 1) for row in rows[OVERVIEW_LIMIT:]]
                more = '<button type="button" class="btn ghost show-all">%s (%d)</button>' % (e(labels["show_all"]), len(rows))
            blocks.append('<section class="part kind-%s"><h3><span class="count %s">%s</span> %d</h3><ul class="plain">%s</ul>%s</section>'
                          % (kind, kind, e(labels[kind]), len(rows), "".join(rows), more))
    content = "".join(blocks) or '<p class="none">%s</p>' % e(labels["nothing_open"])
    return '<header class="panel-head"><h2>%s</h2></header>%s' % (e(labels["all_open"]), content)


def timeline(digest, report, labels, figures_html="", mix_html=""):
    sessions = all_sessions(digest)
    starts = [minutes(session["first"]) for session in sessions]
    ends = [minutes(session["last"]) for session in sessions]
    low, high = (min(starts) // 60) * 60, (max(ends) // 60 + 1) * 60
    span = max(high - low, 60)

    def at(clock):
        return 100.0 * (minutes(clock) - low) / span

    hours = range(low // 60, high // 60 + 1)
    ticks = "".join('<span class="tick" style="left:%.2f%%">%02d:00</span>' % (100.0 * (hour * 60 - low) / span, hour) for hour in hours)
    lines = "".join('<i class="gridline" style="left:%.2f%%"></i>' % (100.0 * (hour * 60 - low) / span) for hour in hours)
    rows = []
    for index, group in enumerate(ordered(digest, report)):
        lanes = rows_for(group)
        # every bar keeps its full height, and a repository with sessions side by side gets a taller row
        height = 20
        # wider than the selection outline and its offset, so a selected bar never seems to touch its neighbour
        gap = 8
        bars = []
        for lane, row in enumerate(lanes):
            for session in row["sessions"]:
                items = body_of(report, session).get("open", [])
                label = "%s %s&ndash;%s" % (e(name_of(group)), e(session["first"]), e(session["last"]))
                # the repository's labels count what is open, so a bar only shows whether its session left anything
                bars.append('<button type="button" class="bar%s" data-show="s-%s" aria-label="%s" style="left:%.2f%%;width:%.2f%%;top:%dpx;height:%dpx"></button>'
                            % (" has" if items else "", e(session["id"], quote=True), label, at(session["first"]),
                               max(at(session["last"]) - at(session["first"]), 0.8), 3 + lane * (height + gap), height))
        track = '<div class="track" style="height:%dpx">%s%s</div>' % (6 + len(lanes) * height + (len(lanes) - 1) * gap, lines, "".join(bars))
        items = [item for session in group["sessions"] for item in body_of(report, session).get("open", [])]
        here = ' <span class="chip here">%s</span>' % e(labels["here"]) if group.get("current") else ""
        rows.append('<button type="button" class="tname" data-show="r-%d"><span class="line"><span class="nm">%s</span>%s</span>%s</button>%s'
                    % (index, e(name_of(group)), here, counts(items, labels), track))
    first = min(session["first"] for session in sessions)
    last = max(session["last"] for session in sessions)
    # the day leads the section it describes, with when the live state was read beside it
    head = ('<header class="timeline-head"><h2>%s <span class="span">%s&ndash;%s</span></h2><span class="asof">%s %s</span></header>'
            % (e(digest["day"]), e(first), e(last), e(labels["as_of"]), e(readable(digest.get("generated")))))
    # the figures describe the day, so they sit under its title, and the token bar separates them from the bars
    return ('<section class="timeline" aria-label="timeline">%s%s%s<p class="hint">%s</p><div class="scroll"><div class="grid">'
            '<span></span><div class="axis">%s</div>%s</div></div></section>'
            % (head, figures_html, mix_html, e(labels["timeline_hint"]), ticks, "".join(rows)))


STYLE = """
:root { --bg:#f5f6f8; --card:#ffffff; --ink:#1c2330; --soft:#566173; --faint:#68717f; --line:#e1e5eb; --accent:#2f6fb2;
  --warn:#a3324f; --warn-bg:#fbe6ea; --live:#1f7a4d; --live-bg:#e2f3e9; --bar:#c3cad5; --openbar:#3b4a63;
  --decision:#7a3fb0; --decision-bg:#f0e7fa; --action:#0f6e66; --action-bg:#e0f2ef; --question:#9a5806; --question-bg:#fcefdc;
  --mix-read:#a9c0dc; --mix-write:#5b8fd0; --mix-fresh:#24599a; --mix-out:#d08a2f;
  --sans:-apple-system,"Segoe UI","PingFang TC","Microsoft JhengHei",system-ui,sans-serif; --mono:ui-monospace,"Cascadia Code",Consolas,monospace }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#13161b; --card:#1b1f26; --ink:#e6e9ef; --soft:#a4adbb; --faint:#8c95a5;
  --line:#2b313b; --accent:#7fb0e6; --warn:#f08aa2; --warn-bg:#3a1c24; --live:#6fd3a0; --live-bg:#163225; --bar:#3a424f; --openbar:#c3cfe2;
  --decision:#c9a2ef; --decision-bg:#2e2140; --action:#6fd0c4; --action-bg:#173230; --question:#f0b35c; --question-bg:#3a2b16;
  --mix-read:#3d5a80; --mix-write:#5b8fd0; --mix-fresh:#9cc3f0; --mix-out:#e0a050; color-scheme:dark } }
:root[data-theme="dark"] { --bg:#13161b; --card:#1b1f26; --ink:#e6e9ef; --soft:#a4adbb; --faint:#8c95a5;
  --line:#2b313b; --accent:#7fb0e6; --warn:#f08aa2; --warn-bg:#3a1c24; --live:#6fd3a0; --live-bg:#163225; --bar:#3a424f; --openbar:#c3cfe2;
  --decision:#c9a2ef; --decision-bg:#2e2140; --action:#6fd0c4; --action-bg:#173230; --question:#f0b35c; --question-bg:#3a2b16;
  --mix-read:#3d5a80; --mix-write:#5b8fd0; --mix-fresh:#9cc3f0; --mix-out:#e0a050; color-scheme:dark }
:root[data-theme="light"] { color-scheme:light }
[hidden] { display:none !important }
body { margin:0; background:var(--bg); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.55 }
.wrap { max-width:1160px; margin:0 auto; padding-inline:16px; padding-block:28px 40px; display:grid; gap:18px }
.headline { display:flex; gap:12px 20px; align-items:flex-start; justify-content:space-between }
.headline .theme { flex:none; white-space:nowrap }
h1 { font-size:26px; line-height:1.3; margin:0; text-wrap:balance }
.figures { display:flex; flex-wrap:wrap; gap:4px 22px; margin:2px 0 0; color:var(--soft); font-size:13px; font-variant-numeric:tabular-nums }
.figures b { color:var(--ink); font-weight:600 }
.mix { display:grid; gap:6px; margin:12px 0 14px }
.mixbar { display:flex; height:10px; border-radius:6px; overflow:hidden; background:var(--line) }
.mixbar span { height:100% }
.legend { display:flex; flex-wrap:wrap; gap:4px 16px; font-size:12px; color:var(--soft) }
.legend i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; vertical-align:-1px }
.timeline, .panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; min-width:0 }
.timeline-head { display:flex; flex-wrap:wrap; gap:4px 16px; align-items:baseline; justify-content:space-between; margin-bottom:4px }
.timeline-head h2 { margin:0; font-size:18px; font-variant-numeric:tabular-nums } .timeline-head .span { font-weight:500; color:var(--soft); font-size:15px }
.asof { color:var(--faint); font-size:12px; font-variant-numeric:tabular-nums }
.hint { margin:0 0 8px; font-size:13px; color:var(--soft) }
.scroll { overflow-x:auto; padding-right:24px }
.grid { display:grid; grid-template-columns:max-content minmax(620px, 1fr); align-items:center }
.grid > .tname, .grid > .track { border-top:1px solid var(--line) }
.axis { position:relative; height:18px }
.tick { position:absolute; transform:translateX(-50%); font-size:11px; color:var(--faint); font-variant-numeric:tabular-nums }
.tname { font:inherit; text-align:left; background:none; border:0; color:var(--ink); cursor:pointer; padding:4px 8px 4px 0; display:flex; flex-direction:column; gap:3px; align-items:flex-start; border-radius:0; align-self:stretch; justify-content:flex-start; padding:4px 16px 14px 0 }
.tname .line { display:flex; gap:6px; align-items:center; white-space:nowrap }
.tname .nm { font-weight:600; font-size:14px; text-decoration:underline; text-decoration-color:var(--line); text-underline-offset:3px }
.tname:hover .nm, .tname.on .nm { text-decoration-color:var(--accent); color:var(--accent) }
.track { position:relative; align-self:stretch; min-height:26px }
.gridline { position:absolute; top:0; bottom:0; width:1px; background:var(--line) }
.bar { position:absolute; padding:0; border:0; background:var(--bar); cursor:pointer; border-radius:5px; text-align:left }
.bar.has { background:var(--openbar) }
.bar .n:empty { display:none }
.bar:hover, .bar:focus-visible { filter:brightness(1.12) }
.bar:hover, .bar:focus-visible, .bar.on { outline:2px solid var(--accent); outline-offset:2px }
.bar.has { animation:invite 1.6s ease-in-out 2 }
@keyframes invite { 50% { transform:translateY(-2px) } }
@media (prefers-reduced-motion: reduce) { .bar.has { animation:none } }
.panel { display:grid; gap:14px }
.panel-head { display:flex; flex-wrap:wrap; gap:6px 10px; align-items:center }
.panel-head h2 { margin:0; font-size:18px } .panel-head .span { font-weight:500; color:var(--soft); font-size:15px }
.meta { flex-basis:100%; margin:0; color:var(--faint); font-size:12px; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; display:flex; flex-wrap:wrap; gap:2px 18px }
.part { border-top:1px solid var(--line); padding-top:12px; display:grid; gap:8px }
.part h3 { margin:0; font-size:13px; font-weight:600; color:var(--soft); letter-spacing:.03em; display:flex; gap:8px; align-items:center }
.topics, .done { margin:0; padding-left:18px } .done { color:var(--soft) }
.sessions { margin:0; padding:0; list-style:none; display:grid; gap:6px }
.open { margin:0; padding:0; list-style:none; display:grid; gap:6px }
.plain { margin:0; padding-left:22px; display:grid; gap:6px } .plain li::marker { color:var(--faint); font-variant-numeric:tabular-nums }
.plain .linkish { display:block; width:100% }
.open li { display:grid; grid-template-columns:auto 1fr; gap:8px; align-items:start }
.kind, .count { font-size:11px; font-weight:600; padding:2px 7px; border-radius:6px; white-space:nowrap }
.kind.decision, .count.decision { background:var(--decision-bg); color:var(--decision) }
.kind.action, .count.action { background:var(--action-bg); color:var(--action) }
.kind.question, .count.question { background:var(--question-bg); color:var(--question) }
.counts { display:inline-flex; gap:4px; flex-wrap:wrap }
.what { min-width:0 } .since, .where { color:var(--faint); font-size:12px; font-variant-numeric:tabular-nums }
.none { margin:0; color:var(--faint) }
.chip { font-size:12px; padding:1px 8px; border-radius:999px; border:1px solid var(--line); color:var(--soft) }
.chip.warn { background:var(--warn-bg); color:var(--warn); border-color:transparent }
.chip.live { background:var(--live-bg); color:var(--live); border-color:transparent }
.chip.here { border-color:var(--accent); color:var(--accent) }
.buttons { display:flex; flex-wrap:wrap; gap:8px }
.btn { font:inherit; font-size:13px; padding:6px 12px; border-radius:7px; border:1px solid var(--accent); background:var(--accent); color:var(--card); text-decoration:none; cursor:pointer }
.btn.ghost { background:none; color:var(--accent) }
.btn:hover { filter:brightness(1.08) } .btn:focus-visible, .linkish:focus-visible, .tname:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
.linkish { font:inherit; color:inherit; background:none; border:0; padding:0; text-align:left; cursor:pointer }
.linkish:hover { color:var(--accent) }
.back { justify-self:start; font-size:13px }
.show-all { justify-self:start; font-size:13px }
@media (max-width: 640px) { h1 { font-size:22px } }
"""

SCRIPT = """
(function () {
  // two states only: the page opens in the system's theme, and the button names the one a click switches to
  var root = document.documentElement, names = %(themes)s, button = document.getElementById('theme');
  function current() { return root.getAttribute('data-theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); }
  function label() { button.textContent = names[current() === 'dark' ? 'light' : 'dark']; }
  try { var saved = localStorage.getItem('remind-me-theme'); if (saved === 'light' || saved === 'dark') { root.setAttribute('data-theme', saved); } } catch (ignored) { }
  label();
  button.addEventListener('click', function () {
    var next = current() === 'dark' ? 'light' : 'dark'; root.setAttribute('data-theme', next); label();
    try { localStorage.setItem('remind-me-theme', next); } catch (ignored) { } });
  function show(id) {
    document.querySelectorAll('.panel').forEach(function (p) { p.hidden = p.id !== id; });
    document.querySelectorAll('[data-show]').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-show') === id); });
    document.getElementById('back').hidden = id === 'overview';
  }
  document.addEventListener('click', function (event) {
    var all = event.target.closest('button.show-all');
    if (all) { all.parentNode.querySelectorAll('li.more').forEach(function (li) { li.hidden = false; }); all.hidden = true; return; }
    var target = event.target.closest('[data-show]');
    if (target) { show(target.getAttribute('data-show')); return; }
    var button = event.target.closest('button.copy'); if (!button) { return; }
    var text = button.getAttribute('data-copy'), label = button.textContent;
    function done() { button.textContent = button.getAttribute('data-done'); setTimeout(function () { button.textContent = label; }, 1500); }
    function fallback() { var area = document.createElement('textarea'); area.value = text; document.body.appendChild(area); area.select();
      try { if (document.execCommand('copy')) { done(); } } catch (ignored) { } document.body.removeChild(area); }
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done, fallback); } else { fallback(); }
  });
})();
"""


def page(report, digest):
    labels = report["labels"]
    numbers = figures(digest)
    usage = numbers["tokens"]
    stats = [
        (numbers["sessions"], labels["sessions"]),
        (numbers["repositories"], labels["repositories"]),
        (numbers["prompts"], labels["prompts"]),
        ("%d / %d" % (numbers["completed"], numbers["pull_requests"]), labels["pull_requests"]),
        (compact(usage["charged"]), labels["tokens"]),
        (compact(usage["cached"]), labels["tokens_cached"]),
        (compact(usage["output"]), labels["output"]),
    ]
    figures_html = '<p class="figures">%s</p>' % "".join("<span><b>%s</b> %s</span>" % (e(str(value)), e(label)) for value, label in stats)
    parts = mix(all_sessions(digest))
    whole = sum(parts.values()) or 1
    mix_names = (("read", "cache read"), ("write", "cache write"), ("fresh", "uncached input"), ("out", "output"))
    mix_html = ('<div class="mix"><div class="mixbar" role="img" aria-label="token mix">%s</div><div class="legend">%s</div></div>'
                % ("".join('<span style="width:%.3f%%;background:var(--mix-%s)"></span>' % (100.0 * parts[key] / whole, key) for key, _ in mix_names),
                   "".join('<span><i style="background:var(--mix-%s)"></i>%s %s (%.1f%%)</span>' % (key, name, compact(parts[key]), 100.0 * parts[key] / whole)
                           for key, name in mix_names)))
    panels = ['<section class="panel" id="overview">%s</section>' % overview_panel(digest, report, labels)]
    for index, group in enumerate(ordered(digest, report)):
        panels.append('<section class="panel" id="r-%d" hidden>%s</section>' % (index, repository_panel(group, report, labels)))
        for session in group["sessions"]:
            panels.append('<section class="panel" id="s-%s" hidden>%s</section>'
                          % (e(session["id"], quote=True), session_panel(group, session, report, labels)))
    themes = json.dumps({"light": labels["theme_light"], "dark": labels["theme_dark"]}, ensure_ascii=False)
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>remind-me %s</title><style>%s</style></head><body><main class="wrap">'
            '<div class="headline"><h1>%s</h1><button type="button" class="btn ghost theme" id="theme" aria-label="%s">%s</button></div>'
            '%s'
            '<button type="button" class="btn ghost back" id="back" data-show="overview" hidden>%s</button>%s'
            '</main><script>%s</script></body></html>'
            % (e(report["language"], quote=True), e(report["day"]), STYLE, e(report["headline"]), e(labels["theme"], quote=True), e(labels["theme_dark"]),
               timeline(digest, report, labels, figures_html, mix_html), e(labels["all_open"]), "".join(panels),
               SCRIPT % {"themes": themes}))


def summary(report, digest, out):
    """The Markdown the session shows: no tables, because the terminal prints a <br> in a cell literally."""
    labels = report["labels"]
    numbers = figures(digest)
    lines = ["**%s**" % report["headline"], "",
             "%s · %d %s · %d %s · %s %s" % (report["day"], numbers["sessions"], labels["sessions"], numbers["repositories"],
                                           labels["repositories"], compact(numbers["tokens"]["charged"]), labels["tokens"])]
    for group in ordered(digest, report):
        lines += ["", "### %s" % name_of(group)]
        for session in group["sessions"]:
            body = body_of(report, session)
            running = " (%s)" % labels["running"] if session.get("running") else ""
            lines.append("- **%s–%s**%s %s" % (session["first"], session["last"], running, " / ".join(body["topics"])))
            for item in body.get("open", []):
                lines.append("  - %s: %s" % (labels[item["kind"]], item["text"]))
    lines += ["", pathlib.Path(out).resolve().as_uri(), "", "_%s_" % labels["language_hint"]]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report")
    parser.add_argument("--digest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--open", action="store_true", help="open the page in the default browser once written")
    args = parser.parse_args(argv)
    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)
    with open(args.digest, encoding="utf-8") as handle:
        digest = json.load(handle)
    problems = problems_in(report, digest)
    sys.stdout.reconfigure(encoding="utf-8")
    if problems:
        print("the report does not match the schema, so nothing was written:")
        for problem in problems:
            print("- " + problem)
        return EXIT_SCHEMA
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(page(report, digest))
    print(summary(report, digest, args.out))
    if args.open:
        webbrowser.open(pathlib.Path(args.out).resolve().as_uri())
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
