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
    return cl.read_session(os.path.join(root, "proj", name + ".jsonl"), day)


def _collect(files, day=DAY, here=None, **kwargs):
    """Run collect over a fresh projects dir from inside here, a non-repository folder by default,
    because collect asks git about os.getcwd() and the tests' own checkout must stay out of it."""
    root = _projects(files)
    saved = os.getcwd()
    os.chdir(here or _tempdir())
    try:
        return cl.collect(day, root=root, **kwargs)
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
    check("a summary is cut to SUMMARY_LIMIT", cl.typed_prompt(_user(long_summary)),
          ("summary", long_summary[:cl.SUMMARY_LIMIT]))
    check("command with its args",
          cl.typed_prompt(_user("<command-message>review</command-message>\n<command-name>/review</command-name>\n"
                                "<command-args>  42 now </command-args>")), ("command", "/review 42 now"))
    check("command without args", cl.typed_prompt(_user("<command-name>/clear</command-name>")), ("command", "/clear"))
    check("command name without its slash gets one",
          cl.typed_prompt(_user("<command-name>plugin:skill</command-name>")), ("command", "/plugin:skill"))


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


def test_invokes_remind_me():
    check("the slash command", cl.invokes_remind_me("/remind-me"), True)
    check("the slash command with args after whitespace", cl.invokes_remind_me("  /remind-me --day 2026-09-04"), True)
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
    cl.subagent_usage(os.path.join(root, "proj", "s1.jsonl"), DAY, totals)
    check("same-day subagent usage added, other days and broken lines ignored",
          totals, {"claude-x": dict.fromkeys(cl.USAGE_FIELDS, 1),
                   "claude-y": {"input_tokens": 8, "output_tokens": 1, "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0}})


# ----- stretches ---------------------------------------------------------------

def test_stretches():
    check("ACTIVE_GAP_MINUTES", cl.ACTIVE_GAP_MINUTES, 30)
    check("a gap of exactly 30 minutes merges", cl.stretches(["10:00", "10:30"]), [["10:00", "10:30"]])
    check("a gap of 31 minutes splits", cl.stretches(["10:00", "10:31"]), [["10:00", "10:00"], ["10:31", "10:31"]])
    check("unsorted clocks sorted first", cl.stretches(["11:10", "10:00", "11:01", "10:20"]),
          [["10:00", "10:20"], ["11:01", "11:10"]])
    # each gap is measured from the end of the stretch so far, not from its start
    check("a chain of short gaps stays one stretch", cl.stretches(["09:00", "09:25", "09:50", "10:15"]),
          [["09:00", "10:15"]])
    check("no clocks", cl.stretches([]), [])


# ----- read_session -------------------------------------------------------------

def _rich_session():
    root = _projects({
        "proj/s1.jsonl": [
            _user("fix the build", at="10:00", branch="main"),
            _user("<command-name>/remind-me</command-name>", at="10:01"),
            _assistant("Want me to open the PR?", at="10:05", msg_id="m1", usage=_usage(10, 5), branch="HEAD"),
            _assistant([_tool_use("Bash", "git commit -m wip && git push origin feature")], at="10:05",
                       msg_id="m1", usage=_usage(10, 5), branch="feature"),
            _assistant([_tool_use("Read", "git commit -m not-a-shell")], at="10:05", msg_id="m1",
                       usage=_usage(10, 5)),
            _user([_tool_result("Created !151943")], at="10:06"),
            # a list-content result is serialised before it is searched
            _user([_tool_result([{"type": "text", "text": "https://dev.azure.com/a/p/_git/r/pullrequest/888"}])],
                  at="10:07"),
            _assistant("Merged. See https://github.com/acme/widgets/pull/12", at="11:00", msg_id="m2",
                       usage=_usage(20, 6), branch="main"),
            _assistant("subagent words?", at="11:01", msg_id="m3", usage=_usage(1000), isSidechain=True),
            _assistant([_tool_use("PowerShell", "gh pr create --title x"), {"type": "text", "text": "   "}],
                       at="11:02", msg_id="m4"),
            _user("yesterday's prompt", at="09:00", day=PREV),
            "not json at all",
        ],
        "proj/s1/subagents/a1.jsonl": [_assistant("sub", msg_id="sa1", usage=_usage(3), model="claude-y")],
    })
    return cl.read_session(os.path.join(root, "proj", "s1.jsonl"), DAY)


def test_read_session():
    session = _rich_session()
    check("id from the file name", session["id"], "s1")
    check("cwd", session["cwd"], FOLDER)
    check("entrypoint", session["entrypoint"], "cli")
    check("branches in order, HEAD left out", session["branches"], ["main", "feature"])
    check("first and last clock of the day", (session["first"], session["last"]), ("10:00", "11:02"))
    # the request that runs this skill is not part of the day it reports on
    check("prompts without the remind-me run", session["prompts"],
          [{"at": "10:00", "kind": "prompt", "text": "fix the build"}])
    check("questions", session["questions"], [{"at": "10:05", "text": "Want me to open the PR?"}])
    check("actions from shell tool commands only", session["actions"],
          [{"at": "10:05", "kind": "commit", "command": "git commit -m wip && git push origin feature"},
           {"at": "10:05", "kind": "push", "command": "git commit -m wip && git push origin feature"},
           {"at": "11:02", "kind": "pr-create", "command": "gh pr create --title x"}])
    check("pull requests from results, replies and commands, sorted", session["pull_requests"],
          ["azure:151943", "azure:888", "github:acme/widgets#12"])
    check("last reply is the last non-blank text", session["last_reply"],
          {"at": "11:00", "text": "Merged. See https://github.com/acme/widgets/pull/12"})
    check("usage: split reply once, sidechain left out, subagent added", session["usage"],
          {"claude-x": {"input_tokens": 30, "output_tokens": 11, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0},
           "claude-y": {"input_tokens": 3, "output_tokens": 0, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}})
    check("active stretches split by the 53-minute gap", session["active"], [["10:00", "10:07"], ["11:00", "11:02"]])
    check("pull_requests_new is left to collect", session["pull_requests_new"], [])


def test_read_session_nothing_that_day():
    check("only other days is None", _session([_user("thursday", day=PREV)]), None)
    check("no lines is None", _session([]), None)


def test_read_session_cuts():
    command = "git push " + "x" * 300
    session = _session([_assistant([_tool_use("Bash", command)]), _assistant("y" * 2000 + "?")])
    check("an action's command is cut to COMMAND_LIMIT", session["actions"][0]["command"], command[:cl.COMMAND_LIMIT])
    check("a question keeps its last QUESTION_TAIL characters", session["questions"][0]["text"],
          ("y" * 2000 + "?")[-cl.QUESTION_TAIL:])
    check("the last reply keeps its last LAST_TAIL characters", session["last_reply"]["text"],
          ("y" * 2000 + "?")[-cl.LAST_TAIL:])


def test_read_session_elides_the_middle():
    session = _session([_user("p%d" % n) for n in range(85)])
    prompts = session["prompts"]
    check("MAX_PROMPTS", cl.MAX_PROMPTS, 80)
    # 10 from the start, a marker, and 70 from the end, so 5 of 85 are left out
    check("kept count", len(prompts), 81)
    check("the first ten kept", [p["text"] for p in prompts[:10]], ["p%d" % n for n in range(10)])
    check("the marker", prompts[10], {"at": None, "kind": "elided", "text": "5 prompts left out"})
    check("the rest from the end", [p["text"] for p in prompts[11:]], ["p%d" % n for n in range(15, 85)])

    session = _session([_user("p%d" % n) for n in range(80)])
    check("exactly MAX_PROMPTS kept whole", [p["text"] for p in session["prompts"]], ["p%d" % n for n in range(80)])


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
    check("day", digest["day"], DAY)
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
    check("no day given reports the latest day before today", (digest["day"], _summary(digest)),
          (DAY, [(FOLDER, False, False, ["b"])]))
    digest = _collect({"proj/c.jsonl": [_user("monday", day=TODAY)]}, day=None, live=False, today=TODAY)
    check("only today has entries: no day, nothing reported", (digest["day"], digest["repositories"]), (None, []))


def test_collect_live_pull_requests_new():
    repo = _repo()
    remote = "https://dev.azure.com/acme/proj/_git/widgets"
    _git(repo, "remote", "add", "origin", remote)
    states = {"1234": {"status": "active", "created": _local(DAY, "09:00")},
              "5678": {"status": "completed", "created": _local(PREV, "09:00")}}

    def answer(argv):
        if argv[0] == "az":
            return True, json.dumps(states[argv[argv.index("--id") + 1]])
        return True, json.dumps({"state": "OPEN", "createdAt": _local(DAY, "12:00"), "author": {"login": "ann"}})

    digest, calls = _with_cli(answer, lambda: _collect({"proj/s1.jsonl": [
        _user("go", cwd=repo),
        _assistant("opened !1234, compared with !5678 and https://github.com/acme/widgets/pull/12", cwd=repo)]},
        today=TODAY))
    session = digest["repositories"][0]["sessions"][0]
    check("the organisation comes from the repository's remote",
          sorted(c[c.index("--org") + 1] for c in calls if c[0] == "az"), ["https://dev.azure.com/acme"] * 2)
    check("every named pull request looked up", sorted(digest["pull_requests"]),
          ["azure:1234", "azure:5678", "github:acme/widgets#12"])
    check("an old pull request keeps its own state", digest["pull_requests"]["azure:5678"]["status"], "completed")
    # the new ones are those the platform says were created that day
    check("pull_requests_new is the keys created on the day", session["pull_requests_new"],
          ["azure:1234", "github:acme/widgets#12"])
    check("the repository's live state was read", digest["repositories"][0]["live"]["remote"], remote)


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
              "url": "https://github.com/acme/widgets/pull/12"}
    state, calls = _with_cli(lambda argv: (True, json.dumps(answer)),
                             lambda: cl.pull_request_state("github:acme/widgets#12", ["https://dev.azure.com/one"]))
    check("state lower-cased into status, createdAt to created, author to its login",
          state, {"status": "merged", "title": "t", "created": "2026-09-04T10:00:00Z", "author": "ann",
                  "url": "https://github.com/acme/widgets/pull/12"})
    check("gh asked for the number in its repository", [c[:6] for c in calls],
          [["gh", "pr", "view", "12", "-R", "acme/widgets"]])

    state, _ = _with_cli(lambda argv: (False, "gh: Could not resolve to a PullRequest"),
                         lambda: cl.pull_request_state("github:acme/widgets#12", []))
    check("a failed lookup is unknown with its reason",
          state, {"status": "unknown", "reason": "gh: Could not resolve to a PullRequest"})

    state, _ = _with_cli(lambda argv: (True, ""), lambda: cl.pull_request_state("github:acme/widgets#12", []))
    check("an empty answer is unknown", state, {"status": "unknown", "reason": "unreadable answer"})


def test_created_on():
    check("created that local day", cl.created_on({"created": _local(DAY, "23:59")}, DAY), True)
    check("created another day", cl.created_on({"created": _local(PREV, "23:59")}, DAY), False)
    check("no created", cl.created_on({"status": "unknown", "reason": "x"}, DAY), False)


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
           "remote": "https://dev.azure.com/acme/proj/_git/widgets",
           "branches": [{"name": "main", "local": True, "on_remote": True, "unpushed": 2},
                        {"name": "local-only", "local": True, "on_remote": False},
                        {"name": "gone", "local": False}]})


