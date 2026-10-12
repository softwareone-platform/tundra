"""Render a remind-me report as a local HTML page and print its summary for the session.

The model writes only what needs judgement: each session's topics, what it decided, did and found out, what is still open,
and an emoji per repository.
Everything countable comes from the digest the collector wrote, so no figure on the page is the model's:
the times, the token usage, the pull request states, the folders, and the commands that reopen a session.

Usage:
    python render.py <report.json> --digest <digest.json> --out <report.html> [--open]

Exit status 2 lists what in the report does not match the schema in report-schema.md, and nothing is written.
"""

import argparse
import datetime
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
          "show_all", "repository_list", "decided", "found", "cache_read", "cache_write", "uncached_input", "decision", "action", "question",
          "level_day", "level_range", "level_repository", "level_session", "session_position", "still_open", "what_happened",
          "duration", "duration_minutes", "timeline", "session_count", "running_count", "cannot_resume", "token_mix",
          "more_in_repository", "newer", "older", "maximise", "restore")
# the labels that carry numbers or names, and the placeholders each must keep so the renderer can fill them in
PLACEHOLDERS = {"session_position": ("n", "total"), "duration": ("hours", "minutes"), "duration_minutes": ("minutes",),
                "session_count": ("n", "repository"), "running_count": ("n",),
                "more_in_repository": ("n", "repository"), "newer": ("n",), "older": ("n",)}
KINDS = ("decision", "action", "question")
# each open kind has a twin among what happened: a decision is decided, an action done, a question found
TWINS = (("decided", "decision", "c"), ("done", "action", "d"), ("found", "question", "f"))
TOP_KEYS = ("from", "to", "language", "headline", "labels", "sessions", "marks")
SESSION_KEYS = ("topics", "open", "decided", "done", "found")
ITEM_KEYS = ("kind", "text", "detail")
# a kind with more open items than this shows its first ones and folds the rest behind a button
OVERVIEW_LIMIT = 10
# the overview shows each repository's latest few, since a repository's items are usually about the same work, and links to the rest
PER_REPOSITORY = 3
# the side panel lists at most this many of a repository's sessions at a time, in pages of an even share
SIDE_PAGE = 8

e = html.escape


def problems_in(report, digest):
    """Everything in the report that the page cannot be built from, as readable lines."""
    if not isinstance(report, dict):
        return ["the report must be a JSON object"]
    found = []
    for key in ("from", "to", "language", "headline", "labels", "sessions"):
        if key not in report:
            found.append("missing top-level field: %s" % key)
    if found:
        return found
    for key in ("from", "to"):
        if report[key] != digest.get(key):
            found.append("report %s %s is not the digest's %s %s" % (key, report[key], key, digest.get(key)))
    # the page escapes these as text, so anything but a string stops the render with a traceback rather than this list
    for key in ("language", "headline"):
        if not isinstance(report[key], str) or not report[key].strip():
            found.append("%s must be a non-blank string" % key)
    labels = report["labels"]
    if not isinstance(labels, dict):
        found.append("labels must be an object")
    else:
        missing = [label for label in LABELS if not isinstance(labels.get(label), str) or not labels[label].strip()]
        if missing:
            found.append("labels missing: %s" % ", ".join(missing))
        for label, names in PLACEHOLDERS.items():
            lost = [name for name in names if "{%s}" % name not in str(labels.get(label, ""))]
            if label not in missing and lost:
                found.append("label %s must keep %s" % (label, ", ".join("{%s}" % name for name in lost)))
    # a misspelt key would otherwise drop its content without a word
    found += ["unknown top-level field: %s" % key for key in report if key not in TOP_KEYS]
    if not isinstance(report["sessions"], dict):
        found.append("sessions must be an object")
        return list(dict.fromkeys(found))
    known = {session["id"] for group in digest.get("repositories", []) for session in group["sessions"]}
    for session_id, body in report["sessions"].items():
        if session_id not in known:
            found.append("session %s is not in the digest" % session_id)
            continue
        if not isinstance(body, dict):
            found.append("session %s must be an object" % session_id)
            continue
        found += ["session %s: unknown field %s" % (session_id, key) for key in body if key not in SESSION_KEYS]
        if not body.get("topics") or not lines_ok(body.get("topics")):
            found.append("session %s needs topics, a list of non-blank strings" % session_id)
        for key in ("decided", "done", "found"):
            if key in body and not lines_ok(body[key]):
                found.append("session %s: %s must be a list of non-blank strings" % (session_id, key))
        items = body.get("open", [])
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            found.append("session %s: open must be a list of objects" % session_id)
            continue
        for item in items:
            found += ["session %s: an open item has an unknown field %s" % (session_id, key) for key in item if key not in ITEM_KEYS]
            if item.get("kind") not in KINDS:
                found.append("session %s: open item kind must be one of %s" % (session_id, ", ".join(KINDS)))
            if not isinstance(item.get("text"), str) or not item["text"].strip():
                found.append("session %s: an open item has no text" % session_id)
            if "detail" in item and not isinstance(item["detail"], str):
                found.append("session %s: an open item's detail must be a string" % session_id)
    # a session no reader judged is in no fragment, so the file its reader was given is what says which reader to send again,
    # and with one repository there was no reader, so its name stands in
    readers = len(digest.get("repositories", [])) > 1
    home = {session["id"]: "in %s" % group["split"] if readers and group.get("split") else "repository %s" % name_of(group)
            for group in digest.get("repositories", []) for session in group["sessions"]}
    for session_id in sorted(known - set(report["sessions"])):
        found.append("session %s from the digest has no entry in the report (%s)" % (session_id, home[session_id]))
    # one line per problem, however many items share it
    return list(dict.fromkeys(found))


def merged(report, fragments):
    """The report with each reader's sessions folded in, and the problems the fragments carry themselves.

    Several repositories are judged by one reader each, each writing only its sessions,
    so the sessions arrive as fragments beside a report that holds everything else.
    """
    found = []
    if not fragments or not isinstance(report, dict):
        return report, found
    # a malformed sessions field would otherwise vanish under the fragments' sessions
    if "sessions" in report and not isinstance(report["sessions"], dict):
        found.append("sessions must be an object")
    sessions = dict(report["sessions"]) if isinstance(report.get("sessions"), dict) else {}
    origin = {session_id: "the report" for session_id in sessions}
    for name, fragment in fragments:
        if not isinstance(fragment, dict) or not isinstance(fragment.get("sessions"), dict):
            found.append("fragment %s must be an object with a sessions object" % name)
            continue
        found += ["fragment %s: unknown field %s" % (name, key) for key in fragment if key != "sessions"]
        for session_id, body in fragment["sessions"].items():
            # two readers judging one session would leave whichever ran last, so a repeat is refused rather than settled by order
            if session_id in sessions:
                found.append("session %s is in %s and in fragment %s" % (session_id, origin[session_id], name))
            else:
                origin[session_id] = "fragment " + name
            sessions[session_id] = body
    return dict(report, sessions=sessions), found


