"""Extract the prompts a person typed in Claude Code sessions run inside the given repositories.

Reads the session transcripts under ~/.claude/projects and prints one JSON document:
the number of in-scope sessions, how many were whoami runs and left out,
and every human-typed prompt with its session, time and working directory.
With --text it prints the same prompts as plain text grouped by session instead.

    python extract_prompts.py <repository> [<repository> ...] [--projects <dir>] [--text]

Exit status 3 means sessions were found in scope but none held a human-typed prompt,
which is what a change to the undocumented transcript format looks like.
"""

import argparse
import glob
import json
import os
import re
import sys

FORMAT_CHANGED = 3

CWD = re.compile(r'"cwd":"((?:[^"\\]|\\.)*)"')
SESSION = re.compile(r'"sessionId":"([^"]+)"')

# a paste is text the person handed over rather than wrote, so it is replaced by its size
PASTE = re.compile(r'<pasted_content(?: id="([^"]*)")?>.*?</pasted_content(?: id="\1")?>', re.DOTALL)

# prompts inside a whoami run are answers to its own questions, not evidence of how the person steers
WHOAMI = re.compile(r"<command-name>/whoami(?::whoami)?</command-name>")


def normalise(path):
    path = os.path.expanduser(path)
    # git bash hands over /c/Users/... on Windows
    drive = re.match(r"^/([a-zA-Z])(/|$)", path)
    if os.name == "nt" and drive:
        path = drive.group(1) + ":/" + path[3:]
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def in_scope(cwd, roots):
    path = normalise(cwd)
    # a bare prefix test would put repo-a-generator inside repo-a
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def human_prompt(record):
    if record.get("type") != "user":
        return False
    if (record.get("origin") or {}).get("kind") != "human":
        return False
    # compaction summaries, skill bodies and subagent turns are written by the model, whatever their origin says
    return not (record.get("isMeta") or record.get("isCompactSummary") or record.get("isSidechain"))


def strip_pastes(text):
    return PASTE.sub(lambda m: "[pasted text, %d characters]" % len(m.group(0)), text)


def extract(roots, projects):
    sessions = set()
    prompts = []
    for path in glob.glob(os.path.join(projects, "**", "*.jsonl"), recursive=True):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = CWD.search(line)
                if not match:
                    continue
                cwd = json.loads('"%s"' % match.group(1))
                if not in_scope(cwd, roots):
                    continue
                # a session's subagents write files of their own, so counting files would count them as sessions
                session = SESSION.search(line)
                sessions.add(session.group(1) if session else path)
                # most lines are tool traffic, so skip the full parse unless the line can be a human prompt
                if '"human"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                # the pattern above can land on a nested object's cwd, so the record's own field decides
                cwd = record.get("cwd") or ""
                if not human_prompt(record) or not in_scope(cwd, roots):
                    continue
                prompts.append({
                    "session": record.get("sessionId") or os.path.splitext(os.path.basename(path))[0],
                    "time": record.get("timestamp", ""),
                    "cwd": cwd,
                    "text": strip_pastes(text_of((record.get("message") or {}).get("content"))),
                })
    # the session breaks ties, so which copy of a repeated prompt comes first does not depend on file order
    prompts.sort(key=lambda p: (p["time"], p["session"]))
    return len(sessions), prompts


def drop_whoami_sessions(prompts):
    runs = {p["session"] for p in prompts if WHOAMI.search(p["text"])}
    return [p for p in prompts if p["session"] not in runs], len(runs)


def drop_copies(prompts):
    # a resumed or forked session carries the earlier conversation into a new file,
    # and counting both copies would fake a pattern seen in two different sessions
    seen = set()
    kept = []
    for p in prompts:
        key = (p["time"], p["text"])
        if key not in seen:
            seen.add(key)
            kept.append(p)
    return kept, len(prompts) - len(kept)


def as_text(sessions, excluded, copies, prompts):
    lines = ["sessions in scope: %d, whoami runs left out: %d, copied prompts left out: %d, prompts: %d" % (
        sessions, excluded, copies, len(prompts))]
    grouped = {}
    for p in prompts:
        grouped.setdefault(p["session"], []).append(p)
    for session, items in grouped.items():
        lines += ["", "## session %s  %s" % (session, items[0]["cwd"])]
        # one prompt per line keeps a multi-line prompt from reading as several
        lines += ["[%s] %s" % (p["time"][:16], p["text"].replace("\n", " / ")) for p in items]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repositories", nargs="+")
    parser.add_argument("--projects", default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    parser.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)

    roots = [normalise(r) for r in args.repositories]
    sessions, found = extract(roots, args.projects)
    # whoami runs go first, because a resumed whoami run is recognised only by its copied command prompt
    prompts, excluded = drop_whoami_sessions(found)
    prompts, copies = drop_copies(prompts)
    # a Windows console defaults to cp1252, which cannot encode most prompts written outside English
    sys.stdout.reconfigure(encoding="utf-8")
    if args.text:
        sys.stdout.write(as_text(sessions, excluded, copies, prompts))
    else:
        json.dump({"sessions_in_scope": sessions, "whoami_sessions_excluded": excluded,
                   "copied_prompts_dropped": copies, "prompts": prompts},
                  sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")

    # judged before whoami runs are dropped, since the run in progress is always one of them
    if sessions and not found:
        sys.stderr.write(
            "%d sessions ran in scope but none held a human-typed prompt; "
            "the transcript format has probably changed, so this is not evidence of anything\n" % sessions)
        return FORMAT_CHANGED
    return 0


if __name__ == "__main__":
    sys.exit(main())
