"""Collect one day of Claude Code sessions into a digest the model reads.

The transcripts of a working day run to tens of megabytes, so the model never reads them.
This script keeps only what a summary needs: who asked what and when, the questions a session left,
what it pushed or opened, and the live state of each repository and pull request it named.
A transcript says what was pending when the session stopped, and the live state says whether it still is,
because a pull request a session left in review is often merged the same afternoon.

Usage:
    python collect.py [--day YYYY-MM-DD] [--out digest.json] [--no-live]

Without --day, the day is the most recent one before today that has sessions,
so a Monday run reports the Friday before.
Exit status 3 means no session was active that day.
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

EXIT_OK = 0
EXIT_NO_SESSIONS = 3

PROMPT_LIMIT = 600
SUMMARY_LIMIT = 4000
QUESTION_TAIL = 700
LAST_TAIL = 1500
MAX_PROMPTS = 80
COMMAND_LIMIT = 160
LIVE_TIMEOUT = 60
# a pause longer than this splits a session into separate stretches of activity
ACTIVE_GAP_MINUTES = 30

COMPACTION_PREFIX = "This session is being continued from a previous conversation"
NOISE_PREFIXES = ("<local-command", "<task-notification>", "<bash-input>", "<bash-stdout>", "<bash-stderr>")
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
GIT_ACTIONS = (
    ("commit", re.compile(r"\bgit\b[^\n|;&]*\bcommit\b")),
    ("push", re.compile(r"\bgit\b[^\n|;&]*\bpush\b")),
    ("pr-create", re.compile(r"\baz repos pr create\b|\bgh pr create\b")),
)
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


def default_day(paths, today):
    earlier = set()
    for path in paths:
        earlier.update(day for day in days_in(path) if day < today)
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
    if not text or text.startswith(NOISE_PREFIXES):
        return None
    if text.startswith(COMPACTION_PREFIX):
        return "summary", text[:SUMMARY_LIMIT]
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


def subagent_usage(path, day, totals):
    """Subagents spend on the session's behalf, so their usage counts toward it."""
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
                if when and when[0] == day:
                    add_usage(totals, seen, entry)


def git_actions(command):
    return [kind for kind, pattern in GIT_ACTIONS if pattern.search(command)]


def minutes(clock):
    hours, mins = clock.split(":")
    return int(hours) * 60 + int(mins)


def stretches(clocks):
    """The session's stretches of activity: its first and last entries alone draw a session left open over lunch as a day of work."""
    found = []
    for clock in sorted(clocks):
        if found and minutes(clock) - minutes(found[-1][1]) <= ACTIVE_GAP_MINUTES:
            found[-1][1] = clock
        else:
            found.append([clock, clock])
    return found


