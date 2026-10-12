"""Collect a day, or a range of days, of Claude Code sessions into a digest the model reads.

The transcripts of a working day run to tens of megabytes, so the model never reads them.
This script keeps only what a summary needs: who asked what and when, the questions a session left,
what it pushed or opened, and the live state of each repository and pull request it named.
A transcript says what was pending when the session stopped, and the live state says whether it still is,
because a pull request a session left in review is often merged the same afternoon.

Usage:
    python collect.py [--from YYYY-MM-DD [--to YYYY-MM-DD]] [--out digest.json] [--no-live]

Without --from, the range is the one most recent day before today with a prompt typed in an interactive session,
so a Monday run reports the Friday before. Without --to, the range ends today.
A range holds at most 31 days, both ends included.
Exit status 3 means no session was active in the range.
"""

import argparse
import concurrent.futures
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

EXIT_OK = 0
EXIT_NO_SESSIONS = 3
# a month of sessions is the most one report is built to hold, and Claude Code keeps 30 days of transcripts by default
MAX_DAYS = 31

PROMPT_LIMIT = 600
SUMMARY_HEAD = 1000
SUMMARY_TAIL = 3000
QUESTION_TAIL = 700
LAST_TAIL = 1500
LIVE_TIMEOUT = 60

COMPACTION_PREFIX = "This session is being continued from a previous conversation"
NOISE_PREFIXES = ("<local-command", "<task-notification>", "<bash-input>", "<bash-stdout>", "<bash-stderr>")
# Claude Code writes an interruption as a user entry holding only this marker,
# and whatever the person types next is an entry of its own
INTERRUPT_MARKERS = ("[Request interrupted by user]", "[Request interrupted by user for tool use]")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_NAME = re.compile(r"<command-name>\s*(/?[\w:.-]+)\s*</command-name>")
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)

# a question mark near the end is the one marker that holds across languages,
# and the phrases catch the decision lists that end in a full stop
QUESTION_MARKS = ("?", "？")
DECISION_PHRASES = ("your call", "up to you", "do you want", "shall i", "should i", "want me to",
                    "由你決定", "請你決定", "要不要", "要我")

# Azure DevOps numbers its pull requests across a whole collection, so a bare id is enough to look one up,
# while a GitHub number means nothing without its repository, so only a full URL counts
AZURE_PR = (
    re.compile(r"pullrequest/(\d+)"),
    re.compile(r"(?<![\w/&])!(\d{3,7})\b"),
    re.compile(r'"pullRequestId":\s*(\d+)'),
    re.compile(r"\baz repos pr \w+[^\n]*?--id[ =](\d+)"),
)
GITHUB_PR = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
AZURE_REMOTE = (
    ("https://dev.azure.com/%s", re.compile(r"https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/")),
    ("https://dev.azure.com/%s", re.compile(r"ssh\.dev\.azure\.com:v3/([^/]+)/")),
    ("https://%s.visualstudio.com", re.compile(r"https://(?:[^@/]+@)?([\w-]+)\.visualstudio\.com/")),
)
LOOKUP_WORKERS = 8
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
SHELL_TOOLS = ("Bash", "PowerShell")


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def local_time(timestamp):
    """Return the local (day, HH:MM) of an ISO timestamp, or None when it does not parse."""
    try:
        moment = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return None
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M")


def transcripts(root):
    """Main-session transcripts only: a subagent's lives under <session>/subagents/ and has no user of its own."""
    return sorted(glob.glob(os.path.join(root, "*", "*.jsonl")))


def days_in(path):
    """The local days a transcript has entries on, read without parsing every line."""
    found = set()
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            start = line.find('"timestamp":"')
            if start < 0:
                continue
            when = local_time(line[start + 13:start + 13 + 32].split('"', 1)[0])
            if when:
                found.add(when[0])
    return found


def work_days(path):
    """The local days a transcript holds a typed prompt in an interactive session: the days a report keeps."""
    found = set()
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"type":"user"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("entrypoint") == "sdk-cli":
                return set()
            when = local_time(entry.get("timestamp", ""))
            prompt = typed_prompt(entry)
            if when and prompt and not invokes_remind_me(prompt[1]):
                found.add(when[0])
    return found


