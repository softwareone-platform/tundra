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
import re
import sys
import urllib.parse
import webbrowser

EXIT_OK = 0
EXIT_SCHEMA = 2

LABELS = ("sessions", "repositories", "prompts", "pull_requests", "tokens", "tokens_cached", "output",
          "done", "running", "all_open", "resume", "copy_resume", "new_session", "copy_terminal", "copied", "theme", "theme_light", "theme_dark",
          "uncommitted", "unpushed", "nothing_open", "nothing_happened", "as_of", "language_hint", "resume_last", "here", "state_unread", "copy_path",
          "show_all", "repository_list", "cache_read", "cache_write", "uncached_input", "decision", "action", "question",
          "level_day", "level_repository", "level_session", "session_position", "still_open", "what_happened",
          "duration", "duration_minutes", "timeline", "session_count", "running_count", "cannot_resume", "token_mix")
# the labels that carry numbers or names, and the placeholders each must keep so the renderer can fill them in
PLACEHOLDERS = {"session_position": ("n", "total"), "duration": ("hours", "minutes"), "duration_minutes": ("minutes",),
                "session_count": ("n", "repository"), "running_count": ("n",)}
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
    for label, names in PLACEHOLDERS.items():
        lost = [name for name in names if "{%s}" % name not in str(report["labels"].get(label, ""))]
        if label not in missing and lost:
            found.append("label %s must keep %s" % (label, ", ".join("{%s}" % name for name in lost)))
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


def fill(text, **values):
    """A label with placeholders, filled in by name, because word order differs between languages."""
    for key, value in values.items():
        text = text.replace("{%s}" % key, str(value))
    return text