def test_live_state_not_a_repository():
    state = cl.live_state(_tempdir(), [{"branches": ["main"]}])
    check("nothing read is None, not zero",
          (state["uncommitted"], state["current_branch"], state["remote"]), (None, None, None))
    check("the failure is noted", [note.startswith("git status failed: ") for note in state["notes"]], [True])
    check("a branch that cannot be read is not local", state["branches"], [{"name": "main", "local": False}])


# ----- main: exit status and output ---------------------------------------------

def _main(argv):
    """Run main in-process from a non-repository folder with stdout and stderr captured.
    stdout must be a real text wrapper, because main reconfigures it to UTF-8."""
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")
    err = io.StringIO()
    saved = sys.stdout, sys.stderr, os.getcwd()
    sys.stdout, sys.stderr = out, err
    os.chdir(_tempdir())
    try:
        try:
            code = cl.main(argv)
        except SystemExit as exc:
            code = exc.code
        out.flush()
    finally:
        sys.stdout, sys.stderr = saved[0], saved[1]
        os.chdir(saved[2])
    return code, raw.getvalue().decode("utf-8"), err.getvalue()


def test_main_no_sessions():
    # CLAUDE_CONFIG_DIR holds no projects folder, so nothing is found
    code, out, err = _main(["--day", DAY, "--no-live"])
    check("EXIT_NO_SESSIONS", cl.EXIT_NO_SESSIONS, 3)
    check("no session exits 3", code, 3)
    check("the digest is still printed", (json.loads(out)["day"], json.loads(out)["repositories"]), (DAY, []))
    check("stderr names the day and cleanupPeriodDays", err,
          "no sessions on %s; Claude Code deletes transcripts after cleanupPeriodDays, 30 days by default\n" % DAY)

    code, _, err = _main(["--no-live"])
    check("no day and no session exits 3", code, 3)
    check("stderr says any day before today",
          err.startswith("no sessions on any day before today;") and "cleanupPeriodDays" in err, True)