def default_day(paths, today):
    # a day holding only `claude -p` runs or a run of this skill would report nothing, while the day before has work
    earlier = set()
    for path in paths:
        earlier.update(day for day in work_days(path) if day < today)
    return max(earlier) if earlier else None


def blocks(message):
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [block for block in content or [] if isinstance(block, dict)]


def plain(text):
    return SYSTEM_REMINDER.sub("", text or "").strip()


def typed_prompt(entry):
    """What the person typed, as (kind, text), or None for tool results, hook output and command noise."""
    if entry.get("isMeta") or entry.get("isSidechain"):
        return None
    parts = blocks(entry.get("message") or {})
    if any(block.get("type") == "tool_result" for block in parts):
        return None
    text = plain("\n".join(block.get("text", "") for block in parts if block.get("type") == "text"))
    if not text or text.startswith(NOISE_PREFIXES) or text in INTERRUPT_MARKERS:
        return None
    if text.startswith(COMPACTION_PREFIX):
        # a compaction summary ends with what was still pending and the next step, which is what this report is for
        if len(text) > SUMMARY_HEAD + SUMMARY_TAIL:
            text = text[:SUMMARY_HEAD] + "\n[...]\n" + text[-SUMMARY_TAIL:]
        return "summary", text
    command = COMMAND_NAME.search(text)
    if command:
        args = COMMAND_ARGS.search(text)
        name = command.group(1) if command.group(1).startswith("/") else "/" + command.group(1)
        return "command", (name + " " + (args.group(1).strip() if args else "")).strip()[:PROMPT_LIMIT]
    return "prompt", text[:PROMPT_LIMIT]


def invokes_remind_me(text):
    """The request that runs this skill is not part of the day it reports on."""
    head = text.lstrip()[:80]
    return head.startswith("/remind-me") or "remind-me:remind-me" in head


def is_question(text):
    tail = text.rstrip()[-300:]
    if any(mark in tail for mark in QUESTION_MARKS):
        return True
    lowered = tail.lower()
    return any(phrase in lowered for phrase in DECISION_PHRASES)


def pull_requests(text):
    """Pull request keys a text names: azure:<id> or github:<owner>/<repo>#<number>."""
    found = set()
    for pattern in AZURE_PR:
        found.update("azure:%d" % int(match) for match in pattern.findall(text or ""))
    found.update("github:%s#%d" % (repo, int(number)) for repo, number in GITHUB_PR.findall(text or ""))
    return found


def add_usage(totals, seen, entry):
    """Add one reply's token usage once: a reply split over several transcript lines repeats the same usage."""
    message = entry.get("message") or {}
    usage = message.get("usage")
    key = message.get("id") or entry.get("requestId")
    if not usage or key in seen:
        return
    seen.add(key)
    model = message.get("model") or "unknown"
    bucket = totals.setdefault(model, dict.fromkeys(USAGE_FIELDS, 0))
    for field in USAGE_FIELDS:
        bucket[field] += usage.get(field) or 0


def moment(timestamp):
    """An ISO timestamp as an aware datetime, or None when it does not parse."""
    try:
        moment = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # a timestamp without an offset is read as local, the way local_time reads it, so two of them always compare
    return moment if moment.tzinfo else moment.astimezone()


def in_runs(when, runs):
    return bool(when) and any(start <= when and (end is None or when < end) for start, end in runs)