def length(session, labels):
    total = max(minutes(session["last"]) - minutes(session["first"]), 0)
    if total >= 60:
        return fill(labels["duration"], hours=total // 60, minutes=total % 60)
    return fill(labels["duration_minutes"], minutes=total)


def slugs(groups):
    """An anchor per repository from its name, so a link stays the same however the list is sorted."""
    taken, found = set(), {}
    for group in sorted(groups, key=lambda group: group["path"]):
        base = re.sub(r"[^\w.-]+", "-", name_of(group).lower()).strip("-") or "repository"
        slug, number = base, 2
        while slug in taken:
            slug, number = "%s-%d" % (base, number), number + 1
        taken.add(slug)
        found[group["path"]] = slug
    return found


def by_time(group):
    return sorted(group["sessions"], key=lambda session: session["first"])


def icon(name):
    return '<svg class="i" viewBox="0 0 24 24" aria-hidden="true">%s</svg>' % ICONS[name]


def tile(group):
    """The repository's mark; the model chooses an emoji per repository later, and its initial stands in until then."""
    return '<span class="tile" aria-hidden="true">%s</span>' % e(name_of(group)[:1].upper())


def session_name(report, session):
    return '<span class="at">%s</span>%s' % (e(session["first"]), e(body_of(report, session)["topics"][0]))


def code_chips(text):
    """Ticket keys, pull requests, branches and commands read as code, so they stand out in a line of prose."""
    escaped = e(text)
    return re.sub(r"(?<![\w/-])(![0-9]+|#[0-9]+|[A-Z][A-Z0-9]+-[0-9]+(?:-[0-9]+)*|(?:release|feature|bugfix|hotfix)/[\w.-]+|[\w-]+\.py(?: --[\w-]+)*)(?![\w-])",
                  r"<code>\1</code>", escaped)


def item_body(item, where=""):
    detail = '<div class="d">%s</div>' % code_chips(item["detail"]) if str(item.get("detail", "")).strip() else ""
    return '<div class="t">%s</div>%s%s' % (code_chips(item["text"]), detail, where)


def limited(rows, labels):
    """A kind with more items than the limit shows its first ones, and a button opens the rest in place."""
    if len(rows) <= OVERVIEW_LIMIT:
        return "".join(rows), ""
    shown = rows[:OVERVIEW_LIMIT] + [row.replace("<li", '<li class="more" hidden', 1) for row in rows[OVERVIEW_LIMIT:]]
    return "".join(shown), '<button type="button" class="btn ghost show-all">%s (%d)</button>' % (e(labels["show_all"]), len(rows))


def card(kind, badge, rows, labels, clamp=False):
    body, more = limited(rows, labels)
    return ('<section class="card col %s%s"><h3 class="kindhead"><span class="kind">%s</span> %d</h3><ul class="items">%s</ul>%s</section>'
            % (kind, " clamp" if clamp else "", badge, len(rows), body, more))


def item_row(content, session, key, linked):
    """Above the session level an item leads to its session; on the session's own page it is the destination, so it stays plain."""
    anchor = "i-%s-%s" % (session["id"], key)
    if linked:
        return '<li data-go="s-%s" data-item="%s" tabindex="0">%s</li>' % (e(session["id"], quote=True), e(anchor, quote=True), content)
    return '<li id="%s">%s</li>' % (e(anchor, quote=True), content)


def open_cards(pairs, labels, every_kind, where, linked=True):
    """One card per kind of open item; the overview keeps all three, the levels below only those with items."""
    cards = []
    for kind in KINDS:
        rows = [item_row(item_body(item, where(group, session)), session, "o%d" % number, linked)
                for item, group, session, number in pairs if item["kind"] == kind]
        if rows or every_kind:
            cards.append(card(kind, icon(kind) + e(labels[kind]), rows, labels, clamp=every_kind))
    return cards


def lane(title, cards, empty):
    content = '<div class="cards">%s</div>' % "".join(cards) if cards else '<div class="card empty">%s%s</div>' % (icon("action"), e(empty))
    return '<p class="lanetitle">%s</p>%s' % (e(title), content)


def lanes(pairs, done, labels, linked):
    """What is still open, then what happened; the second row holds done until the report gains decided and found."""
    rows = [item_row('<div class="t">%s</div>' % code_chips(line), session, "d%d" % number, linked) for line, session, number in done]
    happened = [card("done", icon("action") + e(labels["done"]), rows, labels)] if rows else []
    return (lane(labels["still_open"], open_cards(pairs, labels, False, lambda group, session: "", linked), labels["nothing_open"])
            + lane(labels["what_happened"], happened, labels["nothing_happened"]))


def timeline(groups, span, labels, title, report, selected=None):
    """The same timeline at every level: the axis always spans the whole day, so a bar keeps its place when the level changes."""
    low, high = span
    width = max(high - low, 60)

    def at(clock):
        return 100.0 * (minutes(clock) - low) / width

    ticks = "".join('<span style="left:%.2f%%">%02d:00</span>' % (100.0 * (hour * 60 - low) / width, hour) for hour in range(low // 60, high // 60 + 1))
    rows = []
    for group, slug in groups:
        lanes_of = rows_for(group)
        # every bar keeps its height, and sessions side by side make the row taller
        height, gap = 20, 8
        bars = []
        for number, row in enumerate(lanes_of):
            for session in row["sessions"]:
                state = " has" if body_of(report, session).get("open") else ""
                if selected:
                    state += " lit" if session["id"] == selected else " dim"
                bars.append('<button type="button" class="bar%s" data-go="s-%s" aria-label="%s" style="left:%.2f%%;width:%.2f%%;top:%dpx;height:%dpx"></button>'
                            % (state, e(session["id"], quote=True), e("%s %s-%s" % (name_of(group), session["first"], session["last"]), quote=True),
                               at(session["first"]), max(at(session["last"]) - at(session["first"]), 0.8), 10 + number * (height + gap), height))
        track = '<div class="track" style="height:%dpx">%s</div>' % (20 + len(lanes_of) * height + (len(lanes_of) - 1) * gap, "".join(bars))
        rows.append('<button type="button" class="tname" data-go="r-%s">%s<span class="nm">%s</span></button>%s'
                    % (e(slug, quote=True), tile(group), e(name_of(group)), track))
    return ('<section class="card timeline" aria-label="%s"><h2>%s</h2><div class="grid"><span></span><div class="ticks">%s</div>%s</div></section>'
            % (e(labels["timeline"], quote=True), title, ticks, "".join(rows)))


def trail(*parts):
    """Where the page is, one step per level; every step but the last leads back up."""
    steps = []
    for number, (target, text) in enumerate(parts):
        if number == len(parts) - 1:
            steps.append('<span class="here">%s</span>' % text)
        else:
            steps.append('<button type="button" class="step" data-go="%s">%s</button>' % (e(target, quote=True), text))
    return '<nav class="trail">%s</nav>' % icon("chevron").join(steps)


def eyebrow(name, text):
    return '<span class="eyebrow">%s%s</span>' % (icon(name), e(text))


def overview_panel(digest, report, labels, groups, span):
    numbers = figures(digest)
    usage = numbers["tokens"]
    stats = [(numbers["sessions"], labels["sessions"]), (numbers["repositories"], labels["repositories"]), (numbers["prompts"], labels["prompts"]),
             ("%d / %d" % (numbers["completed"], numbers["pull_requests"]), labels["pull_requests"]),
             (compact(usage["charged"]), labels["tokens"]), (compact(usage["cached"]), labels["tokens_cached"]), (compact(usage["output"]), labels["output"])]
    parts = mix(all_sessions(digest))
    whole = sum(parts.values()) or 1
    mix_names = (("read", labels["cache_read"]), ("write", labels["cache_write"]), ("fresh", labels["uncached_input"]), ("out", labels["output"]))
    head = ('<section class="card head scope">%s<h1>%s</h1><p class="figures">%s</p>'
            '<div class="mix"><div class="mixbar" role="img" aria-label="%s">%s</div><div class="legend">%s</div></div></section>'
            % (eyebrow("calendar", "%s · %s" % (labels["level_day"], digest["day"])), e(report["headline"]),
               "".join("<span><b>%s</b> %s</span>" % (e(str(value)), e(label)) for value, label in stats), e(labels["token_mix"], quote=True),
               "".join('<span style="width:%.3f%%;background:var(--mix-%s)"></span>' % (100.0 * parts[key] / whole, key) for key, _ in mix_names),
               "".join('<span><i style="background:var(--mix-%s)"></i>%s %s (%.1f%%)</span>' % (key, e(name), compact(parts[key]), 100.0 * parts[key] / whole)
                       for key, name in mix_names)))
    pairs = [(item, group, session, number) for group, _ in groups for session in by_time(group) for number, item in enumerate(body_of(report, session).get("open", []))]
    cards = open_cards(pairs, labels, True, lambda group, session: '<div class="where">%s%s</div>' % (tile(group), e(name_of(group))))
    first = min(session["first"] for session in all_sessions(digest))
    last = max(session["last"] for session in all_sessions(digest))
    return (trail(("", e(labels["all_open"]))), head
            + timeline(groups, span, labels, '%s <span>%s–%s</span>' % (e(labels["timeline"]), e(first), e(last)), report)
            + '<p class="lanetitle">%s</p><div class="cards">%s</div>' % (e(labels["still_open"]), "".join(cards)))


def repository_head(group, labels, running):
    live = group.get("live") or {}
    chips = []
    if live.get("current_branch"):
        chips.append('<span class="chip">%s%s</span>' % (icon("branch"), e(live["current_branch"])))
    if group.get("is_repository") and (live.get("notes") or live.get("uncommitted") is None):
        # without this a repository git could not read shows no chips, exactly like a clean one
        chips.append('<span class="chip warn" title="%s">%s</span>' % (e("; ".join(live.get("notes") or []), quote=True), e(labels["state_unread"])))
    if live.get("uncommitted"):
        chips.append('<span class="chip warn">%s%s %d</span>' % (icon("pencil"), e(labels["uncommitted"]), live["uncommitted"]))
    unpushed = sum(branch.get("unpushed") or 0 for branch in live.get("branches", []))
    if unpushed:
        chips.append('<span class="chip warn">%s%s %d</span>' % (icon("up"), e(labels["unpushed"]), unpushed))
    if running:
        chips.append('<span class="chip live"><i class="dot"></i>%s</span>' % e(fill(labels["running_count"], n=running)))
    if group.get("current"):
        chips.append('<span class="chip here">%s%s</span>' % (icon("pin"), e(labels["here"])))
    buttons = ""
    if os.path.isabs(group["path"]):
        resumable = [session for session in group["sessions"] if session.get("cwd") and not session.get("running")]
        if resumable:
            # the latest session that has stopped, since a running one is already open somewhere
            last = max(resumable, key=lambda session: session["last"])
            buttons += button_link(deep_link(last["cwd"], "/resume " + last["id"]), icon("play") + e(labels["resume_last"]), "%s %s-%s" % (last["cwd"], last["first"], last["last"]))
        buttons += (button_link(deep_link(group["path"]), icon("plus") + e(labels["new_session"]), group["path"], ghost=True)
                    + copy_button(terminal_command(group["path"]), icon("terminal"), labels["copy_terminal"], labels)
                    + copy_button(group["path"], icon("folder"), labels["copy_path"], labels))
    return ('<section class="card head scope">%s<div class="top">%s<div class="grow"><h2>%s</h2><div class="path">%s</div></div><div class="buttons">%s</div></div>'
            '<div class="chips">%s</div></section>'
            % (eyebrow("repo", labels["level_repository"]), tile(group), e(name_of(group)), e(group["path"]), buttons, "".join(chips)))


def repository_panel(group, slug, report, labels, span):
    sessions = by_time(group)
    pairs = [(item, group, session, number) for session in sessions for number, item in enumerate(body_of(report, session).get("open", []))]
    done = [(line, session, number) for session in sessions for number, line in enumerate(body_of(report, session).get("done", []))]
    running = sum(1 for session in sessions if session.get("running"))
    return (trail(("", e(labels["all_open"])), ("r-" + slug, tile(group) + e(name_of(group)))),
            repository_head(group, labels, running)
            + timeline([(group, slug)], span, labels, e(fill(labels["session_count"], n=len(sessions), repository=name_of(group))), report)
            + lanes(pairs, done, labels, True))


def session_panel(group, slug, session, report, labels, span):
    sessions = by_time(group)
    body = body_of(report, session)
    usage = tokens(session["usage"].values())
    if session.get("running"):
        actions = '<span class="chip live big">%s%s</span>' % ('<i class="dot"></i>', e(labels["cannot_resume"]))
    elif session.get("cwd"):
        actions = (button_link(deep_link(session["cwd"], "/resume " + session["id"]), icon("play") + e(labels["resume"]), session["cwd"])
                   + copy_button(resume_command(session["cwd"], session["id"]), icon("copy") + e(labels["copy_resume"]), labels["copy_resume"], labels, wide=True))
    else:
        actions = ""
    head = ('<section class="card head scope">%s<div class="top"><div class="grow"><h2>%s–%s <span class="span">%s</span></h2>'
            '<div class="path">%s %s · %s %s · %s</div></div><div class="buttons">%s</div></div><div class="topics">%s</div></section>'
            % (eyebrow("clock", "%s · %s" % (labels["level_session"], fill(labels["session_position"], n=sessions.index(session) + 1, total=len(sessions)))),
               e(session["first"]), e(session["last"]), e(length(session, labels)), compact(usage["charged"]), e(labels["tokens"]),
               compact(usage["output"]), e(labels["output"]), e(session["id"]), actions, "".join("<span>%s</span>" % e(topic) for topic in body["topics"])))
    pairs = [(item, group, session, number) for number, item in enumerate(body.get("open", []))]
    return (trail(("", e(labels["all_open"])), ("r-" + slug, tile(group) + e(name_of(group))), ("s-" + session["id"], session_name(report, session))),
            head
            + timeline([(group, slug)], span, labels, e(fill(labels["session_count"], n=len(sessions), repository=name_of(group))), report, selected=session["id"])
            + lanes(pairs, [(line, session, number) for number, line in enumerate(body.get("done", []))], labels, False))


def sidebar(digest, report, labels, groups):
    total = sum(open_count(group, report) for group, _ in groups)
    rows = ['<button type="button" class="nav" data-go="" data-level="day">%s<span class="nm">%s</span><span class="badge">%s</span></button>'
            % (icon("list"), e(labels["all_open"]), badge(total))]
    for group, slug in groups:
        dot = '<i class="dot" title="%s" aria-label="%s"></i>' % (e(labels["running"], quote=True), e(labels["running"], quote=True)) if any(session.get("running") for session in group["sessions"]) else ""
        rows.append('<button type="button" class="nav" data-go="r-%s" data-repo="%s">%s<span class="nm">%s</span>%s<span class="badge">%s</span></button>'
                    % (e(slug, quote=True), e(slug, quote=True), tile(group), e(name_of(group)), dot, badge(open_count(group, report))))
        children = []
        for session in by_time(group):
            count = len(body_of(report, session).get("open", []))
            running = '<i class="dot" title="%s" aria-label="%s"></i>' % (e(labels["running"], quote=True), e(labels["running"], quote=True)) if session.get("running") else ""
            children.append('<button type="button" class="nav child" data-go="s-%s" title="%s">%s%s%s</button>'
                            % (e(session["id"], quote=True), e("%s-%s" % (session["first"], session["last"]), quote=True), '<span class="nm">%s</span>' % session_name(report, session),
                               running, '<span class="badge small">%s</span>' % badge(count) if count else ""))
        rows.append('<div class="children" data-children="%s" hidden>%s</div>' % (e(slug, quote=True), "".join(children)))
    return ('<aside class="card side"><div class="day">%s</div><div class="asof">%s %s</div>%s<hr><div class="sec">%s</div>%s</aside>'
            % (e(digest["day"]), e(labels["as_of"]), e(readable(digest.get("generated"))), rows[0], e(labels["repository_list"]), "".join(rows[1:])))


def badge(number):
    return "99+" if number > 99 else str(number)


# Lucide icons (ISC, some derived from Feather under MIT), copied as published; the plugin's NOTICE carries both licences
ICONS = {
    "list": "<path d='M3 5h.01'/><path d='M3 12h.01'/><path d='M3 19h.01'/><path d='M8 5h13'/><path d='M8 12h13'/><path d='M8 19h13'/>",
    "decision": "<path d='M12 13v8'/><path d='M12 3v3'/><path d='M2.354 10.354a1.207 1.207 0 0 1 0-1.708l2.06-2.06A2 2 0 0 1 5.828 6h12.344a2 2 0 0 1 1.414.586l2.06 2.06a1.207 1.207 0 0 1 0 1.708l-2.06 2.06a2 2 0 0 1-1.414.586H5.828a2 2 0 0 1-1.414-.586z'/>",
    "action": "<circle cx='12' cy='12' r='10'/><path d='m16 9-5.5 5.5L8 12'/>",
    "question": "<path d='m21 21-4.34-4.34'/><circle cx='11' cy='11' r='8'/>",
    "play": "<path d='M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z'/>",
    "copy": "<rect width='14' height='14' x='8' y='8' rx='2' ry='2'/><path d='M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2'/>",
    "terminal": "<path d='M12 19h8'/><path d='m4 17 6-6-6-6'/>",
    "plus": "<path d='M5 12h14'/><path d='M12 5v14'/>",
    "folder": "<path d='M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z'/>",
    "branch": "<path d='M15 6a9 9 0 0 0-9 9V3'/><circle cx='18' cy='6' r='3'/><circle cx='6' cy='18' r='3'/>",
    "pencil": "<path d='M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z'/><path d='m15 5 4 4'/>",
    "up": "<path d='m5 12 7-7 7 7'/><path d='M12 19V5'/>",
    "pin": "<path d='M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0'/><circle cx='12' cy='10' r='3'/>",
    "clock": "<circle cx='12' cy='12' r='10'/><path d='M12 6v6l4 2'/>",
    "calendar": "<path d='M8 2v3'/><path d='M16 2v3'/><rect x='3' y='3' width='18' height='18' rx='2'/><path d='M3 9h18'/>",
    "repo": "<path d='M18 19a5 5 0 0 1-5-5v8'/><path d='M9 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2v5'/><circle cx='13' cy='12' r='2'/><circle cx='20' cy='19' r='2'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2v2'/><path d='M12 20v2'/><path d='m4.93 4.93 1.41 1.41'/><path d='m17.66 17.66 1.41 1.41'/><path d='M2 12h2'/><path d='M20 12h2'/><path d='m6.34 17.66-1.41 1.41'/><path d='m19.07 4.93-1.41 1.41'/>",
    "moon": "<path d='M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401'/>",
    "chevron": "<path d='m9 18 6-6-6-6'/>",
    "mark": "<path d='M17 3a2 2 0 0 1 2 2v15a1 1 0 0 1-1.496.868l-4.512-2.578a2 2 0 0 0-1.984 0l-4.512 2.578A1 1 0 0 1 5 20V5a2 2 0 0 1 2-2z'/>",
}


def button_link(href, text_html, hint, ghost=False):
    return '<a class="btn%s" href="%s" title="%s">%s</a>' % (" ghost" if ghost else "", e(href, quote=True), e(hint, quote=True), text_html)


def copy_button(command, face_html, name, labels, wide=False):
    """A button that copies a command; one with only an icon names its action for screen readers and on hover."""
    return ('<button type="button" class="btn ghost copy%s" data-copy="%s" data-done="%s" title="%s" aria-label="%s">%s</button>'
            % ("" if wide else " icon-only", e(command, quote=True), e(labels["copied"], quote=True), e(command, quote=True), e(name, quote=True), face_html))


STYLE = """
:root { --ink:#1c2330; --soft:#4f5868; --faint:#4f5868; --line:rgba(28,35,48,.10); --accent:#24599a; --on-accent:#ffffff;
  --card:rgba(255,255,255,.55); --card-line:rgba(255,255,255,.80); --sheen:rgba(255,255,255,.35); --solid:#ffffff;
  --bar:rgba(60,74,99,.22); --openbar:#3b4a63; --on-openbar:#ffffff; --code-bg:rgba(36,89,154,.09);
  --decision:#7a3fb0; --decision-bg:#f0e7fa; --action:#0f6e66; --action-bg:#e0f2ef; --question:#9a5806; --question-bg:#fcefdc;
  --done:#1f7a4d; --warn:#a3324f; --warn-bg:#fbe6ea; --live:#1f7a4d;
  --mix-read:#a9c0dc; --mix-write:#5b8fd0; --mix-fresh:#24599a; --mix-out:#d08a2f;
  --lv-day:hsl(220 12% 38%); --lv-repo:hsl(84 75% 28%); --lv-session:hsl(315 60% 42%);
  --bg:linear-gradient(135deg, hsl(200 75% 88%) 0%, hsl(214 55% 95%) 50%, hsl(228 70% 90%) 100%); --bg-base:hsl(212 45% 95%);
  --shadow:0 1px 2px rgba(20,40,80,.06), 0 10px 30px rgba(20,40,80,.08);
  --sans:-apple-system,"Segoe UI","PingFang TC","Microsoft JhengHei",system-ui,sans-serif; --mono:ui-monospace,"Cascadia Code",Consolas,monospace;
  --emoji:"Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif; color-scheme:light }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --ink:#e6e9ef; --soft:#b3bccb; --faint:#a9b2c1; --line:rgba(255,255,255,.08);
  --accent:#8cbcf0; --on-accent:#13161b; --card:rgba(20,26,36,.36); --card-line:rgba(255,255,255,.12); --sheen:rgba(255,255,255,.06); --solid:#1d2430;
  --bar:rgba(195,207,226,.22); --openbar:#c3cfe2; --on-openbar:#13161b; --code-bg:rgba(140,188,240,.12);
  --decision:#c9a2ef; --decision-bg:#2e2140; --action:#6fd0c4; --action-bg:#173230; --question:#f0b35c; --question-bg:#3a2b16;
  --done:#6fd3a0; --warn:#f08aa2; --warn-bg:#3a1c24; --live:#6fd3a0; --mix-read:#3d5a80; --mix-write:#5b8fd0; --mix-fresh:#9cc3f0; --mix-out:#e0a050;
  --lv-day:hsl(220 15% 84%); --lv-repo:hsl(84 60% 60%); --lv-session:hsl(315 70% 72%);
  --bg:linear-gradient(135deg, hsl(203 55% 17%) 0%, hsl(216 38% 9%) 50%, hsl(228 45% 16%) 100%); --bg-base:hsl(216 35% 8%);
  --shadow:inset 0 1px 0 rgba(255,255,255,.10), 0 12px 32px rgba(0,0,0,.30); color-scheme:dark } }
:root[data-theme="dark"] { --ink:#e6e9ef; --soft:#b3bccb; --faint:#a9b2c1; --line:rgba(255,255,255,.08);
  --accent:#8cbcf0; --on-accent:#13161b; --card:rgba(20,26,36,.36); --card-line:rgba(255,255,255,.12); --sheen:rgba(255,255,255,.06); --solid:#1d2430;
  --bar:rgba(195,207,226,.22); --openbar:#c3cfe2; --on-openbar:#13161b; --code-bg:rgba(140,188,240,.12);
  --decision:#c9a2ef; --decision-bg:#2e2140; --action:#6fd0c4; --action-bg:#173230; --question:#f0b35c; --question-bg:#3a2b16;
  --done:#6fd3a0; --warn:#f08aa2; --warn-bg:#3a1c24; --live:#6fd3a0; --mix-read:#3d5a80; --mix-write:#5b8fd0; --mix-fresh:#9cc3f0; --mix-out:#e0a050;
  --lv-day:hsl(220 15% 84%); --lv-repo:hsl(84 60% 60%); --lv-session:hsl(315 70% 72%);
  --bg:linear-gradient(135deg, hsl(203 55% 17%) 0%, hsl(216 38% 9%) 50%, hsl(228 45% 16%) 100%); --bg-base:hsl(216 35% 8%);
  --shadow:inset 0 1px 0 rgba(255,255,255,.10), 0 12px 32px rgba(0,0,0,.30); color-scheme:dark }
:root[data-theme="light"] { color-scheme:light }
:root.lv-day { --lv:var(--lv-day) } :root.lv-repo { --lv:var(--lv-repo) } :root.lv-session { --lv:var(--lv-session) }
[hidden] { display:none !important }
html { min-height:100% }
body { margin:0; min-height:100%; background:var(--bg-base); color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.55 }
body::before { content:""; position:fixed; inset:0; z-index:-1; background:var(--bg) }
button { font:inherit; color:inherit }
.i { width:16px; height:16px; flex:none; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; vertical-align:-3px }
code { font-family:var(--mono); font-size:.86em; padding:1px 5px; border-radius:5px; background:var(--code-bg); white-space:nowrap }
:lang(zh) code { padding:1px 3px; margin:0 1px }
.app { display:grid; grid-template-columns:300px minmax(0, 1fr); grid-template-rows:auto 1fr; gap:14px 52px; padding:20px; box-sizing:border-box }
.brand { display:flex; gap:9px; align-items:center; padding:0 6px; font-size:14px; font-weight:700; min-height:30px }
.brand .i { color:var(--accent) }
.topbar { display:flex; gap:12px; justify-content:space-between; align-items:center; min-height:30px }
.trail-of { min-width:0 }
.card { background:linear-gradient(180deg, var(--sheen), transparent 38%), var(--card); border:1px solid var(--card-line); border-radius:14px; box-shadow:var(--shadow);
  -webkit-backdrop-filter:blur(26px) saturate(170%); backdrop-filter:blur(26px) saturate(170%) }
.side { padding:16px 10px 12px; display:flex; flex-direction:column; gap:2px; align-self:start }
.side .day { font-weight:700; font-size:18px; padding:0 10px 2px; font-variant-numeric:tabular-nums }
.side .asof { font-size:12px; color:var(--faint); padding:0 10px 12px; font-variant-numeric:tabular-nums }
.side hr { border:0; border-top:1px solid var(--line); margin:8px 4px }
.sec { font-size:11px; font-weight:600; color:var(--faint); padding:4px 10px; letter-spacing:.06em }
.nav { display:flex; align-items:center; gap:10px; width:100%; padding:7px 10px; border:0; border-radius:9px; background:none; text-align:left; font-size:14px; font-weight:500; cursor:pointer }
.nav .nm { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.nav.child .nm { display:block }
.nav .i { width:20px; height:20px; color:var(--accent) }
.nav.on { color:var(--lv); background:color-mix(in srgb, var(--lv) 15%, transparent); box-shadow:inset 3px 0 0 var(--lv) }
.nav.parent { color:var(--lv-repo) }
.children { display:grid; gap:1px; min-width:0; margin:0 0 4px 22px; padding-left:10px; border-left:1px solid var(--line) }
.nav.child { min-width:0; padding:5px 10px; font-size:13px; font-weight:400; color:var(--soft) }
.nav.child.on { color:var(--lv); font-weight:600 }
.at { font-variant-numeric:tabular-nums; color:var(--faint); margin-right:7px; font-weight:400 }
.badge { font-size:12px; font-weight:600; min-width:22px; text-align:center; padding:1px 7px; border-radius:999px; background:var(--openbar); color:var(--on-openbar); font-variant-numeric:tabular-nums }
.badge.small { font-size:11px; min-width:18px; padding:0 6px }
.tile { display:inline-grid; place-items:center; width:28px; height:28px; flex:none; border-radius:8px; font-size:14px; font-weight:700; font-family:var(--emoji);
  background:color-mix(in srgb, var(--accent) 14%, transparent); color:var(--accent) }
.dot { width:8px; height:8px; border-radius:50%; background:var(--live); box-shadow:0 0 0 3px color-mix(in srgb, var(--live) 25%, transparent); flex:none;
  animation:breathe 2s ease-in-out infinite }
@keyframes breathe { 50% { box-shadow:0 0 0 5px color-mix(in srgb, var(--live) 10%, transparent) } }
main { display:grid; gap:20px; align-content:start; min-width:0 }
.panel { display:grid; gap:20px; min-width:0 }
.trail { display:flex; flex-wrap:wrap; gap:6px; align-items:center; font-size:13px; color:var(--soft); min-height:30px }
.trail .step { background:none; border:0; padding:0; color:var(--soft); cursor:pointer; display:inline-flex; gap:6px; align-items:center }
.trail .here { color:var(--ink); font-weight:600; display:inline-flex; gap:6px; align-items:center }
.trail .tile, .where .tile, .tname .tile { width:20px; height:20px; font-size:11px; border-radius:6px }
.pill { flex:none; white-space:nowrap; display:inline-flex; padding:3px; border-radius:999px; border:1px solid var(--line); background:var(--card) }
.pill button { display:inline-flex; gap:6px; align-items:center; font-size:12.5px; padding:4px 12px; border:0; border-radius:999px; background:none; color:var(--soft); cursor:pointer }
.pill button[aria-checked="true"] { background:var(--accent); color:var(--on-accent); font-weight:600 }
.head { padding:18px 20px; display:grid; gap:10px }
.scope { border:1.5px solid var(--lv); background:linear-gradient(180deg, var(--sheen), transparent 38%), color-mix(in srgb, var(--lv) 9%, var(--card)) }
.eyebrow { display:inline-flex; gap:6px; align-items:center; justify-self:start; font-size:11px; font-weight:700; letter-spacing:.1em; color:var(--lv);
  padding:2px 8px; border-radius:6px; background:color-mix(in srgb, var(--lv) 16%, transparent) }
.eyebrow .i { width:13px; height:13px; vertical-align:0 }
:lang(zh) .eyebrow, :lang(zh) .lanetitle, :lang(zh) .sec { letter-spacing:0 }
h1 { margin:0; font-size:24px; line-height:1.3; text-wrap:balance }
.head h2 { margin:0; font-size:22px; font-variant-numeric:tabular-nums } .head h2 .span { font-weight:500; color:var(--soft); font-size:16px }
.head .top { display:flex; gap:14px; align-items:center; flex-wrap:wrap }
.head .top > .tile { width:52px; height:52px; font-size:26px; border-radius:12px }
.head .grow { flex:1; min-width:0 }
.path { font-family:var(--mono); font-size:12.5px; color:var(--faint); overflow-wrap:anywhere }
.figures { display:flex; flex-wrap:wrap; gap:4px 22px; margin:0; color:var(--soft); font-size:13px; font-variant-numeric:tabular-nums }
.figures b { color:var(--ink); font-weight:600 }
.mix { display:grid; gap:6px }
.mixbar { display:flex; height:6px; border-radius:6px; overflow:hidden; background:var(--line) }
.mixbar span { height:100% }
.legend { display:flex; flex-wrap:wrap; gap:4px 16px; font-size:12px; color:var(--soft) }
.legend i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; vertical-align:-1px }
.chips, .topics { display:flex; gap:8px; flex-wrap:wrap }
.topics span { font-size:13px; padding:3px 10px; border-radius:8px; background:var(--code-bg) }
.chip { display:inline-flex; gap:6px; align-items:center; font-size:12.5px; padding:3px 10px; border-radius:999px; border:1px solid var(--line); color:var(--soft); background:var(--card) }
.chip .i { width:14px; height:14px }
.chip.warn { background:var(--warn-bg); color:var(--warn); border-color:transparent }
.chip.live { color:var(--live); background:color-mix(in srgb, var(--live) 14%, transparent); border-color:transparent }
.chip.here { color:var(--accent); border-color:var(--accent) }
.chip.big { font-size:13.5px; padding:7px 14px }
.buttons { display:flex; gap:8px; flex-wrap:wrap }
.btn { display:inline-flex; gap:7px; align-items:center; font-size:13px; font-weight:500; padding:7px 13px; border-radius:8px; border:1px solid var(--accent);
  background:var(--accent); color:var(--on-accent); text-decoration:none; white-space:nowrap; cursor:pointer }
.btn.ghost { background:transparent; color:var(--accent) }
.btn.icon-only { padding:7px 9px }
.timeline { padding:16px 18px }
.timeline h2 { margin:0 0 6px; font-size:17px } .timeline h2 span { color:var(--soft); font-weight:500; font-size:14px }
.grid { display:grid; grid-template-columns:minmax(120px, max-content) minmax(0, 1fr); align-items:stretch }
.tname .nm { max-width:240px }
.ticks { position:relative; height:18px }
.ticks span { position:absolute; transform:translateX(-50%); font-size:11px; color:var(--faint); font-variant-numeric:tabular-nums }
.tname { display:flex; gap:8px; align-items:center; border:0; border-top:1px solid var(--line); background:none; padding:6px 12px 6px 0; text-align:left;
  font-weight:600; font-size:14px; cursor:pointer; min-width:0 }
.tname .nm { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.track { position:relative; border-top:1px solid var(--line) }
.bar { position:absolute; padding:0; border:0; border-radius:6px; background:var(--bar); cursor:pointer }
.bar.has { background:var(--openbar) }
.bar.dim { opacity:.35 }
.bar.lit { background:var(--lv); outline:2px solid var(--lv); outline-offset:2px }
svg.link { position:absolute; left:0; top:0; pointer-events:none; overflow:visible }
svg.link path { fill:none; stroke:var(--lv); stroke-width:2 } svg.link circle { fill:var(--lv) }
.lanetitle { margin:4px 0 -8px; font-size:12px; font-weight:600; letter-spacing:.08em; color:var(--faint); display:flex; gap:10px; align-items:center; text-transform:uppercase }
.lanetitle::after { content:""; flex:1; border-top:1px solid var(--line) }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:20px }
.col { padding:16px 18px; display:grid; gap:10px; align-content:start }
.kindhead { margin:0; display:flex; gap:8px; align-items:center; font-size:14px }
.kind { display:inline-flex; gap:5px; align-items:center; font-size:12px; font-weight:600; padding:2px 8px; border-radius:6px; white-space:nowrap }
.kind .i { width:13px; height:13px; vertical-align:0 }
.decision .kind { background:var(--decision-bg); color:var(--decision) }
.action .kind { background:var(--action-bg); color:var(--action) }
.question .kind { background:var(--question-bg); color:var(--question) }
.done .kind { background:transparent; color:var(--done); border:1px solid currentColor }
.done .t { font-weight:400; color:var(--soft) }
.items { margin:0; padding:0; list-style:none; display:grid; gap:12px }
.items li { border-top:1px solid var(--line); padding-top:12px; min-width:0 }
.items li:first-child { border-top:0; padding-top:0 }
.t { font-weight:600 }
.d { color:var(--soft); font-size:13.5px; margin-top:2px }
.clamp .d { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden }
.where { display:flex; gap:6px; align-items:center; color:var(--faint); font-size:12px; margin-top:4px }
.empty { padding:12px 18px; color:var(--soft); display:flex; gap:8px; align-items:center; border-style:dashed }
.empty .i { color:var(--done) }
.show-all { justify-self:start }
/* hover only says that something can be clicked: the outline and the level colour belong to the selection */
.nav:not(.on):hover, .tname:hover, .items li[data-go]:hover { background:color-mix(in srgb, var(--ink) 7%, transparent) }
.items li[data-go] { cursor:pointer; border-radius:8px; margin-inline:-8px; padding-inline:8px }
.items li[data-go]:hover .t { text-decoration:underline; text-underline-offset:3px; text-decoration-color:color-mix(in srgb, var(--ink) 35%, transparent) }
.bar:hover { filter:brightness(1.3) } .bar.dim:hover { opacity:.7 }
.trail .step:hover { color:var(--ink); text-decoration:underline; text-underline-offset:3px }
.btn:hover { filter:brightness(1.08) }
.nav:focus-visible, .tname:focus-visible, .bar:focus-visible, .btn:focus-visible, .step:focus-visible, .pill button:focus-visible, .items li:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
.items li.flash { animation:flash 1.6s ease-out; border-radius:8px }
@keyframes flash { 0%, 40% { background:color-mix(in srgb, var(--lv) 22%, transparent); box-shadow:0 0 0 6px color-mix(in srgb, var(--lv) 22%, transparent) } }
@media (prefers-reduced-motion: reduce) { .dot, .items li.flash { animation:none } .items li.flash { background:color-mix(in srgb, var(--lv) 18%, transparent) } }
/* below this width the panel and the page no longer sit side by side */
@media (max-width: 900px) { .app { grid-template-columns:minmax(0, 1fr); gap:14px } .brand { display:none } .grid { grid-template-columns:130px minmax(0, 1fr) } }
"""

SCRIPT = """
(function () {
  var root = document.documentElement;
  // two states only: the page opens in the system's theme, and the pill shows which one is on
  function current() { return root.getAttribute('data-theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); }
  function mark() { document.querySelectorAll('[data-theme-set]').forEach(function (b) { b.setAttribute('aria-checked', String(b.getAttribute('data-theme-set') === current())); }); }
  try { var saved = localStorage.getItem('remind-me-theme'); if (saved === 'light' || saved === 'dark') { root.setAttribute('data-theme', saved); } } catch (ignored) { }
  mark();
  function route() {
    var key = decodeURIComponent(location.hash.replace(/^#/, ''));
    var panel = document.getElementById('p-' + key) || document.getElementById('p-');
    document.querySelectorAll('.panel').forEach(function (p) { p.hidden = p !== panel; });
    document.querySelectorAll('.trail-of').forEach(function (t) { t.hidden = t.getAttribute('data-for') !== panel.getAttribute('data-key'); });
    var level = panel.getAttribute('data-level'), repo = panel.getAttribute('data-repo');
    root.classList.remove('lv-day', 'lv-repo', 'lv-session'); root.classList.add('lv-' + level);
    document.querySelectorAll('.nav').forEach(function (n) {
      var go = n.getAttribute('data-go');
      n.classList.toggle('on', go === panel.getAttribute('data-key'));
      n.classList.toggle('parent', level === 'session' && n.getAttribute('data-repo') === repo);
    });
    document.querySelectorAll('[data-children]').forEach(function (c) { c.hidden = c.getAttribute('data-children') !== repo; });
    document.title = panel.getAttribute('data-title');
    requestAnimationFrame(draw);
  }

  // the lines are measured from the laid-out page, so they are drawn after layout and again whenever it changes
  function textBox(el) { var r = document.createRange(); r.selectNodeContents(el); return r.getBoundingClientRect(); }
  function draw() {
    document.querySelectorAll('svg.link').forEach(function (old) { old.remove(); });
    if (window.innerWidth <= 900) { return; }
    var panel = document.querySelector('.panel:not([hidden])'), nav = document.querySelector('.nav.on');
    var head = panel && panel.querySelector('.scope');
    if (!nav || !head) { return; }
    var sx = window.scrollX, sy = window.scrollY, rn = nav.getBoundingClientRect(), rh = head.getBoundingClientRect();
    var x1 = rn.right + 10, y1 = rn.top + rn.height / 2, x2 = rh.left, y2 = rh.top + Math.min(rh.height / 2, 44), mx = Math.round((x1 + x2) / 2);
    var paths = ['M' + x1 + ' ' + y1 + ' H' + mx + ' V' + y2 + ' H' + x2], dots = [[x1, y1], [x2, y2]];
    var bar = panel.querySelector('.bar.lit');
    if (bar) {
      var rb = bar.getBoundingClientRect(), top = rh.bottom;
      // every text a line could run through: the hour labels and the timeline card's title
      var blocks = [].map.call(panel.querySelectorAll('.ticks span'), function (t) { return t.getBoundingClientRect(); });
      blocks.push(textBox(panel.querySelector('.timeline h2')));
      function clear(x) { return blocks.every(function (r) { return x < r.left - 10 || x > r.right + 10; }); }
      var down = null, start = rb.left + Math.min(28, rb.width / 2);
      // straight down into the bar wherever that clears the labels, nearest its start
      for (var d = 0; d < rb.width && down === null; d += 2) {
        [start + d, start - d].forEach(function (x) { x = Math.round(x); if (down === null && x > rb.left + 3 && x < rb.right - 3 && clear(x)) { down = x; } });
      }
      if (down !== null) {
        paths.push('M' + down + ' ' + top + ' V' + (rb.top - 4)); dots.push([down, top]);
      } else {
        // otherwise down at a half hour left of the bar, then across into its left end, through the dimmed bars before it
        var ticks = panel.querySelector('.ticks').getBoundingClientRect(), hours = panel.querySelectorAll('.ticks span').length - 1, side = null;
        for (var k = 0; k < hours; k++) { var cx = Math.round(ticks.left + (k + 0.5) * ticks.width / hours); if (cx < rb.left - 6 && clear(cx)) { side = cx; } }
        var x = side === null ? Math.round(rb.left + rb.width / 2) : side, mid = Math.round(rb.top + rb.height / 2);
        paths.push(side === null ? 'M' + x + ' ' + top + ' V' + (rb.top - 4) : 'M' + x + ' ' + top + ' V' + mid + ' H' + Math.round(rb.left - 4));
        dots.push([x, top]);
      }
    }
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'link'); svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('width', document.documentElement.scrollWidth); svg.setAttribute('height', document.documentElement.scrollHeight);
    svg.innerHTML = '<g transform="translate(' + sx + ' ' + sy + ')">' + paths.map(function (p) { return '<path d="' + p + '"/>'; }).join('')
      + dots.map(function (p) { return '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="3.5"/>'; }).join('') + '</g>';
    document.body.appendChild(svg);
  }
  window.addEventListener('load', function () { setTimeout(draw, 200); });
  window.addEventListener('resize', draw);
  window.addEventListener('hashchange', function () { route(); window.scrollTo(0, 0); });
  route();
  function point(anchor) {
    var item = anchor && document.getElementById(anchor);
    if (!item) { return; }
    item.classList.remove('flash'); void item.offsetWidth; item.classList.add('flash');
    item.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
  function go(key) { if (key) { location.hash = key; } else { history.pushState(null, '', location.pathname + location.search); route(); } }
  window.addEventListener('popstate', route);
  document.addEventListener('keydown', function (event) {
    var item = event.target.closest('li[data-go]');
    if (item && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); go(item.getAttribute('data-go')); point(item.getAttribute('data-item')); }
  });
  document.addEventListener('click', function (event) {
    var set = event.target.closest('[data-theme-set]');
    if (set) { root.setAttribute('data-theme', set.getAttribute('data-theme-set')); mark(); try { localStorage.setItem('remind-me-theme', current()); } catch (ignored) { } return; }
    var all = event.target.closest('button.show-all');
    if (all) { all.parentNode.querySelectorAll('li.more').forEach(function (li) { li.hidden = false; }); all.hidden = true; return; }
    var button = event.target.closest('button.copy');
    if (button) {
      var text = button.getAttribute('data-copy'), face = button.innerHTML;
      function done() { button.textContent = button.getAttribute('data-done'); setTimeout(function () { button.innerHTML = face; }, 1500); }
      function fallback() { var area = document.createElement('textarea'); area.value = text; document.body.appendChild(area); area.select();
        try { if (document.execCommand('copy')) { done(); } } catch (ignored) { } document.body.removeChild(area); }
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done, fallback); } else { fallback(); }
      return;
    }
    var target = event.target.closest('[data-go]');
    if (target) { go(target.getAttribute('data-go')); point(target.getAttribute('data-item')); }
  });
})();
"""


def page(report, digest):
    labels = report["labels"]
    groups = [(group, slugs(digest["repositories"])[group["path"]]) for group in ordered(digest, report)]
    sessions = all_sessions(digest)
    span = ((min(minutes(session["first"]) for session in sessions) // 60) * 60, (max(minutes(session["last"]) for session in sessions) // 60 + 1) * 60)
    title = "remind-me %s" % report["day"]
    built = [("", "day", "", title, overview_panel(digest, report, labels, groups, span))]
    for group, slug in groups:
        built.append(("r-" + slug, "repo", slug, "%s · %s" % (name_of(group), title), repository_panel(group, slug, report, labels, span)))
        for session in by_time(group):
            built.append(("s-" + session["id"], "session", slug, "%s %s · %s" % (name_of(group), session["first"], title),
                          session_panel(group, slug, session, report, labels, span)))
    panels = "".join('<section class="panel" id="p-%s" data-key="%s" data-level="%s" data-repo="%s" data-title="%s"%s>%s</section>'
                     % (e(key, quote=True), e(key, quote=True), level, e(slug, quote=True), e(name, quote=True), "" if not key else " hidden", body)
                     for key, level, slug, name, (_, body) in built)
    trails = "".join('<div class="trail-of" data-for="%s"%s>%s</div>' % (e(key, quote=True), "" if not key else " hidden", way)
                     for key, _, _, _, (way, _) in built)
    pill = ('<div class="pill" role="radiogroup" aria-label="%s"><button type="button" role="radio" data-theme-set="light">%s%s</button>'
            '<button type="button" role="radio" data-theme-set="dark">%s%s</button></div>'
            % (e(labels["theme"], quote=True), icon("sun"), e(labels["theme_light"]), icon("moon"), e(labels["theme_dark"])))
    return ('<!doctype html><html lang="%s" class="lv-day"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><div class="app">'
            '<div class="brand">%sremind-me</div><div class="topbar">%s%s</div>%s<main>%s</main>'
            '</div><script>%s</script></body></html>'
            % (e(report["language"], quote=True), e(title), STYLE, icon("mark"), trails, pill, sidebar(digest, report, labels, groups), panels, SCRIPT))


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
