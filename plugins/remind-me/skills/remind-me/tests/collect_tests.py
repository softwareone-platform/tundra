"""Deterministic self-check for the remind-me collector.

The collector reads an undocumented transcript format and the live state of repositories and pull requests,
so every case here is a known-answer case over a synthetic projects directory
(<project>/<session>.jsonl files) written to a temp dir, or over real git repositories built in temp dirs.
Nothing reaches the network or the real ~/.claude:
CLAUDE_CONFIG_DIR points at a temp dir, every collect call is given its root,
and collect.run is wrapped so only git starts a real process, while az and gh answer from a table or not at all.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/collect_tests.py
"""

import datetime
import io
import json
import os
import subprocess
import sys
import tempfile


def _tempdir():
    # git reports the long form of a path, while mkdtemp can hand back an 8.3 short name on Windows
    return os.path.realpath(tempfile.mkdtemp())


# the module reads its config dir at call time, so it is pointed away from the real ~/.claude before anything runs
CONFIG = _tempdir()
os.environ["CLAUDE_CONFIG_DIR"] = CONFIG

# the global and system git config would otherwise reach into the temp repositories,
# and the script's git calls inherit os.environ, so this has to happen before any git call
_GLOBAL_CONFIG = os.path.join(_tempdir(), "gitconfig")
open(_GLOBAL_CONFIG, "w").close()
os.environ["GIT_CONFIG_GLOBAL"] = _GLOBAL_CONFIG
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
    os.environ.pop(_var, None)

# import the module under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _SCRIPTS)
import collect as cl  # noqa: E402

_SCRIPT = os.path.join(_SCRIPTS, "collect.py")

# a Friday, the Thursday before it, and the Monday after it
DAY = "2026-09-04"
PREV = "2026-09-03"
TODAY = "2026-09-07"
# the Wednesday before PREV and the Saturday after DAY, for ranges and sessions that cross a day
BEFORE = "2026-09-02"
NEXT = "2026-09-05"

# a folder outside any repository, where a session can run
FOLDER = _tempdir()


# every check records (group, name, ok, detail),
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(name, got, want):
    ok = got == want
    _results.append((_group, name, ok, "" if ok else "got %r, want %r" % (got, want)))


# ----- no CLI but git ever starts ------------------------------------------------

_real_run = cl.run
_escaped = []


def _git_only(argv, cwd=None):
    # az and gh would reach the network, so anything but git is refused and recorded
    if argv[0] == "git":
        return _real_run(argv, cwd)
    _escaped.append(list(argv))
    return False, "blocked by the tests"


cl.run = _git_only


def _with_cli(answer, body):
    """Run body with az and gh answered by answer(argv), git still real; return (body's result, the az/gh calls)."""
    calls = []

    def fake(argv, cwd=None):
        if argv[0] == "git":
            return _real_run(argv, cwd)
        calls.append(list(argv))
        return answer(argv)

    cl.run = fake
    try:
        return body(), calls
    finally:
        cl.run = _git_only


# ----- synthetic transcripts ---------------------------------------------------