def test_main_day_format():
    for day in ("yesterday", "2026-13-01"):
        code, out, err = _main(["--day", day, "--no-live"])
        check("--day %s exits 2" % day, code, 2)
        check("--day %s explains the format" % day, err.rstrip().endswith("--day must be YYYY-MM-DD"), True)
        check("--day %s prints no digest" % day, out, "")


def test_non_ascii_on_cp1252_console():
    text = "\u7e41\u9ad4\u4e2d\u6587 prompt"
    config = _tempdir()
    projects = _projects({"proj/s1.jsonl": [_user(text)]})
    os.rename(projects, os.path.join(config, "projects"))
    env = dict(os.environ, PYTHONIOENCODING="cp1252", CLAUDE_CONFIG_DIR=config)
    proc = subprocess.run([sys.executable, _SCRIPT, "--day", DAY, "--no-live"], capture_output=True, env=env,
                          cwd=_tempdir(), timeout=60)
    check("subprocess exits 0", proc.returncode, 0)
    check("subprocess stderr empty", proc.stderr, b"")
    doc = json.loads(proc.stdout.decode("utf-8"))
    check("non-ASCII prompt survives as UTF-8",
          [p["text"] for g in doc["repositories"] for s in g["sessions"] for p in s["prompts"]], [text])


# ----- the guard held -------------------------------------------------------------

def test_no_cli_escaped():
    # runs last: every az or gh call outside _with_cli was refused and recorded
    check("no az or gh call reached the guard", _escaped, [])


_TESTS = (test_isolation, test_local_time, test_days_in, test_default_day, test_transcripts_skip_subagents,
          test_typed_prompt_kept, test_typed_prompt_summary_and_command, test_typed_prompt_excluded,
          test_invokes_remind_me, test_is_question, test_pull_requests_azure, test_pull_requests_github,
          test_add_usage, test_subagent_usage, test_stretches,
          test_read_session, test_read_session_nothing_that_day, test_read_session_cuts,
          test_read_session_elides_the_middle,
          test_collect_left_out_and_dropped, test_collect_groups_by_repository, test_collect_running_sessions,
          test_collect_default_day, test_collect_live_pull_requests_new,
          test_azure_organisation, test_pull_request_state_azure, test_pull_request_state_github, test_created_on,
          test_live_state, test_live_state_not_a_repository,
          test_main_no_sessions, test_main_day_format, test_non_ascii_on_cp1252_console,
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