def located(problems, fragments):
    """Each problem with a session that came from a fragment, prefixed with that fragment's name.

    The orchestrator never reads the digest, so a session id alone does not tell it which reader to send again.
    """
    owner = {}
    for name, fragment in fragments:
        if isinstance(fragment, dict) and isinstance(fragment.get("sessions"), dict):
            # the last fragment wins, as it does in merged(), so a problem names the reader whose body was checked
            for session_id in fragment["sessions"]:
                owner[session_id] = name
    named = []
    for problem in problems:
        # most lines go on "session <id>: ...", and the colon is not part of the id
        match = re.match(r"session ([^\s:]+)", problem)
        name = owner.get(match.group(1)) if match else None
        named.append("fragment %s: %s" % (name, problem) if name else problem)
    return named


def lines_ok(value):
    return isinstance(value, list) and all(isinstance(line, str) and line.strip() for line in value)


def emoji_like(value):
    """One short emoji: no letters or digits, no flag (Windows draws one as two letters), and something from the emoji blocks."""
    if not isinstance(value, str) or not value.strip() or len(value) > 8:
        return False
    points = [ord(char) for char in value]
    if any(char.isascii() and char.isalnum() for char in value) or any(0x1F1E6 <= point <= 0x1F1FF for point in points):
        return False
    return any(0x1F300 <= point <= 0x1FAFF or 0x2600 <= point <= 0x27BF for point in points)


def same_path(left, right):
    """The model copies Windows paths through JSON escaping, so case, slash direction and a trailing separator are not held against it."""
    def plain(path):
        return path.replace("\\", "/").rstrip("/").casefold()
    return plain(left) == plain(right)


def marks(report, groups):
    """Each repository's mark, decided once: the model's emoji where it is usable and not already taken on the page, else nothing."""
    given = report.get("marks")
    chosen, used, ignored = {}, set(), []
    if given is not None and not isinstance(given, dict):
        return chosen, ["marks is not an object"]
    given = given or {}
    for key, value in given.items():
        if not any(same_path(key, group["path"]) for group in groups):
            ignored.append("%s (not a repository in the digest)" % key)
    for group in groups:
        value = next((value for key, value in given.items() if same_path(key, group["path"])), None)
        if value is None:
            continue
        bare = value.replace("\ufe0f", "") if isinstance(value, str) else value
        if not emoji_like(value):
            ignored.append("%s (not one emoji)" % group["path"])
        elif bare in used:
            ignored.append("%s (emoji already used)" % group["path"])
        else:
            chosen[group["path"]] = value
            used.add(bare)
    return chosen, ignored


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


# one report covers either one day, drawn by the hour, or a range, drawn a column per day; page() and summary() set which
VIEW = {"range": False, "axis": None}


def single(digest):
    return digest["from"] == digest["to"]


def span_text(digest):
    return digest["from"] if single(digest) else "%s – %s" % (digest["from"], digest["to"])


def day_of(stamp):
    return stamp.partition(" ")[0]


def clock(stamp):
    """Minutes past midnight of a YYYY-MM-DD HH:MM stamp."""
    hours, mins = stamp.partition(" ")[2].split(":")
    return int(hours) * 60 + int(mins)


def minutes(stamp):
    """Minutes since a fixed start, so stamps on different days compare and subtract."""
    return datetime.date.fromisoformat(day_of(stamp)).toordinal() * 1440 + clock(stamp)


def shown(stamp):
    """A stamp as the page prints it: the time alone on one day's page, the date as well on a range's."""
    if VIEW["range"]:
        return "%s %s" % (day_of(stamp)[5:], stamp.partition(" ")[2])
    return stamp.partition(" ")[2]


def stamp_html(stamp):
    """On a range the page script rewrites each date in the report's language, so the date travels in a time element."""
    if VIEW["range"]:
        return '<time datetime="%s">%s</time>' % (e(stamp.replace(" ", "T"), quote=True), e(shown(stamp)))
    return e(shown(stamp))