def result_text(content):
    """A tool result as text, its text blocks as written: serialising them would escape the quotes a pattern looks for."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") if isinstance(part, dict) and part.get("type") == "text"
                         else json.dumps(part, ensure_ascii=False) for part in content)
    return json.dumps(content, ensure_ascii=False)


def read_session(path, day):
    """Everything the digest keeps from one transcript on one day, or None when it has nothing that day."""
    session = {
        "id": os.path.basename(path)[:-len(".jsonl")],
        "cwd": None,
        "entrypoint": None,
        "branches": [],
        "first": None,
        "last": None,
        "prompts": [],
        "questions": [],
        "actions": [],
        "pull_requests": [],
        "pull_requests_new": [],
        "last_reply": None,
        "usage": {},
        "active": [],
    }
    seen_usage = set()
    clocks = []
    branches = []
    prs = set()
    replies = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            when = local_time(entry.get("timestamp", ""))
            if not when or when[0] != day:
                continue
            clock = when[1]
            clocks.append(clock)
            session["first"] = session["first"] or clock
            session["last"] = clock
            session["cwd"] = entry.get("cwd") or session["cwd"]
            session["entrypoint"] = entry.get("entrypoint") or session["entrypoint"]
            branch = entry.get("gitBranch")
            if branch and branch != "HEAD" and branch not in branches:
                branches.append(branch)
            kind = entry.get("type")
            if kind == "user":
                prompt = typed_prompt(entry)
                if prompt and not invokes_remind_me(prompt[1]):
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
                        for action in git_actions(command):
                            session["actions"].append({"at": clock, "kind": action, "command": command[:COMMAND_LIMIT]})
    if session["first"] is None:
        return None
    session["active"] = stretches(clocks)
    subagent_usage(path, day, session["usage"])
    session["branches"] = branches
    session["pull_requests"] = sorted(prs)
    session["questions"] = [{"at": clock, "text": text[-QUESTION_TAIL:]} for clock, text in replies if is_question(text)]
    if replies:
        session["last_reply"] = {"at": replies[-1][0], "text": replies[-1][1][-LAST_TAIL:]}
    if len(session["prompts"]) > MAX_PROMPTS:
        # the opening states the topic and the end states where it stopped, so the middle goes first
        keep = session["prompts"]
        session["prompts"] = keep[:10] + [{"at": None, "kind": "elided", "text": "%d prompts left out" % (len(keep) - MAX_PROMPTS)}] + keep[-(MAX_PROMPTS - 10):]
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
                           "{status:status,title:title,target:targetRefName,created:creationDate,closed:closedDate,"
                           "author:createdBy.displayName,repository:repository.name}"])
            if ok:
                break
            reason = out
        else:
            return {"status": "unknown", "reason": reason}
    else:
        repo, _, number = ref.partition("#")
        ok, out = run(["gh", "pr", "view", number, "-R", repo, "--json", "state,title,baseRefName,createdAt,closedAt,mergedAt,url,author"])
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
    return state


def created_on(state, day):
    when = local_time(state.get("created") or "")
    return bool(when) and when[0] == day


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
            remote, _ = run(["git", "-C", root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch])
            entry["on_remote"] = remote
            if remote:
                ok, out = run(["git", "-C", root, "rev-list", "--count", "origin/%s..%s" % (branch, branch)])
                entry["unpushed"] = count(out) if ok else None
        state["branches"].append(entry)
    ok, remote = run(["git", "-C", root, "remote", "get-url", "origin"])
    state["remote"] = remote.strip() if ok else None
    return state


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


def collect(day, live=True, root=None, today=None):
    root = root or os.path.join(config_dir(), "projects")
    paths = transcripts(root)
    today = today or datetime.date.today().isoformat()
    day = day or default_day(paths, today)
    digest = {"day": day, "timezone": datetime.datetime.now().astimezone().strftime("%z"),
              "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "left_out": {"headless": 0}, "repositories": [], "pull_requests": {}}
    if not day:
        return digest
    running = running_sessions()
    groups = {}
    for path in paths:
        if day not in days_in(path):
            continue
        session = read_session(path, day)
        if not session:
            continue
        if session["entrypoint"] == "sdk-cli":
            # `claude -p` runs are scripts and probes, not work a person left open
            digest["left_out"]["headless"] += 1
            continue
        if not session["prompts"]:
            continue
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
        # a session also names old pull requests it labelled or compared against, so the new ones are those created that day,
        # by the platform's own record; one session can name another session's new pull request, so this is no claim of who opened it
        for group in digest["repositories"]:
            for session in group["sessions"]:
                session["pull_requests_new"] = [key for key in session["pull_requests"]
                                                   if created_on(digest["pull_requests"].get(key, {}), day)]
    return digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day", help="local day to report, YYYY-MM-DD")
    parser.add_argument("--out", help="write the digest here instead of stdout")
    parser.add_argument("--no-live", action="store_true", help="skip git and pull request lookups")
    args = parser.parse_args(argv)
    if args.day:
        # fromisoformat alone also accepts 20260904 and 2026-W36-5, which match no day key and read as an empty day
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.day):
                raise ValueError(args.day)
            datetime.date.fromisoformat(args.day)
        except ValueError:
            parser.error("--day must be YYYY-MM-DD")
    digest = collect(args.day, live=not args.no_live)
    text = json.dumps(digest, ensure_ascii=False, indent=1)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        sessions = sum(len(group["sessions"]) for group in digest["repositories"])
        print("%s: %d sessions in %d folders, %d headless runs left out, %d bytes -> %s"
              % (digest["day"], sessions, len(digest["repositories"]), digest["left_out"]["headless"], len(text.encode("utf-8")), args.out))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    if not digest["repositories"]:
        # nothing found can also mean the transcripts are gone: Claude Code deletes them after cleanupPeriodDays
        print("no sessions on %s; Claude Code deletes transcripts after cleanupPeriodDays, 30 days by default"
              % (digest["day"] or "any day before today"), file=sys.stderr)
        return EXIT_NO_SESSIONS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