def subagent_usage(path, days, totals, runs=()):
    """Subagents spend on the session's behalf, so their usage counts toward it, except while this skill was running."""
    folder = os.path.join(path[:-len(".jsonl")], "subagents")
    for sub_path in glob.glob(os.path.join(folder, "*.jsonl")):
        seen = set()
        with open(sub_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"usage"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                when = local_time(entry.get("timestamp", ""))
                # the readers this skill sends out are part of its run, not work on the days it reports
                if when and when[0] in days and not in_runs(moment(entry.get("timestamp", "")), runs):
                    add_usage(totals, seen, entry)


def result_text(content):
    """A tool result as text, its text blocks as written: serialising them would escape the quotes a pattern looks for."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") if isinstance(part, dict) and part.get("type") == "text"
                         else json.dumps(part, ensure_ascii=False) for part in content)
    return json.dumps(content, ensure_ascii=False)


def read_session(path, days):
    """Everything the digest keeps from one transcript on a set of days, or None when it has nothing on them."""
    session = {
        "id": os.path.basename(path)[:-len(".jsonl")],
        "cwd": None,
        "entrypoint": None,
        "branches": [],
        "first": None,
        "last": None,
        "days": [],
        "prompts": [],
        "questions": [],
        "pull_requests": [],
        "pull_requests_new": [],
        "last_reply": None,
        "usage": {},
    }
    seen_usage = set()
    branches = []
    prs = set()
    replies = []
    in_run = False
    # each run of this skill as [start, end), end None while it is still going, so its subagents can be left out too
    runs = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not session["cwd"]:
                # the folder the session started in is where Claude Code keeps it and where a resume belongs,
                # while a later cwd only follows the shell wherever a command moved it
                session["cwd"] = entry.get("cwd")
            kind = entry.get("type")
            prompt = typed_prompt(entry) if kind == "user" else None
            # a run is followed on every day, so one started the evening before the range is still recognised inside it
            if prompt and invokes_remind_me(prompt[1]):
                if not in_run:
                    runs.append([moment(entry.get("timestamp", "")), None])
                in_run = True
                continue
            if in_run:
                # a run of this skill is not work on the days it reports: its replies would become the session's last reply,
                # and only the person typing again ends it, since a compaction summary is not the person
                if not (prompt and prompt[0] in ("prompt", "command")):
                    continue
                in_run = False
                runs[-1][1] = moment(entry.get("timestamp", ""))
            when = local_time(entry.get("timestamp", ""))
            if not when or when[0] not in days:
                continue
            clock = "%s %s" % when
            session["first"] = session["first"] or clock
            session["last"] = clock
            if when[0] not in session["days"]:
                session["days"].append(when[0])
            session["entrypoint"] = entry.get("entrypoint") or session["entrypoint"]
            branch = entry.get("gitBranch")
            if branch and branch != "HEAD" and branch not in branches:
                branches.append(branch)
            if kind == "user":
                if prompt:
                    session["prompts"].append({"at": clock, "kind": prompt[0], "text": prompt[1]})
                for block in blocks(entry.get("message") or {}):
                    if block.get("type") == "tool_result":
                        prs.update(pull_requests(result_text(block.get("content"))))
            elif kind == "assistant" and not entry.get("isSidechain"):
                add_usage(session["usage"], seen_usage, entry)
                for block in blocks(entry.get("message") or {}):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        text = block["text"].strip()
                        replies.append((clock, text))
                        prs.update(pull_requests(text))
                    elif block.get("type") == "tool_use" and block.get("name") in SHELL_TOOLS:
                        command = (block.get("input") or {}).get("command", "")
                        prs.update(pull_requests(command))
    if session["first"] is None:
        return None
    subagent_usage(path, days, session["usage"], [run for run in runs if run[0]])
    session["branches"] = branches
    session["pull_requests"] = sorted(prs)
    session["questions"] = [{"at": clock, "text": text[-QUESTION_TAIL:]} for clock, text in replies if is_question(text)]
    if replies:
        session["last_reply"] = {"at": replies[-1][0], "text": replies[-1][1][-LAST_TAIL:]}
    return session


def run(argv, cwd=None):
    """Run a command and return (ok, stdout or the reason it failed). Never raises."""
    executable = shutil.which(argv[0])
    if not executable:
        return False, "%s is not installed" % argv[0]
    try:
        result = subprocess.run([executable] + argv[1:], cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=LIVE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        return False, lines[-1] if lines else "exit %d" % result.returncode
    return True, result.stdout


def repository_root(cwd):
    """The main working tree a directory belongs to, so a worktree's sessions group with its repository."""
    if not cwd or not os.path.isdir(cwd):
        return None
    ok, out = run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if not ok:
        return None
    common = os.path.normpath(out.strip())
    return os.path.dirname(common) if os.path.basename(common) == ".git" else common


def azure_organisation(remote):
    """The organisation URL `az` needs, from a remote in any of the forms Azure DevOps hands out."""
    for template, pattern in AZURE_REMOTE:
        match = pattern.search(remote or "")
        if match:
            return template % match.group(1)
    return None


def pull_request_state(key, organisations):
    """The state of one pull request now, or status unknown with the reason it could not be read."""
    platform, _, ref = key.partition(":")
    if platform == "azure":
        if not organisations:
            return {"status": "unknown", "reason": "no Azure DevOps repository in the digest to name the organisation"}
        reason = "not found"
        for organisation in organisations:
            ok, out = run(["az", "repos", "pr", "show", "--id", ref, "--org", organisation, "-o", "json", "--query",
                           "{status:status,title:title,source:sourceRefName,target:targetRefName,created:creationDate,"
                           "closed:closedDate,author:createdBy.uniqueName,repository:repository.name}"])
            if ok:
                break
            reason = out
        else:
            return {"status": "unknown", "reason": reason}
    else:
        repo, _, number = ref.partition("#")
        ok, out = run(["gh", "pr", "view", number, "-R", repo, "--json", "state,title,headRefName,baseRefName,createdAt,closedAt,mergedAt,url,author"])
        if not ok:
            return {"status": "unknown", "reason": out}
    try:
        state = json.loads(out)
    except ValueError:
        return {"status": "unknown", "reason": "unreadable answer"}
    state["status"] = (state.pop("state", None) or state.get("status") or "unknown").lower()
    if "createdAt" in state:
        state["created"] = state.pop("createdAt")
        state["author"] = (state.get("author") or {}).get("login")
        state["source"] = state.pop("headRefName", None)
    if (state.get("source") or "").startswith("refs/heads/"):
        state["source"] = state["source"][len("refs/heads/"):]
    return state


def same_repository(key, state, remote):
    """Whether a pull request belongs to the repository behind a remote, by the name each platform gives it."""
    path = urllib.parse.unquote(re.sub(r"\.git$", "", (remote or "").rstrip("/"))).replace(":", "/").lower().split("/")
    if key.startswith("github:"):
        return "/".join(path[-2:]) == key[len("github:"):].partition("#")[0].lower()
    name = (state.get("repository") or "").lower()
    return bool(name) and path[-1] == name


def created_in(state, days):
    when = local_time(state.get("created") or "")
    return bool(when) and when[0] in days


def count(out):
    try:
        return int(out.strip())
    except ValueError:
        return None


def live_state(root, sessions):
    """What is true now in one repository: work not committed and commits not pushed."""
    state = {"uncommitted": None, "branches": [], "notes": []}
    ok, out = run(["git", "-C", root, "status", "--porcelain"])
    if ok:
        state["uncommitted"] = len([line for line in out.splitlines() if line.strip()])
    else:
        state["notes"].append("git status failed: " + out)
    ok, out = run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
    state["current_branch"] = out.strip() if ok else None
    branches = []
    for session in sessions:
        branches.extend(branch for branch in session["branches"] if branch not in branches)
    for branch in branches:
        entry = {"name": branch}
        exists, _ = run(["git", "-C", root, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch])
        entry["local"] = exists
        if exists:
            # remote-tracking refs are as fresh as the clone's last fetch: nothing here fetches
            remote, _ = run(["git", "-C", root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch])
            entry["on_remote"] = remote
            ok, out = run(["git", "-C", root, "rev-list", "--count", "refs/heads/" + branch, "--not", "--remotes"])
            entry["unpushed"] = count(out) if ok else None
        state["branches"].append(entry)
    ok, remote = run(["git", "-C", root, "remote", "get-url", "origin"])
    state["remote"] = re.sub(r"^(\w+://)[^/@]+@", r"\1", remote.strip()) if ok else None
    ok, email = run(["git", "-C", root, "config", "user.email"])
    state["email"] = email.strip() if ok and email.strip() else None
    return state


def identities(digest):
    """Who the person is on each platform: the accounts the CLIs are signed in as, and the repositories' git emails.

    The CLI account is the one that opens and reads the pull requests; a git email can differ from it,
    and on GitHub it is often a noreply address, so it only adds to the set.
    """
    found = {group["live"]["email"].lower() for group in digest["repositories"] if (group.get("live") or {}).get("email")}
    keys = digest.get("pull_requests", {})
    if any(key.startswith("azure:") for key in keys):
        ok, out = run(["az", "account", "show", "--query", "user.name", "-o", "tsv"])
        if ok and out.strip():
            found.add(out.strip().lower())
    if any(key.startswith("github:") for key in keys):
        ok, out = run(["gh", "api", "user", "--jq", ".login"])
        if ok and out.strip():
            found.add(out.strip().lower())
    return sorted(found)


def running_sessions():
    """Sessions with a live process: Claude Code keeps a file per running session and removes it on exit."""
    found = {}
    for path in glob.glob(os.path.join(config_dir(), "sessions", "*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if data.get("sessionId"):
            found[data["sessionId"]] = data.get("status") or "running"
    return found


def span_days(first, last):
    """Every local day from first to last, both included, as YYYY-MM-DD."""
    start, end = datetime.date.fromisoformat(first), datetime.date.fromisoformat(last)
    return {(start + datetime.timedelta(days=n)).isoformat() for n in range((end - start).days + 1)}


def collect(first=None, last=None, live=True, root=None, today=None):
    root = root or os.path.join(config_dir(), "projects")
    paths = transcripts(root)
    today = today or datetime.date.today().isoformat()
    if not first:
        first = last = default_day(paths, today)
    elif not last:
        last = today
    digest = {"from": first, "to": last, "earliest": None,
              "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "left_out": {"headless": 0}, "repositories": [], "pull_requests": {}}
    if not first:
        return digest
    days = span_days(first, last)
    running = running_sessions()
    groups = {}
    for path in paths:
        present = days_in(path)
        if present:
            # the oldest day any transcript still holds, which tells the skill whether the range starts before what was kept
            digest["earliest"] = min(filter(None, [digest["earliest"], min(present)]))
        if not present & days:
            continue
        session = read_session(path, days)
        if not session:
            continue
        if session["entrypoint"] == "sdk-cli":
            # `claude -p` runs are scripts and probes, not work a person left open
            digest["left_out"]["headless"] += 1
            continue
        if not session["prompts"]:
            continue
        # the entrypoint only decides what is left out, so it is not part of what the model reads
        del session["entrypoint"]
        session["running"] = running.get(session["id"])
        repository = repository_root(session["cwd"])
        key = repository or session["cwd"] or "(unknown folder)"
        group = groups.setdefault(key, {"path": key, "is_repository": bool(repository), "current": False, "sessions": []})
        group["sessions"].append(session)
    here = repository_root(os.getcwd()) or os.getcwd()
    if here in groups:
        # the repository this report is asked from: a resume there would land in the session already running
        groups[here]["current"] = True
    for key in sorted(groups):
        group = groups[key]
        group["sessions"].sort(key=lambda session: session["first"])
        if live and group["is_repository"]:
            group["live"] = live_state(key, group["sessions"])
        digest["repositories"].append(group)
    if live:
        organisations = sorted({azure_organisation(group.get("live", {}).get("remote")) for group in digest["repositories"]} - {None})
        keys = sorted({key for group in digest["repositories"] for session in group["sessions"] for key in session["pull_requests"]})
        # each lookup is a network round trip of a second or more, and one session can name dozens
        with concurrent.futures.ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
            states = list(pool.map(lambda key: pull_request_state(key, organisations), keys))
        digest["pull_requests"] = dict(zip(keys, states))
        digest["identities"] = identities(digest)
        merged = [(key, state) for key, state in digest["pull_requests"].items()
                  if state.get("source") and state.get("status") in ("completed", "merged")]
        for group in digest["repositories"]:
            remote = (group.get("live") or {}).get("remote")
            for branch in (group.get("live") or {}).get("branches", []):
                # a branch name alone says nothing across repositories: one ticket's branch can carry the same name in two of them
                key = next((key for key, state in merged if state["source"] == branch["name"] and same_repository(key, state, remote)), None)
                if key:
                    # a merged branch's commits reach no remote ref in a clone that has not fetched since,
                    # so they would read as unpushed work
                    branch["merged_by"] = key
                    branch["unpushed"] = None
        # a session also names old pull requests it labelled or compared against, so the new ones are those created in the range,
        # by the platform's own record, and by the person: a colleague's pull request named then is not the person's work.
        # one session can name another session's new pull request, so this is no claim of which session opened it
        mine = set(digest["identities"])
        for group in digest["repositories"]:
            for session in group["sessions"]:
                session["pull_requests_new"] = [
                    key for key in session["pull_requests"]
                    if created_in(digest["pull_requests"].get(key, {}), days)
                    and (not mine or (digest["pull_requests"][key].get("author") or "").lower() in mine)]
    return digest


def split(digest, folder):
    """One digest per repository, each with the whole digest's range and identities and only its own pull requests.

    A long range is read by one reader per repository, and an item is settled within its repository,
    so each reader needs everything about its repository and nothing about the others.
    Returns (file name, bytes) per repository, in the digest's order.
    """
    os.makedirs(folder, exist_ok=True)
    written, taken = [], set()
    for group in digest["repositories"]:
        base = re.sub(r"[^\w.-]+", "-", os.path.basename(group["path"].rstrip("\\/")).lower()).strip("-.") or "repository"
        name, number = base, 2
        while name in taken:
            name, number = "%s-%d" % (base, number), number + 1
        taken.add(name)
        keys = {key for session in group["sessions"] for key in session["pull_requests"]}
        part = dict(digest, repositories=[group], pull_requests={key: state for key, state in digest["pull_requests"].items() if key in keys})
        text = json.dumps(part, ensure_ascii=False, indent=1)
        with open(os.path.join(folder, name + ".json"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        written.append((name + ".json", len(text.encode("utf-8"))))
    return written


def local_day(parser, flag, value):
    # fromisoformat alone also accepts 20260904 and 2026-W36-5, which match no day key and read as an empty range
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(value)
        return datetime.date.fromisoformat(value)
    except ValueError:
        parser.error("%s must be YYYY-MM-DD" % flag)


def describe(digest):
    if not digest["from"]:
        return "any day before today"
    return digest["from"] if digest["from"] == digest["to"] else "%s to %s" % (digest["from"], digest["to"])


def main(argv=None, today=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="first", help="first local day of the range, YYYY-MM-DD")
    parser.add_argument("--to", dest="last", help="last local day of the range, YYYY-MM-DD; today when left out")
    parser.add_argument("--out", help="write the digest here instead of stdout")
    parser.add_argument("--no-live", action="store_true", help="skip git and pull request lookups")
    parser.add_argument("--split", help="also write one digest per repository into this folder")
    args = parser.parse_args(argv)
    if args.split and not args.out:
        parser.error("--split needs --out")
    today = today or datetime.date.today()
    if args.last and not args.first:
        parser.error("--to needs --from")
    if args.first:
        first = local_day(parser, "--from", args.first)
        last = local_day(parser, "--to", args.last) if args.last else today
        if last < first:
            parser.error("--to must not be before --from")
        if last > today:
            parser.error("--to must not be after today")
        if (last - first).days + 1 > MAX_DAYS:
            parser.error("a range holds at most %d days, both ends included" % MAX_DAYS)
        args.first, args.last = first.isoformat(), last.isoformat()
    digest = collect(args.first, args.last, live=not args.no_live, today=today.isoformat())
    text = json.dumps(digest, ensure_ascii=False, indent=1)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        sessions = sum(len(group["sessions"]) for group in digest["repositories"])
        print("%s: %d sessions in %d folders, %d headless runs left out, %d bytes -> %s"
              % (describe(digest), sessions, len(digest["repositories"]), digest["left_out"]["headless"], len(text.encode("utf-8")), args.out))
        if args.split:
            for name, size in split(digest, args.split):
                print("  %d bytes -> %s" % (size, os.path.join(args.split, name)))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    if not digest["repositories"]:
        print("no sessions on %s" % describe(digest), file=sys.stderr)
        return EXIT_NO_SESSIONS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