def day_axis(digest):
    """The range as columns: a day with a session is a column, and a run of days without one folds into a narrow gap."""
    sessions = all_sessions(digest)
    active = {day for session in sessions for day in session["days"]}
    clocks = [clock(session[key]) for session in sessions for key in ("first", "last")]
    low, high = (min(clocks) // 60) * 60, (max(clocks) // 60 + 1) * 60
    columns = []
    day, end = datetime.date.fromisoformat(digest["from"]), datetime.date.fromisoformat(digest["to"])
    while day <= end:
        if day.isoformat() in active:
            columns.append({"day": day.isoformat(), "weight": 1.0, "monday": day.weekday() == 0})
        elif columns and columns[-1].get("gap"):
            columns[-1]["weight"] = 0.28
        else:
            columns.append({"gap": True, "weight": 0.28})
        day += datetime.timedelta(days=1)
    total, offset = sum(column["weight"] for column in columns), 0.0
    for column in columns:
        column["left"] = 100.0 * offset / total
        column["width"] = 100.0 * column["weight"] / total
        offset += column["weight"]
    return {"columns": columns, "low": low, "high": max(high, low + 60), "index": {c["day"]: c for c in columns if not c.get("gap")}}


def position(stamp):
    """Where a stamp falls on the range's axis, in percent of the track."""
    axis = VIEW["axis"]
    column = axis["index"][day_of(stamp)]
    # a gutter of about 3px on each side of a day, so a session ending late never touches one starting early the next day
    pad = min(0.06, 3.0 / max(column["width"] * 10.0, 1.0))
    share = (clock(stamp) - axis["low"]) / float(axis["high"] - axis["low"])
    return column["left"] + column["width"] * (pad + (1 - 2 * pad) * share)


def rows_for(group):
    """Sessions that ran at the same time get a row each, so no bar hides another."""
    rows = []
    for session in group["sessions"]:
        if VIEW["range"]:
            # a bar is never narrower than its minimum, so on a month's axis two sessions an hour apart would overlap on the page
            left = position(session["first"])
            spans = [(left, max(position(session["last"]), left + 0.9) + 0.25)]
        else:
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
    if day_of(session["first"]) != day_of(session["last"]):
        # a session resumed on a later day has no meaningful length, and its two dates already say how far it ran
        return ""
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
    """The repository's mark everywhere it is drawn: the emoji page() resolved for it, or its initial."""
    if group.get("mark"):
        return '<span class="tile emoji" aria-hidden="true">%s</span>' % e(group["mark"])
    return '<span class="tile" aria-hidden="true">%s</span>' % e(name_of(group)[:1].upper())


def session_name(report, session):
    return '<span class="at">%s</span>%s' % (stamp_html(session["first"]), e(body_of(report, session)["topics"][0]))


def code_chips(text):
    """Ticket keys, pull requests, branches and commands read as code, so they stand out in a line of prose."""
    # the model writes in Markdown habits, so a backticked span is code as it stands and the patterns run only outside it
    parts = re.split(r"`([^`\n]+)`", text)
    out = []
    for number, part in enumerate(parts):
        if number % 2:
            out.append("<code>%s</code>" % e(part))
        else:
            out.append(re.sub(r"(?<![\w/-])(![0-9]+|#[0-9]+|[A-Z][A-Z0-9]+-[0-9]+(?:-[0-9]+)*|(?:release|feature|bugfix|hotfix)/[\w.-]+|[\w-]+\.py(?: --[\w-]+)*)(?![\w-])",
                              r"<code>\1</code>", e(part)))
    return "".join(out)


def newest_first(pairs):
    """The latest session's items first, and within a session the last the model listed, since the page is read to pick work up again."""
    # the session id keeps one session's items together when two sessions end in the same minute
    return sorted(pairs, key=lambda pair: (minutes(pair[2]["last"]), pair[2]["id"], pair[3]), reverse=True)


def dated(where, session):
    """On a range an item says which day its session last worked on it, since the list spans many days."""
    if not VIEW["range"]:
        return where
    day = day_of(session["last"])
    chip = '<span class="date"><time datetime="%s">%s</time></span>' % (e(day, quote=True), e(day[5:]))
    if where:
        return where.replace('<div class="where">', '<div class="where">' + chip + '<span class="sep">\u00b7</span>', 1)
    return '<div class="where">%s</div>' % chip


def item_body(item, where=""):
    detail = '<div class="d">%s</div>' % code_chips(item["detail"]) if str(item.get("detail", "")).strip() else ""
    return '<div class="t">%s</div>%s%s' % (code_chips(item["text"]), detail, where)


def limited(rows, labels, key=None):
    """A kind with more items than the limit shows its first ones, and a button maximises the card to show them all."""
    if len(rows) <= OVERVIEW_LIMIT:
        return "".join(rows), ""
    shown = rows[:OVERVIEW_LIMIT] + [row.replace("<li", '<li class="more" hidden', 1) for row in rows[OVERVIEW_LIMIT:]]
    return "".join(shown), ('<button type="button" class="btn ghost show-all" data-max-kind="%s">%s (%d)</button>'
                            % (e(key or "", quote=True), e(labels["show_all"]), len(rows)))


def max_buttons(key, labels):
    """Maximise and restore sit in the card's own heading; the page shows the one that fits the card's state."""
    return ('<button type="button" class="maxbtn grow" data-max-kind="%s" title="%s" aria-label="%s">%s</button>'
            '<button type="button" class="maxbtn shrink" data-max-restore title="%s" aria-label="%s">%s</button>'
            % (e(key, quote=True), e(labels["maximise"], quote=True), e(labels["maximise"], quote=True), icon("maximise"),
               e(labels["restore"], quote=True), e(labels["restore"], quote=True), icon("restore")))


def card(kind, badge, rows, labels, clamp=False, key=None):
    body, more = limited(rows, labels, key)
    return ('<section class="card col %s%s" data-kind="%s" data-count="%d"><h3 class="kindhead"><span class="kind">%s</span> %d%s</h3><ul class="items">%s</ul>%s</section>'
            % (kind, " clamp" if clamp else "", e(key or kind, quote=True), len(rows), badge, len(rows), max_buttons(key or kind, labels), body, more))


def grouped_card(kind, badge, rows_by_group, labels, clamp=False, key=None):
    """The overview's card: a block per repository with its latest items and a link to the rest on the repository's page."""
    blocks = []
    for group, slug, rows in rows_by_group:
        if not rows:
            continue
        rest = len(rows) - PER_REPOSITORY
        more = ('<button type="button" class="btn quiet more-link" data-go="r-%s">%s</button>'
                % (e(slug, quote=True), e(fill(labels["more_in_repository"], n=rest, repository=name_of(group))))) if rest > 0 else ""
        # the rest stay in the page for the maximised card, which shows each repository whole
        rows = rows[:PER_REPOSITORY] + [row.replace("<li", '<li class="beyond"', 1) for row in rows[PER_REPOSITORY:]]
        blocks.append('<div class="group"><div class="ghead">%s<span class="nm">%s</span><b>%d</b></div><ul class="items">%s</ul>%s</div>'
                      % (tile(group), e(name_of(group)), len(rows), "".join(rows), more))
    total = sum(len(rows) for _, _, rows in rows_by_group)
    return ('<section class="card col %s%s" data-kind="%s" data-count="%d"><h3 class="kindhead"><span class="kind">%s</span> %d%s</h3><div class="groups">%s</div></section>'
            % (kind, " clamp" if clamp else "", e(key or kind, quote=True), total, badge, total, max_buttons(key or kind, labels),
               "".join(blocks) or '<ul class="items"></ul>'))


def item_row(content, session, key, linked):
    """Above the session level an item leads to its session; on the session's own page it is the destination, so it stays plain."""
    anchor = "i-%s-%s" % (session["id"], key)
    # a maximised card on a range sets a heading over each day, from the day its session last worked on the item
    day = ' data-day="%s"' % e(day_of(session["last"]), quote=True) if VIEW["range"] and linked else ""
    if linked:
        return '<li data-go="s-%s" data-item="%s"%s tabindex="0">%s</li>' % (e(session["id"], quote=True), e(anchor, quote=True), day, content)
    return '<li id="%s">%s</li>' % (e(anchor, quote=True), content)


def open_cards(pairs, labels, every_kind, where, linked=True, groups=None):
    """One card per kind of open item; the overview keeps all three, grouped by repository, the levels below only those with items."""
    cards = []
    pairs = newest_first(pairs)
    for kind in KINDS:
        chosen = [pair for pair in pairs if pair[0]["kind"] == kind]
        if not chosen and not every_kind:
            continue
        row = lambda item, group, session, number: item_row(item_body(item, dated(where(group, session), session) if linked else where(group, session)),
                                                            session, "o%d" % number, linked)
        badge_html = icon(kind) + e(labels[kind])
        if groups is not None:
            cards.append(grouped_card(kind, badge_html, [(group, slug, [row(*pair) for pair in chosen if pair[1]["path"] == group["path"]]) for group, slug in groups],
                                      labels, clamp=every_kind, key=kind))
        else:
            cards.append(card(kind, badge_html, [row(*pair) for pair in chosen], labels, clamp=every_kind, key=kind))
    return cards


def lane(title, cards, empty):
    content = '<div class="cards">%s</div>' % "".join(cards) if cards else '<div class="card empty">%s%s</div>' % (icon("action"), e(empty))
    return '<p class="lanetitle">%s</p>%s' % (e(title), content)


def happened_cards(entries, report, labels, linked, where, groups=None):
    """What happened, one card per list that has lines, each carrying the icon and colour of the open kind it answers."""
    cards = []
    for key, kind, prefix in TWINS:
        lines = newest_first([(line, group, session, number) for group, session in entries for number, line in enumerate(body_of(report, session).get(key, []))])
        row = lambda line, group, session, number: item_row('<div class="t">%s</div>%s' % (code_chips(line), dated(where(group, session), session) if linked else where(group, session)),
                                                            session, "%s%d" % (prefix, number), linked)
        if not lines:
            continue
        badge_html = icon(kind) + e(labels[key])
        if groups is not None:
            cards.append(grouped_card("twin " + kind, badge_html, [(group, slug, [row(*one) for one in lines if one[1]["path"] == group["path"]]) for group, slug in groups],
                                      labels, key=key))
        else:
            cards.append(card("twin " + kind, badge_html, [row(*one) for one in lines], labels, key=key))
    return cards


def lanes(pairs, entries, report, labels, linked):
    """What is still open, then what happened."""
    happened = happened_cards(entries, report, labels, linked, lambda group, session: "")
    return (lane(labels["still_open"], open_cards(pairs, labels, False, lambda group, session: "", linked), labels["nothing_open"])
            + lane(labels["what_happened"], happened, labels["nothing_happened"]))


def timeline(groups, span, labels, title, report, selected=None):
    """The same timeline at every level: the axis always spans the whole day, so a bar keeps its place when the level changes."""
    if VIEW["range"]:
        axis = VIEW["axis"]
        # the days with sessions are labelled, Mondays stand out, and the connector may drop down any column edge
        ticks = "".join('<span class="dtick%s" style="left:%.3f%%"><time datetime="%s">%s</time></span>'
                        % (" mon" if column["monday"] else "", column["left"] + column["width"] / 2, column["day"], column["day"][5:])
                        for column in axis["columns"] if not column.get("gap"))
        cuts = " ".join("%.3f" % column["left"] for column in axis["columns"][1:])
        guides = "".join('<span class="gap" style="left:%.3f%%;width:%.3f%%"></span>' % (column["left"], column["width"]) if column.get("gap")
                         else '<span class="dayline%s" style="left:%.3f%%"></span>' % (" mon" if column["monday"] else "", column["left"])
                         for column in axis["columns"])
        at, minimum, ticks_attrs = position, 0.9, ' class="ticks days" data-cuts="%s"' % cuts
    else:
        low, high = span
        width = max(high - low, 60)

        def at(stamp):
            return 100.0 * (clock(stamp) - low) / width

        ticks = "".join('<span style="left:%.2f%%">%02d:00</span>' % (100.0 * (hour * 60 - low) / width, hour) for hour in range(low // 60, high // 60 + 1))
        guides, minimum, ticks_attrs = "", 0.8, ' class="ticks"'
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
                            % (state, e(session["id"], quote=True), e("%s %s-%s" % (name_of(group), shown(session["first"]), shown(session["last"])), quote=True),
                               at(session["first"]), max(at(session["last"]) - at(session["first"]), minimum), 10 + number * (height + gap), height))
        track = '<div class="track" style="height:%dpx">%s%s</div>' % (20 + len(lanes_of) * height + (len(lanes_of) - 1) * gap, guides, "".join(bars))
        rows.append('<button type="button" class="tname" data-go="r-%s">%s<span class="nm">%s</span></button>%s'
                    % (e(slug, quote=True), tile(group), e(name_of(group)), track))
    return ('<section class="card timeline" aria-label="%s"><h2>%s</h2><div class="grid"><span></span><div%s>%s</div>%s</div></section>'
            % (e(labels["timeline"], quote=True), title, ticks_attrs, ticks, "".join(rows)))


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
            % (eyebrow("calendar", "%s · %s" % (labels["level_day" if single(digest) else "level_range"], span_text(digest))), e(report["headline"]),
               "".join("<span><b>%s</b> %s</span>" % (e(str(value)), e(label)) for value, label in stats), e(labels["token_mix"], quote=True),
               "".join('<span style="width:%.3f%%;background:var(--mix-%s)"></span>' % (100.0 * parts[key] / whole, key) for key, _ in mix_names),
               "".join('<span><i style="background:var(--mix-%s)"></i>%s %s (%.1f%%)</span>' % (key, e(name), compact(parts[key]), 100.0 * parts[key] / whole)
                       for key, name in mix_names)))
    pairs = [(item, group, session, number) for group, _ in groups for session in by_time(group) for number, item in enumerate(body_of(report, session).get("open", []))]
    # the repository heads each block of the overview's cards, so an item there carries only its day
    cards = open_cards(pairs, labels, True, lambda group, session: "", groups=groups)
    first = stamp_html(min(session["first"] for session in all_sessions(digest)))
    last = stamp_html(max(session["last"] for session in all_sessions(digest)))
    return (trail(("", e(labels["all_open"]))), head
            + timeline(groups, span, labels, '%s <span>%s–%s</span>' % (e(labels["timeline"]), first, last), report)
            + '<p class="lanetitle">%s</p><div class="cards">%s</div>' % (e(labels["still_open"]), "".join(cards))
            + folded_happened(groups, report, labels))


def folded_happened(groups, report, labels):
    entries = [(group, session) for group, _ in groups for session in by_time(group)]
    cards = happened_cards(entries, report, labels, True, lambda group, session: "", groups=groups)
    if not cards:
        return ""
    counts = "".join('<span class="kind %s">%s%s %d</span>' % (kind, icon(kind), e(labels[key]), total)
                     for key, kind, _ in TWINS
                     for total in [sum(len(body_of(report, session).get(key, [])) for _, session in entries)] if total)
    return ('<details class="folded"><summary class="lanetitle">%s%s<span class="counts">%s</span></summary><div class="cards">%s</div></details>'
            % (icon("chevron"), e(labels["what_happened"]), counts, "".join(cards)))


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
            buttons += button_link(deep_link(last["cwd"], "/resume " + last["id"]), icon("play") + e(labels["resume_last"]), "%s %s-%s" % (last["cwd"], shown(last["first"]), shown(last["last"])))
        buttons += (button_link(deep_link(group["path"]), icon("plus") + e(labels["new_session"]), group["path"], ghost=True)
                    + copy_button(terminal_command(group["path"]), labels["copy_terminal"], labels)
                    + copy_button(group["path"], labels["copy_path"], labels))
    return ('<section class="card head scope">%s<div class="top">%s<div class="grow"><h2>%s</h2><div class="path">%s</div></div><div class="buttons">%s</div></div>'
            '<div class="chips">%s</div></section>'
            % (eyebrow("repo", labels["level_repository"]), tile(group), e(name_of(group)), e(group["path"]), buttons, "".join(chips)))


def repository_panel(group, slug, report, labels, span):
    sessions = by_time(group)
    pairs = [(item, group, session, number) for session in sessions for number, item in enumerate(body_of(report, session).get("open", []))]
    running = sum(1 for session in sessions if session.get("running"))
    return (trail(("", e(labels["all_open"])), ("r-" + slug, tile(group) + e(name_of(group)))),
            repository_head(group, labels, running)
            + timeline([(group, slug)], span, labels, e(fill(labels["session_count"], n=len(sessions), repository=name_of(group))), report)
            + lanes(pairs, [(group, session) for session in sessions], report, labels, True))


def session_panel(group, slug, session, report, labels, span):
    sessions = by_time(group)
    body = body_of(report, session)
    usage = tokens(session["usage"].values())
    if session.get("running"):
        actions = '<span class="chip live big">%s%s</span>' % ('<i class="dot"></i>', e(labels["cannot_resume"]))
    elif session.get("cwd"):
        actions = (button_link(deep_link(session["cwd"], "/resume " + session["id"]), icon("play") + e(labels["resume"]), session["cwd"])
                   + copy_button(resume_command(session["cwd"], session["id"]), labels["copy_resume"], labels))
    else:
        actions = ""
    head = ('<section class="card head scope">%s<div class="top"><div class="grow"><h2>%s–%s <span class="span">%s</span></h2>'
            '<div class="path">%s %s · %s %s · %s</div></div><div class="buttons">%s</div></div><div class="topics">%s</div></section>'
            % (eyebrow("clock", "%s · %s" % (labels["level_session"], fill(labels["session_position"], n=sessions.index(session) + 1, total=len(sessions)))),
               stamp_html(session["first"]), stamp_html(session["last"]), e(length(session, labels)), compact(usage["charged"]), e(labels["tokens"]),
               compact(usage["output"]), e(labels["output"]), e(session["id"]), actions, "".join("<span>%s</span>" % e(topic) for topic in body["topics"])))
    pairs = [(item, group, session, number) for number, item in enumerate(body.get("open", []))]
    return (trail(("", e(labels["all_open"])), ("r-" + slug, tile(group) + e(name_of(group))), ("s-" + session["id"], session_name(report, session))),
            head
            + timeline([(group, slug)], span, labels, e(fill(labels["session_count"], n=len(sessions), repository=name_of(group))), report, selected=session["id"])
            + lanes(pairs, [(group, session)], report, labels, False))


def sidebar(digest, report, labels, groups):
    total = sum(open_count(group, report) for group, _ in groups)
    rows = ['<button type="button" class="nav" data-go="" data-level="day">%s<span class="nm">%s</span><span class="badge">%s</span></button>'
            % (icon("list"), e(labels["all_open"]), badge(total))]
    for group, slug in groups:
        dot = '<i class="dot" title="%s" aria-label="%s"></i>' % (e(labels["running"], quote=True), e(labels["running"], quote=True)) if any(session.get("running") for session in group["sessions"]) else ""
        rows.append('<button type="button" class="nav" data-go="r-%s" data-repo="%s">%s<span class="nm">%s</span>%s<span class="badge">%s</span></button>'
                    % (e(slug, quote=True), e(slug, quote=True), tile(group), e(name_of(group)), dot, badge(open_count(group, report))))
        children = []
        sessions = list(reversed(by_time(group)))
        # an even share per page, so 26 sessions read 7, 7, 7, 5 rather than 8, 8, 8, 2
        pages = max((len(sessions) + SIDE_PAGE - 1) // SIDE_PAGE, 1)
        size = (len(sessions) + pages - 1) // pages
        for number, session in enumerate(sessions):
            count = len(body_of(report, session).get("open", []))
            running = '<i class="dot" title="%s" aria-label="%s"></i>' % (e(labels["running"], quote=True), e(labels["running"], quote=True)) if session.get("running") else ""
            # the panel cuts the name to fit, so hovering shows it whole
            children.append('<button type="button" class="nav child" data-go="s-%s" data-p="%d"%s title="%s">%s%s%s</button>'
                            % (e(session["id"], quote=True), number // size, " hidden" if number >= size else "",
                               e("%s %s" % (shown(session["first"]), body_of(report, session)["topics"][0]), quote=True),
                               '<span class="nm">%s</span>' % session_name(report, session),
                               running, '<span class="badge small">%s</span>' % badge(count) if count else ""))
        steps = ("", "")
        if pages > 1:
            # the side panel is narrow, so turning the page costs a row where the next session would be, not a line of controls
            steps = ('<button type="button" class="step-row" data-sstep="-1" hidden>%s<span></span></button>' % icon("up"),
                     '<button type="button" class="step-row" data-sstep="1">%s<span>%s</span></button>' % (icon("down"), e(fill(labels["older"], n=len(sessions) - size))))
        rows.append('<div class="children" data-children="%s" data-pages="%d" data-size="%d" data-total="%d" data-newer="%s" data-older="%s" hidden>%s%s%s</div>'
                    % (e(slug, quote=True), pages, size, len(sessions), e(labels["newer"], quote=True), e(labels["older"], quote=True),
                       steps[0], "".join(children), steps[1]))
    return ('<aside class="card side"><div class="day">%s</div><div class="asof">%s %s</div>%s<hr><div class="sec">%s</div>%s</aside>'
            % (e(span_text(digest)), e(labels["as_of"]), e(readable(digest.get("generated"))), rows[0], e(labels["repository_list"]), "".join(rows[1:])))


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
    "plus": "<path d='M5 12h14'/><path d='M12 5v14'/>",
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
    "down": "<path d='m6 9 6 6 6-6'/>",
    "maximise": "<path d='M15 3h6v6'/><path d='m21 3-7 7'/><path d='m3 21 7-7'/><path d='M9 21H3v-6'/>",
    "restore": "<path d='m14 10 7-7'/><path d='M20 10h-6V4'/><path d='m3 21 7-7'/><path d='M4 14h6v6'/>",
    "mark": "<path d='M12 6v6l1.5.8'/><path d='M12.338 21.994a10 10 0 1 1 9.587-8.767'/><path d='M14 18h8'/><path d='m18 22-4-4 4-4'/>",
}


def button_link(href, text_html, hint, ghost=False):
    return '<a class="btn%s" href="%s" title="%s">%s</a>' % (" ghost" if ghost else "", e(href, quote=True), e(hint, quote=True), text_html)


def copy_button(command, name, labels):
    """A button that only copies: the copy icon and its words say so, and a quieter look keeps it apart from the links that open something."""
    return ('<button type="button" class="btn quiet copy" data-copy="%s" data-done="%s" title="%s">%s%s</button>'
            % (e(command, quote=True), e(labels["copied"], quote=True), e(command, quote=True), icon("copy"), e(name)))


STYLE = """
:root { --ink:#2b2620; --soft:#5e5549; --faint:#5e5549; --line:rgba(70,45,20,.11); --accent:#24599a; --on-accent:#ffffff;
  --card:rgba(255,252,245,.45); --card-line:rgba(255,255,255,.95); --sheen:rgba(255,255,255,.70); --solid:#ffffff;
  --bar:rgba(110,85,55,.22); --openbar:#4a3f33; --on-openbar:#ffffff; --code-bg:rgba(140,95,40,.10);
  --decision:#7a3fb0; --decision-bg:#f0e7fa; --action:#0f6e66; --action-bg:#e0f2ef; --question:#8a4c04; --question-bg:#f7e0bd;
  --done:#1f7a4d; --warn:#a3324f; --warn-bg:#fbe6ea; --live:#1f7a4d;
  --mix-read:#a9c0dc; --mix-write:#5b8fd0; --mix-fresh:#24599a; --mix-out:#d08a2f;
  --lv-day:hsl(28 25% 32%); --lv-repo:hsl(84 75% 28%); --lv-session:hsl(315 60% 42%);
  --bg:linear-gradient(135deg, hsl(42 75% 88%) 0%, hsl(36 60% 95%) 50%, hsl(22 65% 88%) 100%); --bg-base:hsl(38 55% 93%);
  --shadow:inset 0 1px 0 rgba(255,255,255,.95), 0 1px 2px rgba(80,50,20,.08), 0 14px 34px rgba(80,50,20,.15);
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
.nav.on { color:var(--lv); background:color-mix(in srgb, var(--lv) 15%, transparent); box-shadow:inset -3px 0 0 var(--lv) }
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
/* the name and path keep a column of their own, so the buttons move below them rather than squeezing a long name into pieces */
.head .grow { flex:1 1 360px; min-width:0 }
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
/* a button that only copies stays quiet, so it never reads as one of the links that open a session */
.btn.quiet { background:none; border-color:transparent; color:var(--soft); font-weight:400; padding-inline:8px }
.btn.quiet:hover { color:var(--ink); background:color-mix(in srgb, var(--ink) 7%, transparent); filter:none }
.timeline { padding:16px 18px }
.timeline h2 { margin:0 0 6px; font-size:17px } .timeline h2 span { color:var(--soft); font-weight:500; font-size:14px }
.grid { display:grid; grid-template-columns:minmax(120px, max-content) minmax(0, 1fr); align-items:stretch }
.tname .nm { max-width:240px }
.ticks { position:relative; height:18px }
.ticks span { position:absolute; transform:translateX(-50%); font-size:11px; color:var(--faint); font-variant-numeric:tabular-nums }
.ticks.days { height:30px }
.ticks .dtick { text-align:center; line-height:1.2; white-space:nowrap }
.ticks .dtick.mon { color:var(--ink); font-weight:600 }
.track .dayline { position:absolute; top:0; bottom:0; border-left:1px solid var(--line) }
.track .dayline.mon { border-left-color:color-mix(in srgb, var(--ink) 22%, transparent) }
.track .gap { position:absolute; top:0; bottom:0; background:repeating-linear-gradient(135deg, transparent 0 4px, color-mix(in srgb, var(--ink) 7%, transparent) 4px 5px) }
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
.decision .kind, .kind.decision { background:var(--decision-bg); color:var(--decision) }
.action .kind, .kind.action { background:var(--action-bg); color:var(--action) }
.question .kind, .kind.question { background:var(--question-bg); color:var(--question) }
.twin .kind { background:transparent; border:1px solid currentColor }
.twin .t { font-weight:400; color:var(--soft) }
.tile.emoji { background:color-mix(in srgb, var(--accent) 10%, transparent); font-weight:400 }
.folded > summary { cursor:pointer; list-style:none; margin:4px 0 0 }
.folded > summary::-webkit-details-marker { display:none }
.folded > summary .i { transition:transform .15s } .folded[open] > summary .i { transform:rotate(90deg) }
.folded > summary .counts { display:inline-flex; gap:6px; letter-spacing:0; text-transform:none; font-weight:600 }
.folded > summary:hover { color:var(--ink) }
.folded[open] > .cards { margin-top:22px }
.items { margin:0; padding:0; list-style:none; display:grid; gap:12px }
.items li { border-top:1px solid var(--line); padding-top:12px; min-width:0 }
.items li:first-child { border-top:0; padding-top:0 }
.t { font-weight:600 }
.d { color:var(--soft); font-size:13.5px; margin-top:2px }
.clamp .d { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden }
.where { display:flex; gap:6px; align-items:center; color:var(--faint); font-size:12px; margin-top:4px }
.where .date { font-variant-numeric:tabular-nums } .where .sep { opacity:.6 }
.group { display:grid; gap:8px }
/* a repository's block is set off from the next more strongly than one item from another, without a colour of its own */
.group + .group { margin-top:8px; padding-top:16px; border-top:2px solid color-mix(in srgb, var(--ink) 26%, transparent) }
.group .items li { border-top-color:color-mix(in srgb, var(--ink) 6%, transparent) }
.ghead { display:flex; gap:8px; align-items:center; font-size:13px; font-weight:600; color:var(--soft); min-width:0 }
.ghead .tile { width:20px; height:20px; font-size:11px; border-radius:6px } .ghead .nm { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.ghead b { margin-left:auto; font-size:12px; font-variant-numeric:tabular-nums }
.more-link { justify-self:start; font-size:12.5px; padding:2px 6px; margin-left:-6px; color:var(--accent) }
.step-row { display:flex; gap:6px; align-items:center; width:100%; padding:4px 10px; border:0; border-radius:8px; background:none; color:var(--accent);
  font-size:12.5px; text-align:left; cursor:pointer }
.step-row .i { width:14px; height:14px }
.kindhead .maxbtn { border:0; background:none; color:var(--soft); padding:4px; border-radius:6px; cursor:pointer; display:inline-grid }
.kindhead .maxbtn.grow { margin-left:auto }
.kindhead .maxbtn .i { width:15px; height:15px }
.kindhead .maxbtn:hover { color:var(--ink); background:color-mix(in srgb, var(--ink) 8%, transparent) }
.maxbtn.shrink, .max-on .maxbtn.grow { display:none } .max-on .maxbtn.shrink { display:inline-grid; margin-left:auto }
/* a maximised card takes its row alone and shows every item in columns, its repositories or its days kept whole */
.cards.maxed { grid-template-columns:minmax(0, 1fr) } .cards.maxed > .col:not(.max-on) { display:none }
.max-on .items, .max-on .groups { display:block; column-width:380px; column-gap:34px }
.max-on .items li { break-inside:avoid; margin-bottom:12px }
.max-on .groups .items { column-width:auto; columns:auto } .max-on .group { break-inside:avoid; display:block; margin-bottom:18px }
/* in columns the line goes under a group, since one above would also head the next column */
.max-on .group + .group { margin-top:0; padding-top:0; border-top:0 }
.max-on .group:not(:last-child) { padding-bottom:16px; border-bottom:2px solid color-mix(in srgb, var(--ink) 26%, transparent) }
.group li.beyond { display:none } .max-on .group li.beyond { display:block }
.max-on .more-link, .max-on .show-all { display:none }
.max-on.clamp .d { display:block; -webkit-line-clamp:unset; overflow:visible }
.items li.dayhead { font-size:12px; font-weight:700; color:var(--faint); border-top:0; padding:6px 0 4px; break-after:avoid; cursor:default }
.max-on .items li.dayhead { border-bottom:1px solid var(--line); margin-bottom:8px }
.tabs { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 -6px }
.tab { display:inline-flex; gap:8px; align-items:center; font-size:13px; padding:5px 10px; border-radius:9px; border:1px solid var(--line); background:var(--card); cursor:pointer; color:var(--soft) }
.tab.on { color:var(--ink); border-color:color-mix(in srgb, var(--ink) 40%, transparent); font-weight:600 }
.ticks .dtick i { display:block; font-style:normal; font-size:10px; opacity:.75 }
.empty { padding:12px 18px; color:var(--soft); display:flex; gap:8px; align-items:center; border-style:dashed }
.empty .i { color:var(--done) }
.show-all { justify-self:start }
/* hover only says that something can be clicked: the outline and the level colour belong to the selection */
.step-row:hover, .nav:not(.on):hover, .tname:hover, .items li[data-go]:hover { background:color-mix(in srgb, var(--ink) 7%, transparent) }
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
  // dates are written as YYYY-MM-DD and rewritten here in the page's language, built from their parts so no time zone moves the day
  function localise(scope) {
    var day, weekday, number;
    try {
      day = new Intl.DateTimeFormat(root.lang, { month: 'numeric', day: 'numeric' });
      weekday = new Intl.DateTimeFormat(root.lang, { weekday: 'short' });
      number = new Intl.DateTimeFormat(root.lang, { day: 'numeric' });
    } catch (ignored) { return; }
    var ticks = document.querySelectorAll('.ticks .dtick time');
    scope.querySelectorAll('time[datetime]').forEach(function (t) {
      var m = /^(\\d{4})-(\\d{2})-(\\d{2})(?:T(\\d{2}:\\d{2}))?$/.exec(t.getAttribute('datetime'));
      if (!m) { return; }
      var date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
      var tick = t.closest('.dtick');
      if (tick) {
        // a month's columns are narrow, so only a week's first day and the first column carry the month
        var full = ticks.length <= 12 || tick.classList.contains('mon') || t === ticks[0];
        t.textContent = (full ? day : number).format(date);
        var name = document.createElement('i'); name.textContent = weekday.format(date); t.appendChild(name);
      } else {
        t.textContent = day.format(date) + (m[4] ? ' ' + m[4] : '');
      }
    });
  }
  localise(document);
  // one card of a row takes the row, with a tab for each card of it; the day headings a range's items get are made here and removed on restore
  function maximise(panel, kind) {
    panel.querySelectorAll('.cards').forEach(function (box) {
      var target = kind ? [].filter.call(box.children, function (c) { return c.getAttribute('data-kind') === kind; })[0] : null;
      box.classList.toggle('maxed', !!target);
      var tabs = box.previousElementSibling;
      if (tabs && tabs.classList.contains('tabs')) { tabs.remove(); }
      [].forEach.call(box.children, function (col) {
        var on = col === target;
        col.classList.toggle('max-on', on);
        col.querySelectorAll('li.more').forEach(function (li) { li.hidden = !on; });
        col.querySelectorAll('li.dayhead').forEach(function (li) { li.remove(); });
        if (on && !col.querySelector('.groups')) {
          var last = null;
          col.querySelectorAll('.items > li[data-day]').forEach(function (li) {
            var d = li.getAttribute('data-day');
            if (d === last) { return; }
            last = d;
            var head = document.createElement('li'); head.className = 'dayhead'; head.setAttribute('aria-hidden', 'true');
            var t = document.createElement('time'); t.setAttribute('datetime', d); t.textContent = d; head.appendChild(t);
            li.parentNode.insertBefore(head, li); localise(head);
          });
        }
      });
      if (target) {
        var row = document.createElement('div'); row.className = 'tabs';
        [].forEach.call(box.children, function (col) {
          if (!col.getAttribute('data-kind')) { return; }
          var tab = document.createElement('button'); tab.type = 'button'; tab.className = 'tab' + (col === target ? ' on' : '');
          tab.setAttribute('data-max-kind', col.getAttribute('data-kind'));
          tab.innerHTML = col.querySelector('.kindhead .kind').outerHTML + ' ' + col.getAttribute('data-count');
          row.appendChild(tab);
        });
        box.parentNode.insertBefore(row, box);
      }
    });
  }
  function sidePage(box, page) {
    var pages = Number(box.getAttribute('data-pages'));
    if (!(pages > 1)) { return; }
    var size = Number(box.getAttribute('data-size')), total = Number(box.getAttribute('data-total'));
    page = Math.min(Math.max(page, 0), pages - 1); box.setAttribute('data-page', page);
    box.querySelectorAll('.nav.child').forEach(function (n) { n.hidden = Number(n.getAttribute('data-p')) !== page; });
    var up = box.querySelector('[data-sstep="-1"]'), down = box.querySelector('[data-sstep="1"]');
    up.hidden = page === 0; up.querySelector('span').textContent = box.getAttribute('data-newer').replace('{n}', page * size);
    down.hidden = page === pages - 1; down.querySelector('span').textContent = box.getAttribute('data-older').replace('{n}', Math.max(total - (page + 1) * size, 0));
  }
  var shownKey = null;
  function route() {
    var parts = decodeURIComponent(location.hash.replace(/^#/, '')).split('~'), key = parts[0], kind = parts[1] || '';
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
    document.querySelectorAll('[data-children]').forEach(function (c) {
      c.hidden = c.getAttribute('data-children') !== repo;
      // the line to the header starts at the selected session, so its page is the one shown
      var on = c.querySelector('.nav.child.on'); sidePage(c, on ? Number(on.getAttribute('data-p')) : 0);
    });
    document.title = panel.getAttribute('data-title');
    maximise(panel, kind);
    var moved = shownKey !== panel.getAttribute('data-key');
    shownKey = panel.getAttribute('data-key');
    requestAnimationFrame(draw);
    return moved;
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
    var x1 = rn.right, y1 = rn.top + rn.height / 2, x2 = rh.left, y2 = rh.top + Math.min(rh.height / 2, 44), mx = Math.round((x1 + x2) / 2);
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
        var axis = panel.querySelector('.ticks'), ticks = axis.getBoundingClientRect(), hours = panel.querySelectorAll('.ticks span').length - 1, left = null, right = null;
        // a range's columns differ in width, so the line drops down the edges between days rather than at half hours
        var cuts = axis.getAttribute('data-cuts');
        var candidates = cuts ? cuts.split(' ').map(function (p) { return Math.round(ticks.left + Number(p) * ticks.width / 100); })
          : Array.apply(null, Array(Math.max(hours, 0))).map(function (_, k) { return Math.round(ticks.left + (k + 0.5) * ticks.width / hours); });
        candidates.forEach(function (cx) {
          if (cx < rb.left - 6 && clear(cx)) { left = cx; }
          if (cx > rb.right + 6 && right === null && clear(cx)) { right = cx; }
        });
        var mid = Math.round(rb.top + rb.height / 2), x;
        if (left !== null) {
          x = left; paths.push('M' + x + ' ' + top + ' V' + mid + ' H' + Math.round(rb.left - 4));
        } else if (right !== null) {
          // a bar near the start of the day has the title over every half hour left of it, so the line comes in from the right instead
          x = right; paths.push('M' + x + ' ' + top + ' V' + mid + ' H' + Math.round(rb.right + 4));
        } else {
          x = Math.round(rb.left + rb.width / 2); paths.push('M' + x + ' ' + top + ' V' + (rb.top - 4));
        }
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
  // the item a click came from is marked only once its page is shown and scrolled to the top, or the reset would scroll it away again
  var pending = null;
  function arrive() { window.scrollTo(0, 0); if (pending) { point(pending); pending = null; } }
  // maximising or restoring a card changes the anchor too, and keeps the reader where they are
  window.addEventListener('hashchange', function () { if (route() || pending) { arrive(); } });
  route();
  function point(anchor) {
    var item = anchor && document.getElementById(anchor);
    if (!item) { return; }
    // an item past a card's first ones is folded away, so its card is maximised in place before the flash, without a second navigation
    if (item.hidden) {
      var col = item.closest('.col'), panel = item.closest('.panel');
      history.replaceState(null, '', '#' + panel.getAttribute('data-key') + '~' + col.getAttribute('data-kind'));
      maximise(panel, col.getAttribute('data-kind'));
    }
    item.classList.remove('flash'); void item.offsetWidth; item.classList.add('flash');
    item.scrollIntoView({ block: 'center' });
  }
  function go(key, anchor) {
    pending = anchor || null;
    if (location.hash.replace(/^#/, '') === key) { arrive(); return; }
    if (key) { location.hash = key; } else { history.pushState(null, '', location.pathname + location.search); route(); arrive(); }
  }
  window.addEventListener('popstate', route);
  document.addEventListener('keydown', function (event) {
    var item = event.target.closest('li[data-go]');
    if (item && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); go(item.getAttribute('data-go'), item.getAttribute('data-item')); }
  });
  document.addEventListener('click', function (event) {
    var set = event.target.closest('[data-theme-set]');
    if (set) { root.setAttribute('data-theme', set.getAttribute('data-theme-set')); mark(); try { localStorage.setItem('remind-me-theme', current()); } catch (ignored) { } return; }
    var step = event.target.closest('[data-sstep]');
    if (step) { var box = step.closest('[data-children]'); sidePage(box, Number(box.getAttribute('data-page') || 0) + Number(step.getAttribute('data-sstep'))); return; }
    var grow = event.target.closest('[data-max-kind]'), shrink = event.target.closest('[data-max-restore]');
    if (grow || shrink) {
      var here = document.querySelector('.panel:not([hidden])').getAttribute('data-key');
      location.hash = grow ? here + '~' + grow.getAttribute('data-max-kind') : (here || '~');
      return;
    }
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
    if (target) { go(target.getAttribute('data-go'), target.getAttribute('data-item')); }
  });
})();
"""


def page(report, digest):
    labels = report["labels"]
    names = slugs(digest["repositories"])
    page_order = ordered(digest, report)
    chosen, _ = marks(report, page_order)
    # a copy of each repository carries its mark, so every place that draws it shows the same one
    groups = [(dict(group, mark=chosen.get(group["path"])), names[group["path"]]) for group in page_order]
    sessions = all_sessions(digest)
    VIEW["range"] = not single(digest)
    VIEW["axis"] = day_axis(digest) if VIEW["range"] else None
    span = ((min(clock(session["first"]) for session in sessions) // 60) * 60, (max(clock(session["last"]) for session in sessions) // 60 + 1) * 60)
    title = "remind-me %s" % span_text(digest)
    built = [("", "day", "", title, overview_panel(digest, report, labels, groups, span))]
    for group, slug in groups:
        built.append(("r-" + slug, "repo", slug, "%s · %s" % (name_of(group), title), repository_panel(group, slug, report, labels, span)))
        for session in by_time(group):
            built.append(("s-" + session["id"], "session", slug, "%s %s · %s" % (name_of(group), shown(session["first"]), title),
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
    VIEW["range"] = not single(digest)
    lines = ["**%s**" % report["headline"], "",
             "%s · %d %s · %d %s · %s %s" % (span_text(digest), numbers["sessions"], labels["sessions"], numbers["repositories"],
                                           labels["repositories"], compact(numbers["tokens"]["charged"]), labels["tokens"])]
    for group in ordered(digest, report):
        if VIEW["range"]:
            # a month lists over a hundred sessions, so a range prints only what is open, each repository's latest few, and the page holds the rest
            pairs = newest_first([(item, group, session, number) for session in group["sessions"]
                                  for number, item in enumerate(body_of(report, session).get("open", []))])
            if not pairs:
                continue
            lines += ["", "### %s" % name_of(group)]
            lines += ["- %s: %s (%s)" % (labels[item["kind"]], item["text"], day_of(session["last"])[5:]) for item, _, session, _ in pairs[:PER_REPOSITORY]]
            if len(pairs) > PER_REPOSITORY:
                lines.append("- %s" % fill(labels["more_in_repository"], n=len(pairs) - PER_REPOSITORY, repository=name_of(group)))
            continue
        lines += ["", "### %s" % name_of(group)]
        for session in group["sessions"]:
            body = body_of(report, session)
            running = " (%s)" % labels["running"] if session.get("running") else ""
            lines.append("- **%s–%s**%s %s" % (shown(session["first"]), shown(session["last"]), running, " / ".join(body["topics"])))
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
    parser.add_argument("--fragments", nargs="+", default=[], help="files holding only sessions, folded into the report before it is checked")
    args = parser.parse_args(argv)
    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)
    with open(args.digest, encoding="utf-8") as handle:
        digest = json.load(handle)
    fragments, unread = [], []
    for path in args.fragments:
        # a reader that wrote broken JSON, or nothing, is sent again like any other fragment problem, so it must not stop the run
        try:
            # a file written with a byte order mark reads the same, since a reader sent again would write it the same way
            with open(path, encoding="utf-8-sig") as handle:
                fragments.append((os.path.basename(path), json.load(handle)))
        except (OSError, ValueError) as error:
            unread.append("fragment %s could not be read: %s" % (os.path.basename(path), error))
    report, problems = merged(report, fragments)
    problems = unread + problems
    problems += located(problems_in(report, digest), fragments)
    sys.stdout.reconfigure(encoding="utf-8")
    if problems:
        print("the report does not match the schema, so nothing was written:")
        for problem in problems:
            print("- " + problem)
        return EXIT_SCHEMA
    if fragments:
        # the fragments are deleted with the digest, so the report kept on disk has to hold the sessions itself
        with open(args.report, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(page(report, digest))
    _, ignored = marks(report, ordered(digest, report))
    if ignored:
        sys.stderr.write("marks ignored, initials used instead: %s\n" % "; ".join(ignored))
    print(summary(report, digest, args.out))
    if args.open:
        webbrowser.open(pathlib.Path(args.out).resolve().as_uri())
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