def _ts(day, clock):
    """The transcript's own UTC spelling of a local day and HH:MM, so local_time has to convert it back."""
    moment = datetime.datetime.fromisoformat(day + "T" + clock).astimezone(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _local(day, clock):
    """A local day and HH:MM as an ISO timestamp with the local offset, as az and gh answer."""
    return datetime.datetime.fromisoformat(day + "T" + clock).astimezone().isoformat()


def _entry(kind, message, at, day=DAY, cwd=FOLDER, branch="main", **extra):
    record = {"type": kind, "message": message, "timestamp": _ts(day, at), "sessionId": "s",
              "isSidechain": False, "entrypoint": "cli", "gitBranch": branch}
    if cwd is not None:
        record["cwd"] = cwd
    record.update(extra)
    return record


def _user(content, at="10:00", **extra):
    return _entry("user", {"role": "user", "content": content}, at, **extra)


def _assistant(content, at="10:00", msg_id=None, usage=None, model="claude-x", **extra):
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    message = {"role": "assistant", "model": model, "content": content}
    if msg_id is not None:
        message["id"] = msg_id
    if usage is not None:
        message["usage"] = usage
    return _entry("assistant", message, at, **extra)


def _tool_use(name, command):
    return {"type": "tool_use", "id": "t", "name": name, "input": {"command": command}}


def _tool_result(content):
    return {"type": "tool_result", "tool_use_id": "t", "content": content}


INTERRUPTED = "[Request interrupted by user]"
INTERRUPTED_TOOL = "[Request interrupted by user for tool use]"


def _interrupt(marker, at="10:00", **extra):
    """An interruption as Claude Code writes it: a user entry holding only the marker in a text block."""
    return _user([{"type": "text", "text": marker}], at=at, **extra)


def _usage(inp=0, out=0, read=0, write=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write}


def _projects(files):
    """Write {relative path: [record, ...]} under a fresh projects dir.
    Lines are compact, which is how Claude Code writes them, and days_in's line scan depends on that exact spelling."""
    d = _tempdir()
    for rel, records in files.items():
        path = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write((r if isinstance(r, str) else json.dumps(r, separators=(",", ":"), ensure_ascii=False)) + "\n")
    return d


def _session(records, day=DAY, name="s1"):
    root = _projects({"proj/%s.jsonl" % name: records})
    # read_session takes a set of days, and a single day string would match only by substring
    days = {day} if isinstance(day, str) else set(day)
    return cl.read_session(os.path.join(root, "proj", name + ".jsonl"), days)


def _collect(files, day=DAY, here=None, **kwargs):
    """Run collect over a fresh projects dir from inside here, a non-repository folder by default,
    because collect asks git about os.getcwd() and the tests' own checkout must stay out of it."""
    root = _projects(files)
    saved = os.getcwd()
    os.chdir(here or _tempdir())
    try:
        return cl.collect(first=day, last=day, root=root, **kwargs)
    finally:
        os.chdir(saved)


def _collect_span(files, first, last, **kwargs):
    """Run collect over a range of days from a non-repository folder, as _collect does for one day."""
    root = _projects(files)
    saved = os.getcwd()
    os.chdir(_tempdir())
    try:
        return cl.collect(first=first, last=last, root=root, **kwargs)
    finally:
        os.chdir(saved)


def _summary(digest):
    return [(g["path"], g["is_repository"], g["current"], [s["id"] for s in g["sessions"]])
            for g in digest["repositories"]]


# ----- synthetic repositories ---------------------------------------------------

def _git(repository, *args):
    result = subprocess.run(["git", "-C", repository, "-c", "user.name=t", "-c", "user.email=t@example.com",
                             "-c", "commit.gpgsign=false"] + list(args),
                            capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def _repo():
    path = _tempdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "commit", "-q", "--allow-empty", "-m", "first")
    return path


def _toplevel(path):
    return os.path.normpath(_git(path, "rev-parse", "--show-toplevel"))


# ----- fixture ------------------------------------------------------------------

def test_isolation():
    probe = subprocess.run(["git", "-C", FOLDER, "rev-parse", "--git-dir"], capture_output=True)
    check("fixture: the session folder is outside any repository", probe.returncode != 0, True)
    check("fixture: the config dir is the temp dir", cl.config_dir(), CONFIG)


# ----- local_time, days_in, default_day ------------------------------------------

def test_local_time():
    check("UTC Z timestamp converted to the local day and clock", cl.local_time(_ts(DAY, "23:30")), (DAY, "23:30"))
    check("offset timestamp converted to the local day and clock", cl.local_time(_local(DAY, "00:15")), (DAY, "00:15"))
    check("unparseable text is None", cl.local_time("yesterday"), None)
    check("empty text is None", cl.local_time(""), None)
    check("None is None", cl.local_time(None), None)


def test_days_in():
    root = _projects({"proj/s1.jsonl": [_user("a", at="09:00", day=PREV), _user("b", at="23:59", day=DAY),
                                        '{"type":"summary","summary":"no timestamp"}',
                                        '{"type":"user","timestamp":"garbage"}']})
    check("every local day with an entry", cl.days_in(os.path.join(root, "proj", "s1.jsonl")), {PREV, DAY})


def test_default_day():
    root = _projects({"proj/a.jsonl": [_user("thursday", day=PREV)],
                      "proj/b.jsonl": [_user("friday", day=DAY), _user("monday", day=TODAY)]})
    paths = cl.transcripts(root)
    # a Monday run reports the Friday before, and today itself never counts
    check("the latest day before today", cl.default_day(paths, TODAY), DAY)
    check("a day with entries that is today is left out", cl.default_day(paths, DAY), PREV)
    check("nothing before today is None", cl.default_day(paths, PREV), None)
    check("no transcripts is None", cl.default_day([], TODAY), None)


def _work_days(records):
    root = _projects({"proj/s1.jsonl": records})
    return cl.work_days(os.path.join(root, "proj", "s1.jsonl"))


def test_work_days():
    check("a typed prompt makes a work day", _work_days([_user("fix the build")]), {DAY})
    # the request that runs this skill is not part of the day it reports on
    check("a day whose only prompt is the /remind-me command is not a work day",
          _work_days([_user("<command-name>/remind-me</command-name>")]), set())
    check("a day whose only prompt types /remind-me is not a work day",
          _work_days([_user("/remind-me --from 2026-09-03")]), set())
    check("a day with only a tool result is not a work day", _work_days([_user([_tool_result("ok")])]), set())
    check("a day with only an isMeta entry is not a work day",
          _work_days([_user("Base directory for this skill", isMeta=True)]), set())
    check("a day with only command noise is not a work day",
          _work_days([_user("<local-command-stdout>done</local-command-stdout>")]), set())
    check("a day with only assistant entries is not a work day", _work_days([_assistant("a reply")]), set())
    check("a malformed user line is skipped",
          _work_days(['{"type":"user","timestamp":"' + _ts(PREV, "09:00") + '",broken', _user("fix the build")]),
          {DAY})
    # a `claude -p` transcript is a script or a probe, so even a typed prompt before its sdk-cli entry does not count
    check("a transcript with an sdk-cli user entry has no work days",
          _work_days([_user("thursday", day=PREV), _user("probe", entrypoint="sdk-cli")]), set())


def test_work_days_interrupted():
    check("a day whose only user entry is an interruption is not a work day", _work_days([_interrupt(INTERRUPTED)]),
          set())
    check("a day whose only user entry is an interruption for tool use is not a work day",
          _work_days([_interrupt(INTERRUPTED_TOOL)]), set())
    check("an interruption the day before a typed prompt adds only the prompt's day",
          _work_days([_interrupt(INTERRUPTED, day=PREV), _user("fix the build")]), {DAY})


def test_default_day_work_days():
    typed = {"proj/a.jsonl": [_user("thursday", day=PREV)]}
    headless = dict(typed, **{"proj/b.jsonl": [_user("probe", day=DAY, entrypoint="sdk-cli")]})
    remind = dict(typed, **{"proj/c.jsonl": [_user("<command-name>/remind-me</command-name>", day=DAY),
                                             _assistant("the report", day=DAY)]})
    check("a later day with only a claude -p run is skipped for the earlier day with work",
          cl.default_day(cl.transcripts(_projects(headless)), TODAY), PREV)
    check("a later day with only a /remind-me run is skipped for the earlier day with work",
          cl.default_day(cl.transcripts(_projects(remind)), TODAY), PREV)


def test_transcripts_skip_subagents():
    root = _projects({"proj/s1.jsonl": [_user("x")], "proj/s1/subagents/a1.jsonl": [_assistant("y")]})
    check("only main-session files", cl.transcripts(root), [os.path.join(root, "proj", "s1.jsonl")])


# ----- typed_prompt: what the person typed ---------------------------------------

def test_typed_prompt_kept():
    check("string content", cl.typed_prompt(_user("fix the build")), ("prompt", "fix the build"))
    check("list content keeps the text blocks, one per line",
          cl.typed_prompt(_user([{"type": "text", "text": "first"}, {"type": "image", "source": {}},
                                 {"type": "text", "text": "second"}])), ("prompt", "first\nsecond"))
    check("system reminder stripped",
          cl.typed_prompt(_user("<system-reminder>\nhook text\n</system-reminder>\nfix it")), ("prompt", "fix it"))
    check("a prompt is cut to PROMPT_LIMIT", cl.typed_prompt(_user("x" * 700)), ("prompt", "x" * cl.PROMPT_LIMIT))


def test_typed_prompt_summary_and_command():
    summary = cl.COMPACTION_PREFIX + ". The conversation is summarised below."
    check("compaction summary kept as summary", cl.typed_prompt(_user(summary)), ("summary", summary))
    long_summary = cl.COMPACTION_PREFIX + "y" * 5000
    check("a long summary keeps its first SUMMARY_HEAD and last SUMMARY_TAIL characters",
          cl.typed_prompt(_user(long_summary)),
          ("summary", long_summary[:cl.SUMMARY_HEAD] + "\n[...]\n" + long_summary[-cl.SUMMARY_TAIL:]))
    check("command with its args",
          cl.typed_prompt(_user("<command-message>review</command-message>\n<command-name>/review</command-name>\n"
                                "<command-args>  42 now </command-args>")), ("command", "/review 42 now"))
    check("command without args", cl.typed_prompt(_user("<command-name>/clear</command-name>")), ("command", "/clear"))
    check("command name without its slash gets one",
          cl.typed_prompt(_user("<command-name>plugin:skill</command-name>")), ("command", "/plugin:skill"))


def test_typed_prompt_summary_boundary():
    head = cl.COMPACTION_PREFIX + "a" * (cl.SUMMARY_HEAD - len(cl.COMPACTION_PREFIX))
    tail = "z" * cl.SUMMARY_TAIL
    check("a summary of exactly SUMMARY_HEAD + SUMMARY_TAIL characters is kept whole",
          cl.typed_prompt(_user(head + tail)), ("summary", head + tail))
    # the one character past the limit sits between head and tail, so only it is dropped
    check("one character more is cut to head, marker and tail",
          cl.typed_prompt(_user(head + "m" + tail)), ("summary", head + "\n[...]\n" + tail))


def test_typed_prompt_excluded():
    check("tool_result-only user record",
          cl.typed_prompt(_user([_tool_result("ok")])), None)
    check("tool_result beside text is still a tool result",
          cl.typed_prompt(_user([_tool_result("ok"), {"type": "text", "text": "words"}])), None)
    check("isMeta", cl.typed_prompt(_user("Base directory for this skill", isMeta=True)), None)
    check("isSidechain", cl.typed_prompt(_user("subagent turn", isSidechain=True)), None)
    check("local command output", cl.typed_prompt(_user("<local-command-stdout>done</local-command-stdout>")), None)
    check("local command caveat", cl.typed_prompt(_user("<local-command-caveat>Caveat</local-command-caveat>")), None)
    check("task notification", cl.typed_prompt(_user("<task-notification>done</task-notification>")), None)
    check("bash input", cl.typed_prompt(_user("<bash-input>ls</bash-input>")), None)
    check("only a system reminder", cl.typed_prompt(_user("<system-reminder>x</system-reminder>")), None)
    check("empty content", cl.typed_prompt(_user("")), None)
    check("no message", cl.typed_prompt({"type": "user"}), None)


def test_typed_prompt_interrupted():
    check("the interruption marker", cl.typed_prompt(_interrupt(INTERRUPTED)), None)
    check("the interruption marker for tool use", cl.typed_prompt(_interrupt(INTERRUPTED_TOOL)), None)
    check("the marker as string content", cl.typed_prompt(_user(INTERRUPTED)), None)
    check("the marker once its system reminder is stripped",
          cl.typed_prompt(_user("<system-reminder>\nhook text\n</system-reminder>\n" + INTERRUPTED_TOOL)), None)
    # the match is on the whole text, so a prompt that quotes the marker is still what the person typed
    check("a prompt that starts with the marker is still a prompt",
          cl.typed_prompt(_user(INTERRUPTED + " then fix the build")),
          ("prompt", "[Request interrupted by user] then fix the build"))
    check("a prompt that mentions the marker is still a prompt",
          cl.typed_prompt(_user("why did I see [Request interrupted by user for tool use] there?")),
          ("prompt", "why did I see [Request interrupted by user for tool use] there?"))


def test_invokes_remind_me():
    check("the slash command", cl.invokes_remind_me("/remind-me"), True)
    check("the slash command with args after whitespace", cl.invokes_remind_me("  /remind-me --from 2026-09-04"), True)
    check("the command as typed_prompt renders it", cl.invokes_remind_me(
        cl.typed_prompt(_user("<command-name>/remind-me:remind-me</command-name>"))[1]), True)
    check("the plugin-qualified name in the head", cl.invokes_remind_me("run remind-me:remind-me please"), True)
    check("the plain words", cl.invokes_remind_me("please remind me to push"), False)
    check("the command later in the text", cl.invokes_remind_me("fix the build, then run /remind-me"), False)


def test_is_question():
    check("a trailing question mark", cl.is_question("Shall we merge it?"), True)
    check("a full-width question mark", cl.is_question("\u8981\u5408\u4f75\u55ce\uff1f"), True)
    check("a question mark followed by a short closing", cl.is_question("Merge now? Either works."), True)
    check("a decision phrase ending in a full stop", cl.is_question("Option A or option B. Your call."), True)
    check("a Chinese decision phrase", cl.is_question("\u5169\u500b\u90fd\u53ef\u4ee5\uff0c\u7531\u4f60\u6c7a\u5b9a\u3002"),
          True)
    check("a plain statement", cl.is_question("Done, pushed to origin."), False)
    # only the last 300 characters are read, so an early question in a long reply does not count
    check("a question mark far from the end", cl.is_question("Why? " + "x" * 400 + "."), False)


# ----- pull_requests: which keys a text names ------------------------------------

def test_pull_requests_azure():
    check("pullrequest/<n>",
          cl.pull_requests("https://dev.azure.com/acme/p/_git/r/pullrequest/151943"), {"azure:151943"})
    check("!<n>", cl.pull_requests("opened !151943."), {"azure:151943"})
    check("!<n> at the start", cl.pull_requests("!4321 merged"), {"azure:4321"})
    check("!<n> inside a word", cl.pull_requests("abc!123"), set())
    check("!<n> after a slash", cl.pull_requests("a/!123"), set())
    check("!<n> after an ampersand", cl.pull_requests("x&!123"), set())
    check("!<n> with too few digits", cl.pull_requests("!12"), set())
    check('"pullRequestId": n', cl.pull_requests('{"pullRequestId": 777, "x": 1}'), {"azure:777"})
    check('"pullRequestId":n without a space', cl.pull_requests('{"pullRequestId":778}'), {"azure:778"})
    check("az repos pr ... --id n", cl.pull_requests("az repos pr show --id 4242 -o json"), {"azure:4242"})
    check("az repos pr ... --id=n", cl.pull_requests("az repos pr update --status completed --id=99"), {"azure:99"})
    check("several forms in one text",
          cl.pull_requests("!1001 and pullrequest/1002 and !1001 again"), {"azure:1001", "azure:1002"})


def test_pull_requests_github():
    check("full pull URL", cl.pull_requests("see https://github.com/acme/widgets/pull/12 now"),
          {"github:acme/widgets#12"})
    check("owner and repo with dots and dashes", cl.pull_requests("github.com/a-b/c.d/pull/7"), {"github:a-b/c.d#7"})
    # a GitHub number means nothing without its repository
    check("a bare #12", cl.pull_requests("fixed in #12"), set())
    check("PR 12", cl.pull_requests("see PR 12"), set())
    check("None", cl.pull_requests(None), set())


# ----- usage ------------------------------------------------------------------------

def test_add_usage():
    totals, seen = {}, set()
    # a reply split over several transcript lines repeats the same usage under one message id
    cl.add_usage(totals, seen, _assistant("a", msg_id="m1", usage=_usage(10, 5, 100, 7)))
    cl.add_usage(totals, seen, _assistant("b", msg_id="m1", usage=_usage(10, 5, 100, 7)))
    cl.add_usage(totals, seen, _assistant("c", msg_id="m2", usage=_usage(1, 2, 3, 4)))
    check("one message id counted once, another added",
          totals, {"claude-x": {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 103,
                                "cache_creation_input_tokens": 11}})

    totals, seen = {}, set()
    cl.add_usage(totals, seen, _assistant("a", requestId="r1", usage=_usage(3), model=None))
    cl.add_usage(totals, seen, _assistant("b", requestId="r1", usage=_usage(3), model=None))
    check("requestId keys a reply with no message id, and no model is unknown",
          totals, {"unknown": {"input_tokens": 3, "output_tokens": 0, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}})

    totals, seen = {}, set()
    cl.add_usage(totals, seen, _assistant("a", msg_id="m1"))
    check("no usage adds nothing", (totals, seen), ({}, set()))


def test_subagent_usage():
    root = _projects({
        "proj/s1.jsonl": [_user("x")],
        "proj/s1/subagents/a1.jsonl": [_assistant("a", msg_id="sa1", usage=_usage(3, 1), model="claude-y"),
                                       _assistant("b", msg_id="sa1", usage=_usage(3, 1), model="claude-y"),
                                       _assistant("c", msg_id="sa2", usage=_usage(100), model="claude-y", day=PREV),
                                       '{"usage": broken'],
        "proj/s1/subagents/a2.jsonl": [_assistant("d", msg_id="sb1", usage=_usage(5), model="claude-y")],
    })
    totals = {"claude-x": dict.fromkeys(cl.USAGE_FIELDS, 1)}
    cl.subagent_usage(os.path.join(root, "proj", "s1.jsonl"), {DAY}, totals)
    check("same-day subagent usage added, other days and broken lines ignored",
          totals, {"claude-x": dict.fromkeys(cl.USAGE_FIELDS, 1),
                   "claude-y": {"input_tokens": 8, "output_tokens": 1, "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0}})


# ----- read_session -------------------------------------------------------------

def _rich_session():
    root = _projects({
        "proj/s1.jsonl": [
            _user("fix the build", at="10:00", branch="main"),
            _assistant("Want me to open the PR?", at="10:05", msg_id="m1", usage=_usage(10, 5), branch="HEAD"),
            _assistant([_tool_use("Bash", "git commit -m wip && git push origin feature")], at="10:05",
                       msg_id="m1", usage=_usage(10, 5), branch="feature"),
            _assistant([_tool_use("Read", "git commit -m not-a-shell")], at="10:05", msg_id="m1",
                       usage=_usage(10, 5)),
            _user([_tool_result("Created !151943")], at="10:06"),
            # a list-content result is searched through its text blocks
            _user([_tool_result([{"type": "text", "text": "https://dev.azure.com/a/p/_git/r/pullrequest/888"}])],
                  at="10:07"),
            _assistant("Merged. See https://github.com/acme/widgets/pull/12", at="11:00", msg_id="m2",
                       usage=_usage(20, 6), branch="main"),
            _assistant("subagent words?", at="11:01", msg_id="m3", usage=_usage(1000), isSidechain=True),
            _assistant([_tool_use("PowerShell", "gh pr create --title x"), {"type": "text", "text": "   "}],
                       at="11:02", msg_id="m4"),
            # the run ends the day, because everything after it up to the next typed prompt belongs to the run
            _user("<command-name>/remind-me</command-name>", at="11:03"),
            _user("yesterday's prompt", at="09:00", day=PREV),
            "not json at all",
        ],
        "proj/s1/subagents/a1.jsonl": [_assistant("sub", msg_id="sa1", usage=_usage(3), model="claude-y")],
    })
    return cl.read_session(os.path.join(root, "proj", "s1.jsonl"), {DAY})


def test_read_session():
    session = _rich_session()
    check("id from the file name", session["id"], "s1")
    check("cwd", session["cwd"], FOLDER)
    check("entrypoint", session["entrypoint"], "cli")
    check("branches in order, HEAD left out", session["branches"], ["main", "feature"])
    check("first and last day and clock", (session["first"], session["last"]), (DAY + " 10:00", DAY + " 11:02"))
    check("the days the session has entries on", session["days"], [DAY])
    # the request that runs this skill is not part of the day it reports on
    check("prompts without the remind-me run", session["prompts"],
          [{"at": DAY + " 10:00", "kind": "prompt", "text": "fix the build"}])
    check("questions", session["questions"], [{"at": DAY + " 10:05", "text": "Want me to open the PR?"}])
    check("pull requests from results, replies and commands, sorted", session["pull_requests"],
          ["azure:151943", "azure:888", "github:acme/widgets#12"])
    check("last reply is the last non-blank text", session["last_reply"],
          {"at": DAY + " 11:00", "text": "Merged. See https://github.com/acme/widgets/pull/12"})
    check("usage: split reply once, sidechain left out, subagent added", session["usage"],
          {"claude-x": {"input_tokens": 30, "output_tokens": 11, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0},
           "claude-y": {"input_tokens": 3, "output_tokens": 0, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}})
    check("pull_requests_new is left to collect", session["pull_requests_new"], [])


def test_result_text():
    block = {"type": "text", "text": '{"pullRequestId": 777}'}
    check("a string passes through unchanged", cl.result_text('{"pullRequestId": 777}'), '{"pullRequestId": 777}')
    # serialising a text block would escape the quotes the pullRequestId pattern looks for
    check("a text block keeps its quotes", cl.pull_requests(cl.result_text([block])), {"azure:777"})
    check("text blocks joined by newlines",
          cl.result_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]), "a\nb")
    image = {"type": "image", "source": {"data": "x"}}
    check("a non-text block is serialised", cl.result_text([image]), json.dumps(image, ensure_ascii=False))
    check("a non-list, non-string value is serialised", cl.result_text({"k": 1}), '{"k": 1}')

    session = _session([_user([_tool_result([block])])])
    check("read_session finds a pullRequestId inside a list-content result", session["pull_requests"], ["azure:777"])


def test_read_session_nothing_that_day():
    check("only other days is None", _session([_user("thursday", day=PREV)]), None)
    check("no lines is None", _session([]), None)


def test_read_session_cuts():
    session = _session([_assistant("y" * 2000 + "?")])
    check("a question keeps its last QUESTION_TAIL characters", session["questions"][0]["text"],
          ("y" * 2000 + "?")[-cl.QUESTION_TAIL:])
    check("the last reply keeps its last LAST_TAIL characters", session["last_reply"]["text"],
          ("y" * 2000 + "?")[-cl.LAST_TAIL:])


def test_read_session_after_the_day():
    # the day is a snapshot, so whatever the session received after it changes nothing about the day
    session = _session([
        _user("earlier", at="09:00", day=PREV),
        _user("fix the build", at="10:00"),
        _assistant("Done.", at="11:00", msg_id="m1", usage=_usage(5, 1)),
        _user("merge it", at="09:15", day="2026-09-05", branch="later"),
        _user("<command-name>/remind-me</command-name>", at="08:00", day=TODAY, branch="later"),
        _user([_tool_result("Created !151943")], at="08:01", day=TODAY, branch="later"),
        _assistant("Merged https://github.com/acme/widgets/pull/12. Want me to push?", at="08:02", day=TODAY,
                   msg_id="m2", usage=_usage(100, 50), branch="later"),
        _user("<command-name>/review</command-name><command-args>42</command-args>", at="08:03", day=TODAY,
              branch="later"),
    ])
    check("a session carries no later_prompts", "later_prompts" in session, False)
    check("later entries leave first and last alone", (session["first"], session["last"]),
          (DAY + " 10:00", DAY + " 11:00"))
    check("typed prompts and commands after the day are not prompts", session["prompts"],
          [{"at": DAY + " 10:00", "kind": "prompt", "text": "fix the build"}])
    check("later entries leave usage alone", session["usage"],
          {"claude-x": {"input_tokens": 5, "output_tokens": 1, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}})
    check("later entries leave pull requests alone", session["pull_requests"], [])
    check("later entries leave branches alone", session["branches"], ["main"])
    check("a later reply is neither a question nor the last reply", (session["questions"], session["last_reply"]),
          ([], {"at": DAY + " 11:00", "text": "Done."}))


def test_read_session_interrupted():
    # what the person types after an interruption is an entry of its own, so it stays while the marker goes
    session = _session([
        _user("fix the build", at="10:00"),
        _assistant("Working on it.", at="10:01", msg_id="m1"),
        _interrupt(INTERRUPTED, at="10:02"),
        _user("fix the tests instead", at="10:03"),
        _assistant([_tool_use("Bash", "git push")], at="10:04", msg_id="m2"),
        _interrupt(INTERRUPTED_TOOL, at="10:05"),
        _user("do not push yet", at="10:06"),
    ])
    check("interruptions on the day are not prompts, the prompts after them are", session["prompts"],
          [{"at": DAY + " 10:00", "kind": "prompt", "text": "fix the build"},
           {"at": DAY + " 10:03", "kind": "prompt", "text": "fix the tests instead"},
           {"at": DAY + " 10:06", "kind": "prompt", "text": "do not push yet"}])


def test_read_session_only_later():
    check("entries only after the day is None", _session([_user("monday", day=TODAY)]), None)


def test_read_session_cwd():
    # a cd inside the session moves the shell and every later entry's cwd with it, but not where the session lives
    moved = os.path.join(FOLDER, "sub")
    session = _session([
        _user("fix the build", at="10:00", cwd=FOLDER),
        _assistant("Done.", at="10:30", msg_id="m1", cwd=moved),
        _user("and the tests", at="11:00", cwd=moved),
    ])
    check("a later subfolder cwd on the same day keeps the start folder", session["cwd"], FOLDER)

    started = os.path.join(FOLDER, "started")
    resumed = os.path.join(FOLDER, "resumed")
    session = _session([
        _user("thursday", at="09:00", day=PREV, cwd=started),
        _user("friday", at="10:00", cwd=resumed),
    ])
    check("a session resumed on a later day keeps the folder of its first day", session["cwd"], started)

    first = os.path.join(FOLDER, "first")
    second = os.path.join(FOLDER, "second")
    session = _session([
        {"type": "summary", "summary": "an earlier conversation"},
        _user("no folder yet", at="09:00", cwd=None),
        _user("fix the build", at="10:00", cwd=first),
        _user("and the tests", at="11:00", cwd=second),
    ])
    check("leading entries without a cwd are skipped until one has one", session["cwd"], first)

    session = _session([
        _user("empty folder", at="09:00", cwd=""),
        _user("fix the build", at="10:00", cwd=first),
    ])
    check("a leading empty cwd is skipped like a missing one", session["cwd"], first)


# ----- ranges: span_days, read_session and subagent_usage over several days ---------

def test_span_days():
    check("one day is one element", cl.span_days(DAY, DAY), {DAY})
    check("both ends included", cl.span_days(PREV, DAY), {PREV, DAY})
    check("a range across a month boundary is every date in it",
          sorted(cl.span_days("2026-08-30", "2026-09-02")), ["2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02"])


def test_read_session_days():
    session = _session([
        _user("wednesday", day=BEFORE),
        _user("thursday", day=PREV),
        _user("friday"),
        _user("saturday", day=NEXT),
    ], day={PREV, DAY})
    check("prompts on every day of the set, none from outside it", session["prompts"],
          [{"at": PREV + " 10:00", "kind": "prompt", "text": "thursday"},
           {"at": DAY + " 10:00", "kind": "prompt", "text": "friday"}])
    check("days are the dates of the kept entries", session["days"], [PREV, DAY])
    check("first and last span the set's days", (session["first"], session["last"]),
          (PREV + " 10:00", DAY + " 10:00"))


def test_read_session_across_days():
    session = _session([
        _user("fix the build", at="22:00"),
        _assistant("Want me to push?", at="01:30", day=NEXT, msg_id="m1"),
    ], day={DAY, NEXT})
    check("first and last carry their own dates", (session["first"], session["last"]),
          (DAY + " 22:00", NEXT + " 01:30"))
    check("a session over two days lists both", session["days"], [DAY, NEXT])
    check("a question carries its date", session["questions"], [{"at": NEXT + " 01:30", "text": "Want me to push?"}])
    check("the last reply carries its date", session["last_reply"], {"at": NEXT + " 01:30", "text": "Want me to push?"})


def test_subagent_usage_days():
    root = _projects({
        "proj/s1.jsonl": [_user("x")],
        "proj/s1/subagents/a1.jsonl": [
            _assistant("a", msg_id="p1", usage=_usage(3, 1), model="claude-y", day=PREV),
            _assistant("b", msg_id="d1", usage=_usage(5, 2), model="claude-y"),
            _assistant("c", msg_id="t1", usage=_usage(100, 100), model="claude-y", day=TODAY),
        ],
    })
    totals = {}
    cl.subagent_usage(os.path.join(root, "proj", "s1.jsonl"), {PREV, DAY}, totals)
    check("subagent usage from two days of the set summed, a day outside it left out", totals,
          {"claude-y": {"input_tokens": 8, "output_tokens": 3, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}})


# ----- moment, in_runs: the span of a run of this skill ------------------------------

def _at(clock, day=DAY):
    return cl.moment(_ts(day, clock))


def test_moment():
    utc = cl.moment("2026-09-04T10:00:00.000Z")
    check("a Z timestamp is an aware UTC datetime", (utc, utc.utcoffset()),
          (datetime.datetime(2026, 9, 4, 10, 0, tzinfo=datetime.timezone.utc), datetime.timedelta(0)))
    check("an offset timestamp is the same instant", cl.moment("2026-09-04T12:00:00+02:00"), utc)
    naive = cl.moment("2026-09-04T10:00:00")
    check("a timestamp with no offset is read as local time", (naive, naive.tzinfo is not None),
          (datetime.datetime.fromisoformat("2026-09-04T10:00:00").astimezone(), True))
    # an aware and a naive datetime cannot be compared at all, so this would raise if the local reading were missing
    check("a timestamp with no offset compares with an aware one",
          (naive < _at("10:01"), naive > _at("09:59")), (True, True))
    check("unparseable text is None", cl.moment("yesterday"), None)
    check("empty text is None", cl.moment(""), None)
    check("None is None", cl.moment(None), None)


def test_in_runs():
    run = [[_at("10:00"), _at("11:00")]]
    check("no moment is outside every run", cl.in_runs(None, [[_at("10:00"), None]]), False)
    check("the start is inside", cl.in_runs(_at("10:00"), run), True)
    check("a moment between start and end is inside", cl.in_runs(_at("10:30"), run), True)
    check("the end is outside", cl.in_runs(_at("11:00"), run), False)
    check("a moment before the start is outside", cl.in_runs(_at("09:59"), run), False)
    check("a run with no end holds everything from its start on",
          (cl.in_runs(_at("23:59"), [[_at("10:00"), None]]), cl.in_runs(_at("09:59"), [[_at("10:00"), None]])),
          (True, False))
    two = [[_at("10:00"), _at("11:00")], [_at("12:00"), _at("13:00")]]
    check("with several runs a moment in the gap is outside", cl.in_runs(_at("11:30"), two), False)
    check("with several runs a moment in the second is inside", cl.in_runs(_at("12:30"), two), True)
    check("no runs is outside", cl.in_runs(_at("10:30"), []), False)


def _sub(at, tokens, day=DAY):
    """A subagent reply with its own message id, so every entry counts once and its tokens say which counted."""
    return _assistant("sub", at=at, day=day, msg_id="sub-%s-%s" % (day, at), usage=_usage(tokens), model="claude-y")


def _sub_usage(tokens):
    if not tokens:
        return {}
    return {"claude-y": {"input_tokens": tokens, "output_tokens": 0, "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0}}


def test_subagent_usage_runs():
    # each entry's tokens are a power of two, so the sum names exactly the entries that were counted
    root = _projects({
        "proj/s1.jsonl": [_user("x")],
        "proj/s1/subagents/a1.jsonl": [_sub("09:00", 1), _sub("10:00", 2), _sub("10:30", 4), _sub("11:00", 8),
                                       _sub("12:00", 16), _sub("10:30", 32, day=PREV)],
    })
    path = os.path.join(root, "proj", "s1.jsonl")
    totals = {}
    cl.subagent_usage(path, {DAY}, totals, [[_at("10:00"), _at("11:00")]])
    check("a run's start and inside left out, before it, its end and after it counted, another day left out",
          totals, _sub_usage(1 + 8 + 16))
    totals = {}
    cl.subagent_usage(path, {DAY}, totals, [[_at("10:00"), None]])
    check("an open run leaves out everything from its start on", totals, _sub_usage(1))
    totals = {}
    cl.subagent_usage(path, {PREV, DAY}, totals, [[_at("10:00"), None]])
    check("the day set still applies beside an open run", totals, _sub_usage(32 + 1))


# ----- read_session: a run of this skill is skipped ---------------------------------

def test_read_session_remind_me_run():
    session = _session([
        _user("fix the build", at="10:00", branch="main"),
        _assistant("Done.", at="10:01", msg_id="m1", usage=_usage(5, 1)),
        _user("/remind-me --from 2026-09-03", at="10:02"),
        _assistant("Want me to open !4001?", at="10:03", msg_id="m2", usage=_usage(100, 50), branch="run"),
        _assistant([_tool_use("Bash", "az repos pr show --id 4002")], at="10:04", msg_id="m3", usage=_usage(7),
                   branch="run"),
        _user([_tool_result("Created !4003")], at="10:05", branch="run"),
        _interrupt(INTERRUPTED, at="10:06", branch="run"),
        # still inside the run, since neither the tool result nor the interruption is the person typing
        _assistant("Shall I go on? https://github.com/acme/widgets/pull/9", at="10:07", msg_id="m4",
                   usage=_usage(11), branch="run"),
        _user(cl.COMPACTION_PREFIX + ". Pending: push the branch.", at="10:08", branch="run"),
        _assistant("After the summary, see !4004?", at="10:09", msg_id="m5", usage=_usage(9), branch="run"),
        _user("<command-name>/review</command-name><command-args>42</command-args>", at="10:10", branch="next"),
        _assistant("Reviewed.", at="10:11", msg_id="m6", usage=_usage(2, 1), branch="next"),
    ])
    # a compaction summary is not the person, so it neither ends the run nor becomes a prompt
    check("the run's prompt and summary are not prompts, the command after it is", session["prompts"],
          [{"at": DAY + " 10:00", "kind": "prompt", "text": "fix the build"},
           {"at": DAY + " 10:10", "kind": "command", "text": "/review 42"}])
    check("the run's replies are not questions", session["questions"], [])
    check("the last reply is the one after the run", session["last_reply"], {"at": DAY + " 10:11", "text": "Reviewed."})
    check("the run's usage is left out", session["usage"],
          {"claude-x": {"input_tokens": 7, "output_tokens": 2, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}})
    check("pull requests named in the run's results, replies and commands are left out", session["pull_requests"], [])
    check("the run's branch is left out", session["branches"], ["main", "next"])
    check("first and last are the entries around the run", (session["first"], session["last"]),
          (DAY + " 10:00", DAY + " 10:11"))


def test_read_session_remind_me_run_ended_by_prompt():
    session = _session([
        _user("<command-name>/remind-me</command-name>", at="09:00"),
        _assistant("Anything else?", at="09:05", msg_id="m1"),
        _user("fix the build", at="09:30"),
        _assistant("Done.", at="09:40", msg_id="m2"),
    ])
    check("a typed prompt ends the run and is kept", session["prompts"],
          [{"at": DAY + " 09:30", "kind": "prompt", "text": "fix the build"}])
    check("the session starts at the prompt that ended the run", session["first"], DAY + " 09:30")
    check("the run's question is left out", session["questions"], [])
    check("the last reply is after the run", session["last_reply"], {"at": DAY + " 09:40", "text": "Done."})


def test_read_session_remind_me_run_days():
    session = _session([
        _user("fix the build", at="10:00"),
        _user("<command-name>/remind-me</command-name>", at="23:50"),
        _assistant("The report.", at="00:10", day=NEXT, msg_id="m1", usage=_usage(50)),
        _user([_tool_result("ok")], at="00:11", day=NEXT),
        _user("saturday", day=TODAY),
    ], day={DAY, NEXT})
    # the next day holds only the run's entries, and the prompt after them falls outside the set
    check("a day holding only a run is not listed, nor is a day outside the set",
          (session["first"], session["last"], session["days"]), (DAY + " 10:00", DAY + " 10:00", [DAY]))
    check("the run leaves no last reply and no usage", (session["last_reply"], session["usage"]), (None, {}))


def test_read_session_remind_me_run_from_the_day_before():
    for form, invocation in (("typed", "/remind-me"), ("command", "<command-name>/remind-me</command-name>")):
        session = _session([
            # the run starts the evening before the range, so only its spill-over falls inside it
            _user(invocation, at="23:50", day=PREV),
            _assistant("Want me to open !4101?", at="00:05", msg_id="m1", usage=_usage(100, 50), branch="run"),
            _assistant([_tool_use("Bash", "az repos pr show --id 4102")], at="00:06", msg_id="m2", usage=_usage(7),
                       branch="run"),
            _user([_tool_result("Created !4103")], at="00:07", branch="run"),
            _user("fix the build", at="09:00"),
            _assistant("Done.", at="09:05", msg_id="m3", usage=_usage(5, 1)),
        ], day={DAY})
        check("%s: only the prompt that ends the run is a prompt" % form, session["prompts"],
              [{"at": DAY + " 09:00", "kind": "prompt", "text": "fix the build"}])
        check("%s: the run's question on the day is left out" % form, session["questions"], [])
        check("%s: the last reply is the one after the run" % form, session["last_reply"],
              {"at": DAY + " 09:05", "text": "Done."})
        check("%s: the run's usage on the day is left out" % form, session["usage"],
              {"claude-x": {"input_tokens": 5, "output_tokens": 1, "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0}})
        check("%s: pull requests the run named on the day are left out" % form, session["pull_requests"], [])
        check("%s: the run's branch is left out" % form, session["branches"], ["main"])
        check("%s: the session starts at the prompt that ends the run" % form, (session["first"], session["days"]),
              (DAY + " 09:00", [DAY]))


def test_read_session_remind_me_run_from_the_day_before_summary():
    session = _session([
        _user("/remind-me", at="23:50", day=PREV),
        _assistant("Want me to open !4101?", at="00:05", msg_id="m1", usage=_usage(100, 50), branch="run"),
        _user(cl.COMPACTION_PREFIX + ". Pending: push the branch.", at="00:20", branch="run"),
        _assistant("After the summary, shall I go on?", at="00:25", msg_id="m2", usage=_usage(9), branch="run"),
        # no reply follows the prompt, so a reply kept from inside the run would show as the last one
        _user("fix the build", at="09:00"),
    ], day={DAY})
    check("a summary inside a run from the day before is not a prompt", session["prompts"],
          [{"at": DAY + " 09:00", "kind": "prompt", "text": "fix the build"}])
    check("the reply after the summary is not a question", session["questions"], [])
    check("the reply after the summary is not the last reply", session["last_reply"], None)
    check("the reply after the summary is not counted", session["usage"], {})


def test_read_session_remind_me_run_only_from_the_day_before():
    spill_over = [
        _user("/remind-me", at="23:50", day=PREV),
        _assistant("The report. Want me to go on?", at="00:10", msg_id="m1", usage=_usage(50)),
        _user([_tool_result("Created !5101")], at="00:11"),
    ]
    check("a session holding only a run's spill-over in the range is nothing", _session(spill_over, day={DAY}), None)
    digest = _collect_span({"proj/r1.jsonl": spill_over, "proj/k1.jsonl": [_user("kept")]}, DAY, DAY,
                           live=False, today=TODAY)
    check("collect drops it, and its day before still sets the earliest", (_summary(digest), digest["earliest"]),
          ([(FOLDER, False, False, ["k1"])], PREV))


def test_read_session_remind_me_run_ended_before_the_range():
    session = _session([
        _user("/remind-me", at="23:50", day=BEFORE),
        # the command ends the run although it falls outside the range itself
        _user("<command-name>/review</command-name><command-args>42</command-args>", at="23:55", day=BEFORE),
        _assistant("Reviewed.", at="00:05", msg_id="m1", usage=_usage(2, 1)),
    ], day={DAY})
    check("a reply after a run that ended before the range is the last reply", session["last_reply"],
          {"at": DAY + " 00:05", "text": "Reviewed."})
    check("and is counted", session["usage"],
          {"claude-x": {"input_tokens": 2, "output_tokens": 1, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}})
    check("the command that ended the run is outside the range", (session["prompts"], session["first"]),
          ([], DAY + " 00:05"))


# ----- read_session: a run's subagents are left out too ------------------------------

def _session_subagents(records, subagent, day=DAY):
    root = _projects({"proj/s1.jsonl": records, "proj/s1/subagents/a1.jsonl": subagent})
    days = {day} if isinstance(day, str) else set(day)
    return cl.read_session(os.path.join(root, "proj", "s1.jsonl"), days)


def test_read_session_run_subagents_ended():
    for form, ending in (("prompt", "fix the tests"), ("command", "<command-name>/review</command-name>")):
        session = _session_subagents([
            _user("fix the build", at="09:00"),
            _user("/remind-me", at="10:00"),
            _assistant("The report.", at="10:30", msg_id="m1"),
            _user(ending, at="11:00"),
            _assistant("Done.", at="11:30", msg_id="m2"),
        ], [_sub("09:30", 1), _sub("10:00", 2), _sub("10:30", 4), _sub("11:00", 8), _sub("11:30", 16)])
        # the run ends at the entry that ends it, so a subagent reply at that moment is the session's again
        check("ended by a %s: subagent usage from the run's start to its end left out, before and from the end counted"
              % form, session["usage"], _sub_usage(1 + 8 + 16))


def test_read_session_run_subagents_still_going():
    session = _session_subagents([
        _user("fix the build", at="09:00"),
        _user("/remind-me", at="10:00"),
        _assistant("The report.", at="10:30", msg_id="m1"),
    ], [_sub("09:30", 1), _sub("10:30", 4), _sub("23:00", 8)])
    check("a run still going at the end leaves out every later subagent reply", session["usage"], _sub_usage(1))


def test_read_session_run_subagents_from_the_day_before():
    session = _session_subagents([
        _user("/remind-me", at="23:50", day=PREV),
        _assistant("The report.", at="00:05", msg_id="m1"),
        _user("fix the build", at="09:00"),
    ], [_sub("23:55", 4, day=PREV), _sub("00:10", 1), _sub("09:30", 2)])
    # the run started outside the range, and its readers still spill over into it
    check("a run from the evening before leaves out its subagents' spill-over on the day", session["usage"],
          _sub_usage(2))


def test_read_session_run_subagents_second_invocation():
    session = _session_subagents([
        _user("fix the build", at="09:00"),
        _user("/remind-me", at="10:00"),
        _assistant("The report.", at="10:10", msg_id="m1"),
        _user("/remind-me --from 2026-09-03", at="10:20"),
        _user("fix the tests", at="11:00"),
    ], [_sub("09:30", 8), _sub("10:05", 1), _sub("10:30", 2), _sub("11:00", 4)])
    # a second invocation that moved the start would bring back the reply between the two
    check("a second invocation inside a run keeps the first start", session["usage"], _sub_usage(8 + 4))


def test_read_session_run_subagents_two_runs():
    session = _session_subagents([
        _user("/remind-me", at="09:00"),
        _user("fix the build", at="10:00"),
        _user("/remind-me", at="11:00"),
        _user("fix the tests", at="12:00"),
    ], [_sub("09:30", 1), _sub("10:30", 2), _sub("11:30", 4), _sub("12:30", 8)])
    check("two runs both left out, the gap between them and after them counted", session["usage"], _sub_usage(2 + 8))


def test_read_session_run_subagents_not_closed():
    session = _session_subagents([
        _user("fix the build", at="09:00"),
        _user("/remind-me", at="10:00"),
        _user([_tool_result("ok")], at="10:10"),
        _user(cl.COMPACTION_PREFIX + ". Pending: push the branch.", at="10:20"),
        _user("fix the tests", at="11:00"),
    ], [_sub("10:15", 1), _sub("10:25", 2), _sub("11:30", 4)])
    # neither a tool result nor a compaction summary is the person typing, so the run goes on past both
    check("a tool result and a summary inside a run do not close it", session["usage"], _sub_usage(4))


def test_read_session_run_subagents_start_unparseable():
    session = _session_subagents([
        _user("fix the build", at="09:00"),
        _user("/remind-me", at="10:00", timestamp="garbage"),
        _assistant("Want me to open !4001?", at="10:30", msg_id="m1", usage=_usage(100)),
        _user("fix the tests", at="11:00"),
        _assistant("Done.", at="11:30", msg_id="m2"),
    ], [_sub("09:30", 1), _sub("10:30", 2), _sub("11:30", 4)])
    # a run with no start cannot be placed, so its subagents count while its own entries are still skipped
    check("a run whose start does not parse is not tracked for subagents", session["usage"], _sub_usage(1 + 2 + 4))
    check("its main-session entries are still skipped",
          (session["prompts"], session["questions"], session["pull_requests"], session["last_reply"]),
          ([{"at": DAY + " 09:00", "kind": "prompt", "text": "fix the build"},
            {"at": DAY + " 11:00", "kind": "prompt", "text": "fix the tests"}],
           [], [], {"at": DAY + " 11:30", "text": "Done."}))


def test_read_session_run_subagents_end_unparseable():
    session = _session_subagents([
        _user("fix the build", at="09:00"),
        _user("/remind-me", at="10:00"),
        _assistant("The report.", at="10:30", msg_id="m1"),
        _user("fix the tests", at="11:00", timestamp="garbage"),
        _assistant("Done.", at="11:30", msg_id="m2"),
        _user("and the docs", at="12:00"),
    ], [_sub("09:30", 1), _sub("10:30", 2), _sub("11:30", 4), _sub("12:30", 8)])
    # an end that does not parse cannot be placed either, so the run is left open for its subagents
    check("a run whose ending timestamp does not parse stays open for subagents", session["usage"], _sub_usage(1))
    check("while the main session takes up again after the ending prompt",
          (session["prompts"], session["last_reply"]),
          ([{"at": DAY + " 09:00", "kind": "prompt", "text": "fix the build"},
            {"at": DAY + " 12:00", "kind": "prompt", "text": "and the docs"}],
           {"at": DAY + " 11:30", "text": "Done."}))


# ----- collect: which sessions, grouped how --------------------------------------

def test_collect_left_out_and_dropped():
    digest = _collect({
        # `claude -p` runs are scripts and probes
        "proj/h1.jsonl": [_user("probe", entrypoint="sdk-cli")],
        "proj/n1.jsonl": [_assistant("a reply with no prompt")],
        "proj/r1.jsonl": [_user("<command-name>/remind-me</command-name>")],
        "proj/o1.jsonl": [_user("thursday", day=PREV)],
        "proj/k1.jsonl": [_user("kept")],
        "proj/u1.jsonl": [_user("no folder", cwd=None)],
    }, live=False, today=TODAY)
    check("the range is the one day", (digest["from"], digest["to"]), (DAY, DAY))
    check("headless sessions counted", digest["left_out"], {"headless": 1})
    check("only sessions with prompts that day, grouped by folder", _summary(digest),
          [("(unknown folder)", False, False, ["u1"]), (FOLDER, False, False, ["k1"])])
    check("no live lookups when live is off", (digest["pull_requests"], "live" in digest["repositories"][1]),
          ({}, False))


def test_collect_groups_by_repository():
    main_repo = _repo()
    worktree = os.path.join(_tempdir(), "wt")
    _git(main_repo, "worktree", "add", "-q", worktree, "-b", "feature")
    other = _repo()
    sub = os.path.join(other, "sub")
    os.makedirs(sub)
    digest = _collect({
        "proj/a1.jsonl": [_user("in the worktree", at="11:00", cwd=worktree)],
        "proj/a2.jsonl": [_user("in the main tree", at="10:00", cwd=main_repo)],
        "proj/b1.jsonl": [_user("elsewhere", cwd=other)],
    }, here=sub, live=False, today=TODAY)
    # a worktree's common dir is its main repository's .git, so both sessions land in one group
    want = sorted([(_toplevel(main_repo), True, False, ["a2", "a1"]), (_toplevel(other), True, True, ["b1"])])
    check("worktree grouped with its repository, sessions by first entry, current marked", _summary(digest), want)


def test_collect_running_sessions():
    folder = os.path.join(CONFIG, "sessions")
    os.makedirs(folder, exist_ok=True)
    files = {"x.json": json.dumps({"sessionId": "s-busy", "status": "busy"}),
             "y.json": json.dumps({"sessionId": "s-plain"}),
             "z.json": "not json"}
    for name, text in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(text)
    try:
        digest = _collect({"proj/s-busy.jsonl": [_user("a")], "proj/s-plain.jsonl": [_user("b")],
                           "proj/s-gone.jsonl": [_user("c")]}, live=False)
    finally:
        for name in files:
            os.remove(os.path.join(folder, name))
    running = {s["id"]: s["running"] for s in digest["repositories"][0]["sessions"]}
    check("status from the session file, running when it has none, None when no file",
          running, {"s-busy": "busy", "s-plain": "running", "s-gone": None})


def test_collect_default_day():
    files = {"proj/a.jsonl": [_user("thursday", day=PREV)], "proj/b.jsonl": [_user("friday", day=DAY)],
             "proj/c.jsonl": [_user("monday", day=TODAY)]}
    digest = _collect(files, day=None, live=False, today=TODAY)
    check("no day given reports the latest day before today",
          (digest["from"], digest["to"], _summary(digest)), (DAY, DAY, [(FOLDER, False, False, ["b"])]))
    digest = _collect({"proj/c.jsonl": [_user("monday", day=TODAY)]}, day=None, live=False, today=TODAY)
    check("only today has entries: no first day, nothing reported",
          (digest["from"], digest["to"], digest["repositories"]), (None, None, []))


def test_collect_live_pull_requests_new():
    repo = _repo()
    remote = "https://dev.azure.com/acme/proj/_git/widgets"
    _git(repo, "remote", "add", "origin", remote)
    # the author differs in case from the signed-in account, so the comparison has to ignore case
    states = {"1234": {"status": "active", "created": _local(DAY, "09:00"), "author": "ann@EXAMPLE.com"},
              "5678": {"status": "completed", "created": _local(PREV, "09:00"), "author": "ann@EXAMPLE.com"}}

    def answer(argv):
        if argv[:4] == ["az", "repos", "pr", "show"]:
            return True, json.dumps(states[argv[argv.index("--id") + 1]])
        if argv[:3] == ["az", "account", "show"]:
            return True, "Ann@example.com\n"
        if argv[:3] == ["gh", "api", "user"]:
            return True, "ann\n"
        if argv[:3] == ["gh", "pr", "view"]:
            return True, json.dumps({"state": "OPEN", "createdAt": _local(DAY, "12:00"), "author": {"login": "ann"}})
        return False, "not answered by the tests"

    digest, calls = _with_cli(answer, lambda: _collect({"proj/s1.jsonl": [
        _user("go", cwd=repo),
        _assistant("opened !1234, compared with !5678 and https://github.com/acme/widgets/pull/12", cwd=repo)]},
        today=TODAY))
    session = digest["repositories"][0]["sessions"][0]
    check("the organisation comes from the repository's remote",
          sorted(c[c.index("--org") + 1] for c in calls if c[:4] == ["az", "repos", "pr", "show"]),
          ["https://dev.azure.com/acme"] * 2)
    check("every named pull request looked up", sorted(digest["pull_requests"]),
          ["azure:1234", "azure:5678", "github:acme/widgets#12"])
    check("an old pull request keeps its own state", digest["pull_requests"]["azure:5678"]["status"], "completed")
    # the new ones are those the platform says were created that day, by the person
    check("pull_requests_new is the keys created on the day by the person", session["pull_requests_new"],
          ["azure:1234", "github:acme/widgets#12"])
    check("the repository's live state was read", digest["repositories"][0]["live"]["remote"], remote)


def test_collect_default_day_work_days():
    typed = {"proj/a.jsonl": [_user("thursday", day=PREV)]}
    headless = dict(typed, **{"proj/b.jsonl": [_user("probe", day=DAY, entrypoint="sdk-cli")]})
    remind = dict(typed, **{"proj/c.jsonl": [_user("<command-name>/remind-me</command-name>", day=DAY),
                                             _assistant("the report", day=DAY)]})
    digest = _collect(headless, day=None, live=False, today=TODAY)
    check("a later day with only a claude -p run gives way to the earlier day with work",
          (digest["from"], digest["to"], _summary(digest)), (PREV, PREV, [(FOLDER, False, False, ["a"])]))
    digest = _collect(remind, day=None, live=False, today=TODAY)
    check("a later day with only a /remind-me run gives way to the earlier day with work",
          (digest["from"], digest["to"], _summary(digest)), (PREV, PREV, [(FOLDER, False, False, ["a"])]))


def test_collect_live_merged_by():
    widgets = _repo()
    _git(widgets, "remote", "add", "origin", "https://dev.azure.com/acme/proj/_git/widgets")
    # the same branch name in another repository, where a match by name alone would hide its unpushed commit
    gadgets = _repo()
    _git(gadgets, "remote", "add", "origin", "https://dev.azure.com/acme/proj/_git/gadgets")
    tools = _repo()
    _git(tools, "remote", "add", "origin", "https://github.com/acme/tools.git")
    for repo, names in ((widgets, ("feature/x", "feature/active", "feature/abandoned")), (gadgets, ("feature/x",)),
                        (tools, ("gh-merged",))):
        for name in names:
            _git(repo, "branch", name)
    states = {"1001": {"status": "completed", "source": "refs/heads/feature/x", "repository": "widgets"},
              "1002": {"status": "active", "source": "refs/heads/feature/active", "repository": "widgets"},
              "1003": {"status": "abandoned", "source": "refs/heads/feature/abandoned", "repository": "widgets"}}

    def answer(argv):
        if argv[:4] == ["az", "repos", "pr", "show"]:
            return True, json.dumps(states[argv[argv.index("--id") + 1]])
        if argv[:3] == ["gh", "pr", "view"]:
            return True, json.dumps({"state": "MERGED", "headRefName": "gh-merged",
                                     "createdAt": _local(PREV, "09:00"), "author": {"login": "ann"}})
        return False, "not answered by the tests"

    files = {"proj/w1.jsonl": [_user("a", cwd=widgets, branch="feature/x"),
                               _user("b", cwd=widgets, branch="feature/active"),
                               _user("c", cwd=widgets, branch="feature/abandoned"),
                               _assistant("!1001 !1002 !1003", cwd=widgets, branch="feature/x")],
             "proj/g1.jsonl": [_user("d", cwd=gadgets, branch="feature/x")],
             "proj/t1.jsonl": [_user("e", cwd=tools, branch="gh-merged"),
                               _assistant("https://github.com/acme/tools/pull/7", cwd=tools, branch="gh-merged")]}
    digest, _ = _with_cli(answer, lambda: _collect(files, today=TODAY))
    branches = {g["path"]: {b["name"]: b for b in g["live"]["branches"]} for g in digest["repositories"]}
    # each repository holds only its first commit and no remote-tracking ref, so an untouched branch has one unpushed
    check("a branch merged by a completed Azure pull request in its repository",
          branches[_toplevel(widgets)]["feature/x"],
          {"name": "feature/x", "local": True, "on_remote": False, "unpushed": None, "merged_by": "azure:1001"})
    check("a branch matching an active pull request is untouched", branches[_toplevel(widgets)]["feature/active"],
          {"name": "feature/active", "local": True, "on_remote": False, "unpushed": 1})
    check("a branch matching an abandoned pull request is untouched",
          branches[_toplevel(widgets)]["feature/abandoned"],
          {"name": "feature/abandoned", "local": True, "on_remote": False, "unpushed": 1})
    check("the same branch name in a different repository is untouched", branches[_toplevel(gadgets)]["feature/x"],
          {"name": "feature/x", "local": True, "on_remote": False, "unpushed": 1})
    check("a branch merged by a merged GitHub pull request in its repository", branches[_toplevel(tools)]["gh-merged"],
          {"name": "gh-merged", "local": True, "on_remote": False, "unpushed": None,
           "merged_by": "github:acme/tools#7"})


def _collect_new_pull_requests(account):
    """Collect a day naming the person's new pull request, a colleague's, and one that cannot be read,
    with az account show answering account."""
    repo = _repo()
    _git(repo, "remote", "add", "origin", "https://dev.azure.com/acme/proj/_git/widgets")
    states = {"2001": {"status": "active", "created": _local(DAY, "09:00"), "author": "ann@example.com"},
              "2002": {"status": "active", "created": _local(DAY, "10:00"), "author": "colleague@example.com"}}

    def answer(argv):
        if argv[:4] == ["az", "repos", "pr", "show"]:
            ref = argv[argv.index("--id") + 1]
            return (True, json.dumps(states[ref])) if ref in states else (False, "TF401180: not found")
        if argv[:3] == ["az", "account", "show"]:
            return account
        return False, "not answered by the tests"

    digest, _ = _with_cli(answer, lambda: _collect({"proj/s1.jsonl": [
        _user("go", cwd=repo), _assistant("opened !2001, reviewed !2002 and !2003", cwd=repo)]}, today=TODAY))
    return digest


def test_collect_live_pull_requests_new_author():
    digest = _collect_new_pull_requests((True, "ann@example.com\n"))
    check("the identity comes from the signed-in account", digest["identities"], ["ann@example.com"])
    check("the unreadable pull request is unknown", digest["pull_requests"]["azure:2003"]["status"], "unknown")
    check("a colleague's pull request and an unknown one are not new",
          digest["repositories"][0]["sessions"][0]["pull_requests_new"], ["azure:2001"])


def test_collect_live_pull_requests_new_no_identity():
    digest = _collect_new_pull_requests((False, "az: login required"))
    check("no CLI account and no git email is no identity", digest["identities"], [])
    # with nobody to compare against, created that day is all that can be checked
    check("no identity falls back to the day alone, and an unknown one is still not new",
          digest["repositories"][0]["sessions"][0]["pull_requests_new"], ["azure:2001", "azure:2002"])


def test_collect_output_shape():
    digest = _collect({"proj/s1.jsonl": [_user("go")]}, live=False, today=TODAY)
    check("a collected session carries no entrypoint", "entrypoint" in digest["repositories"][0]["sessions"][0], False)
    repo = _repo()
    _git(repo, "config", "user.email", "Ann@Example.com")
    digest, calls = _with_cli(lambda argv: (False, "not answered by the tests"),
                              lambda: _collect({"proj/s1.jsonl": [_user("go", cwd=repo)]}, today=TODAY))
    check("a session on the live path carries no entrypoint",
          "entrypoint" in digest["repositories"][0]["sessions"][0], False)
    # the full set, so a key that was removed cannot come back unnoticed
    check("a session on the live path carries exactly these keys",
          sorted(digest["repositories"][0]["sessions"][0]),
          sorted(["id", "cwd", "branches", "first", "last", "days", "prompts", "questions", "pull_requests",
                  "pull_requests_new", "last_reply", "usage", "running"]))
    check("the digest on the live path carries exactly these keys", sorted(digest),
          sorted(["from", "to", "earliest", "generated", "left_out", "repositories", "pull_requests", "identities"]))
    check("the digest carries identities on the live path", digest["identities"], ["ann@example.com"])
    check("no pull request named, so no CLI asked", calls, [])


# ----- collect over a range of days ---------------------------------------------

def test_collect_range():
    digest = _collect_span({
        "proj/a1.jsonl": [_user("thursday", day=PREV)],
        "proj/b1.jsonl": [_user("friday")],
        "proj/c1.jsonl": [_user("wednesday", day=BEFORE)],
        "proj/d1.jsonl": [_user("saturday", day=NEXT)],
        "proj/e1.jsonl": [_user("thursday night", at="22:00", day=PREV), _user("friday morning", at="09:00")],
        "proj/f1.jsonl": [_user("wednesday", day=BEFORE), _user("saturday", day=NEXT)],
    }, PREV, DAY, live=False, today=TODAY)
    check("the digest's range is the one asked for", (digest["from"], digest["to"]), (PREV, DAY))
    check("sessions on any day of the range kept, those only before or after it dropped", _summary(digest),
          [(FOLDER, False, False, ["a1", "e1", "b1"])])
    spanning = [s for s in digest["repositories"][0]["sessions"] if s["id"] == "e1"][0]
    check("a session over two days of the range keeps the entries of both", spanning["prompts"],
          [{"at": PREV + " 22:00", "kind": "prompt", "text": "thursday night"},
           {"at": DAY + " 09:00", "kind": "prompt", "text": "friday morning"}])
    check("and lists both days", spanning["days"], [PREV, DAY])


def test_collect_earliest():
    kept = {"proj/k1.jsonl": [_user("kept")]}
    digest = _collect(kept, live=False, today=TODAY)
    check("only the day itself is the earliest", digest["earliest"], DAY)
    digest = _collect(dict(kept, **{"proj/o1.jsonl": [_user("monday before", day="2026-08-31")]}),
                      live=False, today=TODAY)
    check("a transcript outside the range still sets the earliest", digest["earliest"], "2026-08-31")
    digest = _collect(dict(kept, **{"proj/h1.jsonl": [_user("probe", day="2026-08-30", entrypoint="sdk-cli"),
                                                      _user("probe", entrypoint="sdk-cli")]}), live=False, today=TODAY)
    check("a headless transcript left out still sets the earliest", (digest["earliest"], digest["left_out"]),
          ("2026-08-30", {"headless": 1}))
    digest = _collect(dict(kept, **{"proj/n1.jsonl": [_assistant("old reply", day="2026-08-29"),
                                                      _assistant("a reply with no prompt")]}), live=False, today=TODAY)
    check("a transcript dropped for having no prompts still sets the earliest", (digest["earliest"], _summary(digest)),
          ("2026-08-29", [(FOLDER, False, False, ["k1"])]))
    # with no day before today holding work, collect returns before it reads a transcript's days
    digest = _collect({"proj/c.jsonl": [_user("monday", day=TODAY)]}, day=None, live=False, today=TODAY)
    check("no default day leaves earliest None", digest["earliest"], None)


def test_collect_remind_me_run_dropped():
    digest = _collect_span({
        "proj/r1.jsonl": [_user("wednesday", day=BEFORE),
                          _user("/remind-me --from 2026-09-02", at="09:00", day=PREV),
                          _assistant("The report. Want me to go on?", at="09:05", day=PREV),
                          _user([_tool_result("Created !5001")], at="09:06")],
        "proj/k1.jsonl": [_user("kept")],
    }, PREV, DAY, live=False, today=TODAY)
    check("a session whose only content in the range is a run is dropped", _summary(digest),
          [(FOLDER, False, False, ["k1"])])


def test_collect_live_pull_requests_new_range():
    repo = _repo()
    _git(repo, "remote", "add", "origin", "https://dev.azure.com/acme/proj/_git/widgets")
    states = {"3001": {"status": "active", "created": _local(PREV, "09:00"), "author": "ann@example.com"},
              "3002": {"status": "active", "created": _local(DAY, "09:00"), "author": "ann@example.com"},
              "3003": {"status": "active", "created": _local(BEFORE, "23:59"), "author": "ann@example.com"}}

    def answer(argv):
        if argv[:4] == ["az", "repos", "pr", "show"]:
            return True, json.dumps(states[argv[argv.index("--id") + 1]])
        if argv[:3] == ["az", "account", "show"]:
            return True, "ann@example.com\n"
        return False, "not answered by the tests"

    digest, _ = _with_cli(answer, lambda: _collect_span({"proj/s1.jsonl": [
        _user("go", day=PREV, cwd=repo), _assistant("opened !3001, compared with !3003", day=PREV, cwd=repo),
        _user("again", cwd=repo), _assistant("opened !3002", cwd=repo)]}, PREV, DAY, today=TODAY))
    # the range runs from PREV, so a pull request created the day before it is old
    check("pull requests created on any day of the range are new, one from the day before it is not",
          digest["repositories"][0]["sessions"][0]["pull_requests_new"], ["azure:3001", "azure:3002"])


# ----- azure_organisation ---------------------------------------------------------

def test_azure_organisation():
    check("https dev.azure.com", cl.azure_organisation("https://dev.azure.com/acme/proj/_git/repo"),
          "https://dev.azure.com/acme")
    check("user@dev.azure.com", cl.azure_organisation("https://acme@dev.azure.com/acme/proj/_git/repo"),
          "https://dev.azure.com/acme")
    check("ssh v3", cl.azure_organisation("git@ssh.dev.azure.com:v3/acme/proj/repo"), "https://dev.azure.com/acme")
    check("visualstudio.com", cl.azure_organisation("https://acme.visualstudio.com/proj/_git/repo"),
          "https://acme.visualstudio.com")
    check("user@visualstudio.com", cl.azure_organisation("https://me@acme.visualstudio.com/proj/_git/repo"),
          "https://acme.visualstudio.com")
    check("a github remote", cl.azure_organisation("https://github.com/acme/widgets.git"), None)
    check("no remote", cl.azure_organisation(None), None)


# ----- pull_request_state: never pending when unread ------------------------------

def test_pull_request_state_azure():
    state, calls = _with_cli(lambda argv: (True, "unused"), lambda: cl.pull_request_state("azure:1", []))
    check("no organisations is unknown without a lookup",
          (state, calls), ({"status": "unknown",
                            "reason": "no Azure DevOps repository in the digest to name the organisation"}, []))

    def second_answers(argv):
        if argv[argv.index("--org") + 1] == "https://dev.azure.com/one":
            return False, "TF401180: not found"
        return True, json.dumps({"status": "active", "title": "t"})
    state, calls = _with_cli(second_answers, lambda: cl.pull_request_state(
        "azure:42", ["https://dev.azure.com/one", "https://dev.azure.com/two"]))
    check("the organisation that answers gives the state", state, {"status": "active", "title": "t"})
    check("each organisation asked for the id in turn",
          [(c[:5], c[c.index("--org") + 1]) for c in calls],
          [(["az", "repos", "pr", "show", "--id"], "https://dev.azure.com/one"),
           (["az", "repos", "pr", "show", "--id"], "https://dev.azure.com/two")])
    check("the id passed", [c[5] for c in calls], ["42", "42"])

    state, _ = _with_cli(lambda argv: (False, "az: login required"), lambda: cl.pull_request_state(
        "azure:42", ["https://dev.azure.com/one", "https://dev.azure.com/two"]))
    check("every organisation failing is unknown with the last reason",
          state, {"status": "unknown", "reason": "az: login required"})

    state, _ = _with_cli(lambda argv: (True, "<html>sign in</html>"),
                         lambda: cl.pull_request_state("azure:42", ["https://dev.azure.com/one"]))
    check("an unreadable answer is unknown", state, {"status": "unknown", "reason": "unreadable answer"})

    state, _ = _with_cli(lambda argv: (True, json.dumps({"title": "t"})),
                         lambda: cl.pull_request_state("azure:42", ["https://dev.azure.com/one"]))
    check("an answer with no status is unknown", state, {"title": "t", "status": "unknown"})


def test_pull_request_state_github():
    answer = {"state": "MERGED", "title": "t", "createdAt": "2026-09-04T10:00:00Z", "author": {"login": "ann"},
              "headRefName": "feature", "url": "https://github.com/acme/widgets/pull/12"}
    state, calls = _with_cli(lambda argv: (True, json.dumps(answer)),
                             lambda: cl.pull_request_state("github:acme/widgets#12", ["https://dev.azure.com/one"]))
    check("state lower-cased into status, createdAt to created, author to its login, headRefName to source",
          state, {"status": "merged", "title": "t", "created": "2026-09-04T10:00:00Z", "author": "ann",
                  "source": "feature", "url": "https://github.com/acme/widgets/pull/12"})
    check("gh asked for the number in its repository", [c[:6] for c in calls],
          [["gh", "pr", "view", "12", "-R", "acme/widgets"]])

    state, _ = _with_cli(lambda argv: (False, "gh: Could not resolve to a PullRequest"),
                         lambda: cl.pull_request_state("github:acme/widgets#12", []))
    check("a failed lookup is unknown with its reason",
          state, {"status": "unknown", "reason": "gh: Could not resolve to a PullRequest"})

    state, _ = _with_cli(lambda argv: (True, ""), lambda: cl.pull_request_state("github:acme/widgets#12", []))
    check("an empty answer is unknown", state, {"status": "unknown", "reason": "unreadable answer"})


def test_pull_request_state_azure_source_and_author():
    answer = {"status": "completed", "source": "refs/heads/feature/x", "author": "ann@example.com",
              "repository": "widgets"}
    state, calls = _with_cli(lambda argv: (True, json.dumps(answer)),
                             lambda: cl.pull_request_state("azure:42", ["https://dev.azure.com/one"]))
    query = calls[0][calls[0].index("--query") + 1]
    check("the query asks for the source branch and the author",
          [field for field in query.strip("{}").split(",") if field.split(":")[0] in ("source", "author")],
          ["source:sourceRefName", "author:createdBy.uniqueName"])
    check("refs/heads/ dropped from an Azure source", state,
          {"status": "completed", "source": "feature/x", "author": "ann@example.com", "repository": "widgets"})


# ----- same_repository -------------------------------------------------------------

def test_same_repository_azure():
    state = {"repository": "Widgets"}
    check("https _git/<name>, ignoring case",
          cl.same_repository("azure:1", state, "https://dev.azure.com/acme/proj/_git/WIDGETS"), True)
    check("ssh v3", cl.same_repository("azure:1", state, "git@ssh.dev.azure.com:v3/acme/proj/widgets"), True)
    check("a trailing slash", cl.same_repository("azure:1", state, "https://dev.azure.com/acme/proj/_git/widgets/"),
          True)
    check("a URL-encoded name", cl.same_repository(
        "azure:1", {"repository": "My Repo"}, "https://dev.azure.com/acme/proj/_git/My%20Repo"), True)
    check("another repository", cl.same_repository("azure:1", state, "https://dev.azure.com/acme/proj/_git/gadgets"),
          False)
    check("no repository name", cl.same_repository("azure:1", {}, "https://dev.azure.com/acme/proj/_git/widgets"),
          False)
    check("no remote", cl.same_repository("azure:1", state, None), False)
    # both missing leave an empty name against an empty last path segment, which only the name guard refuses
    check("no repository name and no remote", cl.same_repository("azure:1", {}, None), False)


def test_same_repository_github():
    key = "github:acme/widgets#12"
    check("https with .git", cl.same_repository(key, {}, "https://github.com/acme/widgets.git"), True)
    check("scp ssh", cl.same_repository(key, {}, "git@github.com:acme/widgets.git"), True)
    check("owner and repository ignore case", cl.same_repository("github:Acme/Widgets#12", {},
                                                                 "https://github.com/acme/widgets"), True)
    check("a different owner", cl.same_repository(key, {}, "https://github.com/other/widgets.git"), False)
    check("no remote", cl.same_repository(key, {}, None), False)


def test_created_in():
    check("created on a local day in the set", cl.created_in({"created": _local(DAY, "23:59")}, {DAY}), True)
    check("created on a day outside the set", cl.created_in({"created": _local(PREV, "23:59")}, {DAY}), False)
    check("no created", cl.created_in({"status": "unknown", "reason": "x"}, {DAY}), False)
    check("created on any day of a range", cl.created_in({"created": _local(PREV, "09:00")}, {PREV, DAY}), True)
    check("created after the range", cl.created_in({"created": _local(TODAY, "09:00")}, {PREV, DAY}), False)


# ----- live_state against a real repository -----------------------------------------

def test_live_state():
    repo = _repo()
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", "https://dev.azure.com/acme/proj/_git/widgets")
    # origin/main is set by hand at the first commit, so nothing is ever pushed or fetched
    _git(repo, "update-ref", "refs/remotes/origin/main", first)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "third")
    _git(repo, "branch", "local-only")
    for name in ("a.txt", "b.txt"):
        with open(os.path.join(repo, name), "w") as f:
            f.write("x")
    state = cl.live_state(repo, [{"branches": ["main", "local-only"]}, {"branches": ["main", "gone"]}])
    check("live state of the repository", state,
          {"uncommitted": 2, "notes": [], "current_branch": "main",
           "remote": "https://dev.azure.com/acme/proj/_git/widgets", "email": None,
           "branches": [{"name": "main", "local": True, "on_remote": True, "unpushed": 2},
                        {"name": "local-only", "local": True, "on_remote": False, "unpushed": 2},
                        {"name": "gone", "local": False}]})


def test_live_state_not_a_repository():
    state = cl.live_state(_tempdir(), [{"branches": ["main"]}])
    check("nothing read is None, not zero",
          (state["uncommitted"], state["current_branch"], state["remote"]), (None, None, None))
    check("the failure is noted", [note.startswith("git status failed: ") for note in state["notes"]], [True])
    check("a branch that cannot be read is not local", state["branches"], [{"name": "main", "local": False}])


def test_live_state_remote_and_email():
    repo = _repo()
    _git(repo, "remote", "add", "origin", "https://user:token@dev.azure.com/acme/proj/_git/widgets")
    _git(repo, "config", "user.email", "Ann@Example.com")
    state = cl.live_state(repo, [])
    check("credentials dropped from an https remote", state["remote"], "https://dev.azure.com/acme/proj/_git/widgets")
    check("email from the repository's own git config, as written", state["email"], "Ann@Example.com")
    ssh = _repo()
    _git(ssh, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    check("an scp-form ssh remote unchanged", cl.live_state(ssh, [])["remote"], "git@github.com:acme/widgets.git")


def test_live_state_unpushed_other_remote():
    repo = _repo()
    _git(repo, "branch", "feature")
    # the branch's commit sits on a fork's ref and not on origin/feature, which an origin/feature..feature range would miss
    _git(repo, "update-ref", "refs/remotes/fork/feature", _git(repo, "rev-parse", "HEAD"))
    state = cl.live_state(repo, [{"branches": ["feature"]}])
    check("commits on any remote-tracking ref are pushed", state["branches"],
          [{"name": "feature", "local": True, "on_remote": False, "unpushed": 0}])


# ----- identities ------------------------------------------------------------------

_AZ_ACCOUNT = ["az", "account", "show", "--query", "user.name", "-o", "tsv"]
_GH_USER = ["gh", "api", "user", "--jq", ".login"]


def _identities(keys, answer):
    digest = {"repositories": [{"live": {"email": "Ann@Example.COM"}}, {"live": {"email": "bob@example.com"}},
                               {"live": {"email": None}}, {"path": "no live state"}],
              "pull_requests": dict.fromkeys(keys, {})}
    return _with_cli(answer, lambda: cl.identities(digest))


def test_identities():
    def signed_in(argv):
        if argv[:3] == ["az", "account", "show"]:
            return True, "ANN@example.com\n"
        if argv[:3] == ["gh", "api", "user"]:
            return True, "Ann\n"
        return False, "not answered by the tests"

    both = ["azure:1", "github:acme/widgets#2"]
    check("git emails and both accounts, lowercased, de-duplicated and sorted",
          _identities(both, signed_in), (["ann", "ann@example.com", "bob@example.com"], [_AZ_ACCOUNT, _GH_USER]))
    check("only an azure key asks only az",
          _identities(["azure:1"], signed_in), (["ann@example.com", "bob@example.com"], [_AZ_ACCOUNT]))
    check("only a github key asks only gh",
          _identities(["github:acme/widgets#2"], signed_in), (["ann", "ann@example.com", "bob@example.com"], [_GH_USER]))
    check("no pull request asks neither", _identities([], signed_in), (["ann@example.com", "bob@example.com"], []))
    check("a failed CLI adds nothing", _identities(both, lambda argv: (False, "login required")),
          (["ann@example.com", "bob@example.com"], [_AZ_ACCOUNT, _GH_USER]))
    check("an empty CLI answer adds nothing", _identities(both, lambda argv: (True, "  \n")),
          (["ann@example.com", "bob@example.com"], [_AZ_ACCOUNT, _GH_USER]))


# ----- split: one digest per repository ---------------------------------------------

def _split_group(path, *sessions):
    return {"path": path, "is_repository": False, "current": False,
            "sessions": [{"id": "%s-%d" % (os.path.basename(path.rstrip("\\/")), n), "pull_requests": keys}
                         for n, keys in enumerate(sessions)]}


def test_split():
    widgets = _split_group(os.path.join("src", "My Widgets") + os.sep,
                           ["azure:1", "azure:2"], ["github:acme/widgets#3"])
    # a non-ASCII prompt makes the byte count differ from the character count
    widgets["sessions"][0]["prompts"] = [{"text": "\u7e41\u9ad4 prompt"}]
    groups = [widgets,
              _split_group("(unknown folder)", ["azure:2"]),
              _split_group(os.path.join("x", "widgets"), []),
              _split_group(os.path.join("y", "Widgets"), []),
              _split_group(os.path.join("z", "WIDGETS"), []),
              _split_group(os.path.join("w", "!!!"), []),
              _split_group(os.path.join("v", "-.Dot  Name@@v2.-"), [])]
    states = {"azure:1": {"status": "active"}, "azure:2": {"status": "completed"},
              "github:acme/widgets#3": {"status": "merged"}, "azure:9": {"status": "unknown", "reason": "x"}}
    shared = {"from": PREV, "to": DAY, "earliest": BEFORE, "generated": "2026-09-07T08:00:00+02:00",
              "left_out": {"headless": 2}, "identities": ["ann@example.com"]}
    digest = dict(shared, repositories=groups, pull_requests=states)
    # split names the caller's groups in place, so the expected files are built from copies taken before the call
    before = [dict(group) for group in groups]
    folder = os.path.join(_tempdir(), "split", "deep")
    written = cl.split(digest, folder)

    names = ["my-widgets.json", "unknown-folder.json", "widgets.json", "widgets-2.json", "widgets-3.json",
             "repository.json", "dot-name-v2.json"]
    check("one file per repository group, named from its folder, in the digest's order",
          [name for name, _ in written], names)
    check("each group of the whole digest names its own file, in the digest's order",
          [group.get("split") for group in digest["repositories"]], names)
    check("the folder was created and holds only those files", sorted(os.listdir(folder)), sorted(names))
    texts = {}
    for name in names:
        with open(os.path.join(folder, name), "rb") as f:
            texts[name] = f.read()
    check("each returned count is its file's length in bytes", [size for _, size in written],
          [len(texts[name]) for name in names])
    check("no file holds a carriage return", [name for name in names if b"\r" in texts[name]], [])

    # a key named by two groups is in both files, and a key no group names is in none
    pull_requests = [{"azure:1": states["azure:1"], "azure:2": states["azure:2"],
                      "github:acme/widgets#3": states["github:acme/widgets#3"]},
                     {"azure:2": states["azure:2"]}, {}, {}, {}, {}, {}]
    for name, group, keys in zip(names, before, pull_requests):
        check("%s keeps the digest's range and identities, its own group naming itself and its own pull requests" % name,
              json.loads(texts[name].decode("utf-8")),
              dict(shared, repositories=[dict(group, split=name)], pull_requests=keys))


# ----- name_splits: the file name each repository's digest is written to ----------

def _names(*paths):
    """A digest holding one session-less group per path, in the order given."""
    return {"repositories": [_split_group(path) for path in paths]}


def test_name_splits():
    digest = _names(os.path.join("x", "widgets"), os.path.join("y", "Gadgets"), os.path.join("z", "widgets"))
    check("name_splits names the groups in place and returns nothing", cl.name_splits(digest), None)
    check("every group is named from its folder, a repeat suffixed, in the digest's order",
          [group["split"] for group in digest["repositories"]], ["widgets.json", "gadgets.json", "widgets-2.json"])
    # a second call must not count the first call's names as taken
    cl.name_splits(digest)
    check("naming the same digest twice gives the same names",
          [group["split"] for group in digest["repositories"]], ["widgets.json", "gadgets.json", "widgets-2.json"])

    empty = {"repositories": [], "pull_requests": {}}
    cl.name_splits(empty)
    check("a digest with no repositories is left as it was", empty, {"repositories": [], "pull_requests": {}})

    stale = _names(os.path.join("x", "alpha"), os.path.join("y", "beta"))
    stale["repositories"][0]["split"] = "beta.json"
    stale["repositories"][1]["split"] = "old.json"
    cl.name_splits(stale)
    check("a name already on a group is replaced by its own folder's",
          [group["split"] for group in stale["repositories"]], ["alpha.json", "beta.json"])


def test_name_splits_suffix_collision():
    # a folder already named like a generated suffix takes that name first, so the repeat has to skip it
    for paths, want in (((("x", "widgets"), ("y", "widgets"), ("z", "widgets-2")),
                         ["widgets.json", "widgets-2.json", "widgets-2-2.json"]),
                        ((("x", "widgets-2"), ("y", "widgets"), ("z", "widgets")),
                         ["widgets-2.json", "widgets.json", "widgets-3.json"])):
        digest = _names(*[os.path.join(*parts) for parts in paths])
        cl.name_splits(digest)
        label = ", ".join(parts[1] for parts in paths)
        check("%s are named %s" % (label, ", ".join(want)), [group["split"] for group in digest["repositories"]], want)


# ----- main: exit status and output ---------------------------------------------

def _main(argv, today=None):
    """Run main in-process from a non-repository folder with stdout and stderr captured, today passed through.
    stdout must be a real text wrapper, because main reconfigures it to UTF-8."""
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")
    err = io.StringIO()
    saved = sys.stdout, sys.stderr, os.getcwd()
    sys.stdout, sys.stderr = out, err
    os.chdir(_tempdir())
    try:
        try:
            code = cl.main(argv, today)
        except SystemExit as exc:
            code = exc.code
        out.flush()
    finally:
        sys.stdout, sys.stderr = saved[0], saved[1]
        os.chdir(saved[2])
    return code, raw.getvalue().decode("utf-8"), err.getvalue()


def test_main_no_sessions():
    # CLAUDE_CONFIG_DIR holds no projects folder, so nothing is found
    code, out, err = _main(["--from", DAY, "--to", DAY, "--no-live"], today=datetime.date.fromisoformat(TODAY))
    check("EXIT_NO_SESSIONS", cl.EXIT_NO_SESSIONS, 3)
    check("no session exits 3", code, 3)
    doc = json.loads(out)
    check("the digest is still printed", ((doc["from"], doc["to"]), doc["repositories"]), ((DAY, DAY), []))
    check("stderr names the day", err, "no sessions on %s\n" % DAY)

    code, _, err = _main(["--no-live"])
    check("no day and no session exits 3", code, 3)
    check("stderr says any day before today", err, "no sessions on any day before today\n")


def test_main_date_format():
    # fromisoformat accepts the basic and the week-date forms, and those must be refused too
    today = datetime.date.fromisoformat(TODAY)
    for day in ("yesterday", "2026-13-01", "20260904", "2026-W36-5"):
        code, out, err = _main(["--from", day, "--no-live"], today=today)
        check("--from %s exits 2" % day, code, 2)
        check("--from %s explains the format" % day, err.rstrip().endswith("--from must be YYYY-MM-DD"), True)
        check("--from %s prints no digest" % day, out, "")
        # a valid --from leaves only --to to be refused
        code, out, err = _main(["--from", DAY, "--to", day, "--no-live"], today=today)
        check("--to %s exits 2" % day, code, 2)
        check("--to %s explains the format" % day, err.rstrip().endswith("--to must be YYYY-MM-DD"), True)
        check("--to %s prints no digest" % day, out, "")


def test_main_range_validation():
    today = datetime.date.fromisoformat(TODAY)
    too_long = "a range holds at most 31 days, both ends included"
    # 2026-08-07 to TODAY is 32 days, both ends included
    refusals = ((["--to", DAY], "--to needs --from"),
                (["--from", DAY, "--to", PREV], "--to must not be before --from"),
                (["--from", DAY, "--to", "2026-09-08"], "--to must not be after today"),
                (["--from", "2026-08-07", "--to", TODAY], too_long),
                (["--from", "2026-08-07"], too_long))
    for argv, reason in refusals:
        label = " ".join(argv)
        code, out, err = _main(argv + ["--no-live"], today=today)
        check("%s exits 2" % label, code, 2)
        check("%s says %s" % (label, reason), err.rstrip().endswith(reason), True)
        check("%s prints no digest" % label, out, "")

    code, out, err = _main(["--day", DAY, "--no-live"], today=today)
    check("--day exits 2", code, 2)
    check("--day is not a recognised argument", "unrecognized arguments: --day" in err, True)
    check("--day prints no digest", out, "")

    # CLAUDE_CONFIG_DIR holds no projects folder, so an accepted range exits 3 with its digest printed
    for argv, want in ((["--from", "2026-08-08", "--to", TODAY], ("2026-08-08", TODAY)),
                       (["--from", "2026-08-08"], ("2026-08-08", TODAY)),
                       (["--from", DAY], (DAY, TODAY))):
        label = " ".join(argv)
        code, out, _ = _main(argv + ["--no-live"], today=today)
        doc = json.loads(out) if out else {}
        check("%s is accepted, its range ending as given or today" % label, (code, doc.get("from"), doc.get("to")),
              (3,) + want)


def test_main_default_day_today():
    config = _tempdir()
    # a work day after the injected today would be the default if main read the real date
    projects = _projects({"proj/a.jsonl": [_user("thursday", day=PREV)], "proj/b.jsonl": [_user("friday")],
                          "proj/c.jsonl": [_user("tuesday", day="2026-09-08")]})
    os.rename(projects, os.path.join(config, "projects"))
    os.environ["CLAUDE_CONFIG_DIR"] = config
    try:
        code, out, err = _main(["--no-live"], today=datetime.date.fromisoformat(TODAY))
    finally:
        os.environ["CLAUDE_CONFIG_DIR"] = CONFIG
    doc = json.loads(out)
    check("no flags is the latest work day before the injected today", (code, doc["from"], doc["to"]), (0, DAY, DAY))
    check("only that day's session is kept", [s["id"] for g in doc["repositories"] for s in g["sessions"]], ["b"])
    check("nothing on stderr", err, "")


def test_main_progress_line():
    today = datetime.date.fromisoformat(TODAY)
    path = os.path.join(_tempdir(), "out", "digest.json")
    for argv, said, want in ((["--from", DAY, "--to", DAY], DAY, (DAY, DAY)),
                             (["--from", PREV, "--to", DAY], PREV + " to " + DAY, (PREV, DAY)),
                             ([], "any day before today", (None, None))):
        label = " ".join(argv) or "no flags"
        code, out, err = _main(argv + ["--out", path, "--no-live"], today=today)
        check("%s with --out exits 3" % label, code, 3)
        check("%s: the progress line names %s" % (label, said),
              out.startswith(said + ": 0 sessions in 0 folders, 0 headless runs left out, "), True)
        check("%s: stderr names %s" % (label, said), err, "no sessions on %s\n" % said)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        check("%s: the digest is written to --out" % label, (doc["from"], doc["to"]), want)


def test_main_split_needs_out():
    today = datetime.date.fromisoformat(TODAY)
    folder = os.path.join(_tempdir(), "split")
    for argv in (["--from", DAY, "--to", DAY], []):
        label = " ".join(argv) or "no range"
        code, out, err = _main(argv + ["--split", folder, "--no-live"], today=today)
        check("%s: --split without --out exits 2" % label, code, 2)
        check("%s: --split without --out says so" % label, err.rstrip().endswith("--split needs --out"), True)
        check("%s: --split without --out prints nothing" % label, out, "")
        check("%s: --split without --out writes no folder" % label, os.path.exists(folder), False)


def test_main_split():
    config = _tempdir()
    parent = _tempdir()
    alpha, beta = os.path.join(parent, "Alpha"), os.path.join(parent, "beta")
    os.makedirs(alpha)
    os.makedirs(beta)
    projects = _projects({"proj/a.jsonl": [_user("alpha work", cwd=alpha)],
                          "proj/b.jsonl": [_user("beta work", cwd=beta)]})
    os.rename(projects, os.path.join(config, "projects"))
    path = os.path.join(_tempdir(), "out", "digest.json")
    folder = os.path.join(_tempdir(), "split")
    os.environ["CLAUDE_CONFIG_DIR"] = config
    try:
        code, out, err = _main(["--from", DAY, "--to", DAY, "--out", path, "--split", folder, "--no-live"],
                               today=datetime.date.fromisoformat(TODAY))
    finally:
        os.environ["CLAUDE_CONFIG_DIR"] = CONFIG
    with open(path, "rb") as f:
        raw = f.read()
    doc = json.loads(raw.decode("utf-8"))
    check("--split with sessions exits 0 with nothing on stderr", (code, err), (0, ""))
    check("the digest's order is the folders' order", [g["path"] for g in doc["repositories"]], [alpha, beta])
    # the digest is written with LF, so the byte count the progress line names is the file's size
    check("the --out digest holds no carriage return", b"\r" in raw, False)
    sizes = [os.path.getsize(os.path.join(folder, name)) for name in ("alpha.json", "beta.json")]
    check("the progress line, then one line per file in the digest's order", out.splitlines(),
          ["%s: 2 sessions in 2 folders, 0 headless runs left out, %d bytes -> %s" % (DAY, len(raw), path),
           "  %d bytes -> %s" % (sizes[0], os.path.join(folder, "alpha.json")),
           "  %d bytes -> %s" % (sizes[1], os.path.join(folder, "beta.json"))])
    ids = []
    for name in ("alpha.json", "beta.json"):
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            ids.append([s["id"] for g in json.load(f)["repositories"] for s in g["sessions"]])
    check("each split file holds its own folder's session", ids, [["a"], ["b"]])


def test_main_split_no_sessions():
    # CLAUDE_CONFIG_DIR holds no projects folder, so nothing is found
    path = os.path.join(_tempdir(), "out", "digest.json")
    folder = os.path.join(_tempdir(), "split")
    code, out, err = _main(["--from", DAY, "--to", DAY, "--out", path, "--split", folder, "--no-live"],
                           today=datetime.date.fromisoformat(TODAY))
    check("--split with no sessions exits 3", code, 3)
    check("only the progress line is printed", out.splitlines(),
          ["%s: 0 sessions in 0 folders, 0 headless runs left out, %d bytes -> %s"
           % (DAY, os.path.getsize(path), path)])
    check("no split file is written", os.listdir(folder) if os.path.isdir(folder) else [], [])
    check("stderr names the day", err, "no sessions on %s\n" % DAY)


def _main_sessions(cwds, argv):
    """Run main on DAY over one session per folder in cwds, named a, b, c... in that order."""
    config = _tempdir()
    for cwd in cwds:
        os.makedirs(cwd)
    files = {"proj/%s.jsonl" % chr(ord("a") + n): [_user("work", cwd=cwd)] for n, cwd in enumerate(cwds)}
    os.rename(_projects(files), os.path.join(config, "projects"))
    os.environ["CLAUDE_CONFIG_DIR"] = config
    try:
        return _main(["--from", DAY, "--to", DAY, "--no-live"] + argv, today=datetime.date.fromisoformat(TODAY))
    finally:
        os.environ["CLAUDE_CONFIG_DIR"] = CONFIG


def _split_targets(out):
    """The paths the progress lines after the first one say a split file was written to."""
    return [line.split(" -> ", 1)[1] for line in out.splitlines()[1:]]


def _split_sessions(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as f:
        part = json.load(f)
    return [s["id"] for g in part["repositories"] for s in g["sessions"]], [g.get("split") for g in part["repositories"]]


def test_main_split_names_shared():
    parent = _tempdir()
    # the digest sorts by path, and an upper-case letter sorts before a lower-case one
    cwds = [os.path.join(parent, name) for name in ("Alpha", "My Widgets", "beta")]
    path = os.path.join(_tempdir(), "out", "digest.json")
    folder = os.path.join(_tempdir(), "split")
    code, out, err = _main_sessions(cwds, ["--out", path, "--split", folder])
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    named = [g.get("split") for g in doc["repositories"]]
    check("--split with sessions exits 0 with nothing on stderr", (code, err), (0, ""))
    check("fixture: the digest's order is the folders' order", [g["path"] for g in doc["repositories"]], cwds)
    check("the whole digest names each group's file, in the digest's order", named,
          ["alpha.json", "my-widgets.json", "beta.json"])
    check("the split folder holds exactly the files the whole digest names", sorted(os.listdir(folder)), sorted(named))
    check("the progress lines name the files the whole digest names, in its order", _split_targets(out),
          [os.path.join(folder, name) for name in named])
    check("each split file holds its own session, its group naming that file",
          [_split_sessions(folder, name) for name in named],
          [(["a"], ["alpha.json"]), (["b"], ["my-widgets.json"]), (["c"], ["beta.json"])])


def test_main_split_repeated_names():
    # two folders with one name in different parents, which would otherwise share one split file
    parent = _tempdir()
    cwds = [os.path.join(parent, "a", "tundra"), os.path.join(parent, "b", "Tundra")]
    path = os.path.join(_tempdir(), "out", "digest.json")
    folder = os.path.join(_tempdir(), "split")
    code, out, err = _main_sessions(cwds, ["--out", path, "--split", folder])
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    check("--split with sessions exits 0 with nothing on stderr", (code, err), (0, ""))
    check("fixture: the digest's order is the folders' order", [g["path"] for g in doc["repositories"]], cwds)
    check("the whole digest names tundra.json, then tundra-2.json", [g.get("split") for g in doc["repositories"]],
          ["tundra.json", "tundra-2.json"])
    check("the split folder holds both files", sorted(os.listdir(folder)), ["tundra-2.json", "tundra.json"])
    check("each split file holds its own session",
          [_split_sessions(folder, name)[0] for name in ("tundra.json", "tundra-2.json")], [["a"], ["b"]])
    check("the progress lines name the same two files, in the digest's order", _split_targets(out),
          [os.path.join(folder, "tundra.json"), os.path.join(folder, "tundra-2.json")])


def test_main_no_split_no_names():
    parent = _tempdir()
    path = os.path.join(_tempdir(), "out", "digest.json")
    code, _, err = _main_sessions([os.path.join(parent, "alpha")], ["--out", path])
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    check("--out without --split exits 0 with nothing on stderr", (code, err), (0, ""))
    check("--out without --split: no group of the digest carries a split name",
          [("split" in g, len(g["sessions"])) for g in doc["repositories"]], [(False, 1)])

    code, out, err = _main_sessions([os.path.join(_tempdir(), "beta")], [])
    doc = json.loads(out)
    check("stdout without --split exits 0 with nothing on stderr", (code, err), (0, ""))
    check("stdout without --split: no group of the digest carries a split name",
          [("split" in g, len(g["sessions"])) for g in doc["repositories"]], [(False, 1)])


def test_non_ascii_on_cp1252_console():
    text = "\u7e41\u9ad4\u4e2d\u6587 prompt"
    config = _tempdir()
    projects = _projects({"proj/s1.jsonl": [_user(text)]})
    os.rename(projects, os.path.join(config, "projects"))
    env = dict(os.environ, PYTHONIOENCODING="cp1252", CLAUDE_CONFIG_DIR=config)
    proc = subprocess.run([sys.executable, _SCRIPT, "--from", DAY, "--to", DAY, "--no-live"], capture_output=True,
                          env=env, cwd=_tempdir(), timeout=60)
    check("subprocess exits 0", proc.returncode, 0)
    check("subprocess stderr empty", proc.stderr, b"")
    doc = json.loads(proc.stdout.decode("utf-8"))
    check("non-ASCII prompt survives as UTF-8",
          [p["text"] for g in doc["repositories"] for s in g["sessions"] for p in s["prompts"]], [text])


# ----- openable: a transcript past MAX_PATH ----------------------------------------

def _openable_as(name, full, path):
    """openable(path) with os.name as name and every absolute form as full, so any OS can play Windows."""
    saved = os.name, os.path.abspath
    os.name = name
    os.path.abspath = lambda p: full
    try:
        return cl.openable(path)
    finally:
        os.name, os.path.abspath = saved


def test_openable():
    # by hand: "C:\" is 3 characters, so these absolute forms are exactly 260 and 259 long
    at_limit = "C:\\" + "x" * 257
    under = "C:\\" + "x" * 256
    check("on Windows an absolute form of 260 characters is the extended-length form of it",
          _openable_as("nt", at_limit, "s1.jsonl"), "\\\\?\\" + at_limit)
    check("on Windows an absolute form of 259 characters leaves the path unchanged",
          _openable_as("nt", under, "s1.jsonl"), "s1.jsonl")
    extended = "\\\\?\\" + at_limit
    check("on Windows a path already in the extended-length form is unchanged",
          _openable_as("nt", extended, extended), extended)
    check("off Windows a path of any length is unchanged", _openable_as("posix", "/" + "x" * 400, "s1.jsonl"),
          "s1.jsonl")
    if os.name == "nt":
        deep = os.path.join(_tempdir(), "p" * 255)
        check("on this Windows a real path past 260 characters gets the prefix", cl.openable(deep), "\\\\?\\" + deep)
        short = os.path.join(_tempdir(), "s1.jsonl")
        check("on this Windows a real short path is unchanged", cl.openable(short), short)


def _or_raised(body):
    try:
        return body()
    except OSError as exc:
        return "raised " + type(exc).__name__


def test_openable_used_by_readers():
    # every reader is handed a path that does not exist and only openable turns it into the real one,
    # so a reader that opens the path it was given raises instead
    root = _projects({"proj/s1.jsonl": [_user("fix the build"), _assistant("Done.", msg_id="m1", usage=_usage(10, 5))],
                      "proj/s1/subagents/a1.jsonl": [_assistant("sub", msg_id="sa1", usage=_usage(3),
                                                                model="claude-y")]})
    ghost = os.path.join(root, "ghost", "s1.jsonl")
    to_real = {ghost: os.path.join(root, "proj", "s1.jsonl"),
               os.path.join(root, "ghost", "s1", "subagents"): os.path.join(root, "proj", "s1", "subagents")}
    saved = cl.openable
    cl.openable = lambda path: to_real.get(path, path)
    try:
        days = _or_raised(lambda: cl.days_in(ghost))
        work = _or_raised(lambda: cl.work_days(ghost))
        session = _or_raised(lambda: cl.read_session(ghost, {DAY}))
        totals = {}
        sub = _or_raised(lambda: cl.subagent_usage(ghost, {DAY}, totals))
    finally:
        cl.openable = saved
    check("days_in opens what openable returns", days, {DAY})
    check("work_days opens what openable returns", work, {DAY})
    check("read_session opens what openable returns",
          session if isinstance(session, str) else session["prompts"],
          [{"at": DAY + " 10:00", "kind": "prompt", "text": "fix the build"}])
    # by hand: the session's own reply is 10 in and 5 out, the subagent's 3 in
    check("read_session counts the subagents in the folder openable returns",
          session if isinstance(session, str) else session["usage"],
          {"claude-x": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}, "claude-y": _sub_usage(3)["claude-y"]})
    check("subagent_usage lists the folder openable returns", (sub, totals), (None, _sub_usage(3)))


def test_subagent_usage_listed():
    root = _projects({"proj/s1.jsonl": [_user("x")]})
    totals = {"claude-x": dict.fromkeys(cl.USAGE_FIELDS, 1)}
    outcome = _or_raised(lambda: cl.subagent_usage(os.path.join(root, "proj", "s1.jsonl"), {DAY}, totals))
    check("no subagents folder adds no usage and raises nothing",
          (outcome, totals), (None, {"claude-x": dict.fromkeys(cl.USAGE_FIELDS, 1)}))
    # brackets are a glob character class, so a folder globbed rather than listed would match no file here
    root = _projects({"proj[1]/s1.jsonl": [_user("x")],
                      "proj[1]/s1/subagents/a1.jsonl": [_assistant("sub", msg_id="sa1", usage=_usage(3),
                                                                   model="claude-y")]})
    totals = {}
    cl.subagent_usage(os.path.join(root, "proj[1]", "s1.jsonl"), {DAY}, totals)
    check("a subagents folder whose path holds glob characters is still counted", totals, _sub_usage(3))


# a session id adds its 36 characters and ".jsonl" to the project folder's path,
# which took a transcript in a deep folder past the limit on 2026-10-12
_LONG_ID = "a1b2c3d4-0000-4000-8000-000000000001"


def _long_projects(files):
    """Like _projects, but the project folder's path is 240 characters, so the folder still lists
    while every transcript in it runs past 260, as a session started in a deep folder leaves it.
    Files are written through the extended-length form, the only one Windows accepts that deep."""
    root = _tempdir()
    project = os.path.join(root, "p" * max(1, 240 - len(root) - 1))
    for rel, records in files.items():
        path = "\\\\?\\" + os.path.join(project, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
    return root, project


def test_long_path_transcript():
    # only Windows has a MAX_PATH to run past, and test_openable plays it for every other OS
    if os.name != "nt":
        # a group with no check prints nothing, so this says it was skipped rather than passed
        check("NOT RUN here: only Windows has a MAX_PATH to run past, so this group runs on Windows alone",
              os.name == "nt", False)
        return
    # the checks below prove the fix only where Windows has LongPathsEnabled at 0:
    # where it is 1, python.exe opens a 283-character path with no prefix,
    # so they pass even with openable removed.
    # test_openable_used_by_readers proves the wiring on every machine.
    root, project = _long_projects({
        _LONG_ID + ".jsonl": [_user("fix the build", at="10:00"),
                              _assistant("Done.", at="10:01", msg_id="m1", usage=_usage(10, 5))],
        _LONG_ID + "/subagents/a1.jsonl": [_assistant("sub", msg_id="sa1", usage=_usage(3), model="claude-y")],
    })
    path = os.path.join(project, _LONG_ID + ".jsonl")
    check("fixture: the transcript's path is past 260 characters and its folder's is not",
          (len(path) >= 260, len(project) < 260), (True, True))
    check("fixture: the transcript is found", cl.transcripts(root), [path])
    check("its days are read", _or_raised(lambda: cl.days_in(path)), {DAY})
    check("its work days are read", _or_raised(lambda: cl.work_days(path)), {DAY})
    check("it can be the default day", _or_raised(lambda: cl.default_day([path], TODAY)), DAY)
    session = _or_raised(lambda: cl.read_session(path, {DAY}))
    check("its prompts are read", session if isinstance(session, str) else session["prompts"],
          [{"at": DAY + " 10:00", "kind": "prompt", "text": "fix the build"}])
    # by hand: the session's own reply is 10 in and 5 out, the subagent's 3 in
    check("its subagents under the long path are counted", session if isinstance(session, str) else session["usage"],
          {"claude-x": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}, "claude-y": _sub_usage(3)["claude-y"]})
    saved = os.getcwd()
    os.chdir(_tempdir())
    try:
        digest = _or_raised(lambda: cl.collect(first=DAY, last=DAY, root=root, live=False))
    finally:
        os.chdir(saved)
    check("the collector runs through it and keeps the session",
          digest if isinstance(digest, str) else [s["id"] for g in digest["repositories"] for s in g["sessions"]],
          [_LONG_ID])


# ----- the guard held -------------------------------------------------------------

def test_no_cli_escaped():
    # runs last: every az or gh call outside _with_cli was refused and recorded
    check("no az or gh call reached the guard", _escaped, [])


_TESTS = (test_isolation, test_local_time, test_days_in, test_default_day, test_work_days, test_work_days_interrupted,
          test_default_day_work_days, test_transcripts_skip_subagents,
          test_typed_prompt_kept, test_typed_prompt_summary_and_command, test_typed_prompt_summary_boundary,
          test_typed_prompt_excluded, test_typed_prompt_interrupted,
          test_invokes_remind_me, test_is_question, test_pull_requests_azure, test_pull_requests_github,
          test_add_usage, test_subagent_usage,
          test_result_text, test_read_session, test_read_session_nothing_that_day, test_read_session_cuts,
          test_read_session_after_the_day, test_read_session_interrupted,
          test_read_session_only_later,
          test_read_session_cwd,
          test_collect_left_out_and_dropped, test_collect_groups_by_repository, test_collect_running_sessions,
          test_collect_default_day, test_collect_live_pull_requests_new,
          test_collect_default_day_work_days, test_collect_live_merged_by, test_collect_live_pull_requests_new_author,
          test_collect_live_pull_requests_new_no_identity, test_collect_output_shape,
          test_azure_organisation, test_pull_request_state_azure, test_pull_request_state_github, test_created_in,
          test_pull_request_state_azure_source_and_author, test_same_repository_azure, test_same_repository_github,
          test_live_state, test_live_state_not_a_repository, test_live_state_remote_and_email,
          test_live_state_unpushed_other_remote, test_identities,
          test_main_no_sessions, test_main_date_format, test_non_ascii_on_cp1252_console,
          test_span_days, test_read_session_days, test_read_session_across_days, test_subagent_usage_days,
          test_read_session_remind_me_run, test_read_session_remind_me_run_ended_by_prompt,
          test_read_session_remind_me_run_days, test_read_session_remind_me_run_from_the_day_before,
          test_read_session_remind_me_run_from_the_day_before_summary,
          test_read_session_remind_me_run_only_from_the_day_before,
          test_read_session_remind_me_run_ended_before_the_range,
          test_collect_range, test_collect_earliest, test_collect_remind_me_run_dropped,
          test_collect_live_pull_requests_new_range,
          test_main_range_validation, test_main_default_day_today, test_main_progress_line,
          test_moment, test_in_runs, test_subagent_usage_runs,
          test_read_session_run_subagents_ended, test_read_session_run_subagents_still_going,
          test_read_session_run_subagents_from_the_day_before, test_read_session_run_subagents_second_invocation,
          test_read_session_run_subagents_two_runs, test_read_session_run_subagents_not_closed,
          test_read_session_run_subagents_start_unparseable, test_read_session_run_subagents_end_unparseable,
          test_split, test_main_split_needs_out, test_main_split, test_main_split_no_sessions,
          test_name_splits, test_name_splits_suffix_collision, test_main_split_names_shared,
          test_main_split_repeated_names, test_main_no_split_no_names,
          test_openable, test_openable_used_by_readers, test_subagent_usage_listed, test_long_path_transcript,
          test_no_cli_escaped)


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
