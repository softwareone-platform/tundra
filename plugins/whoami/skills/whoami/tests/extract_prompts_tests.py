"""Deterministic self-check for the whoami prompt extractor.

The extractor is a measuring instrument over an undocumented transcript format,
so every case here is a known-answer case over a synthetic transcripts directory
(a `--projects`-style dir of <project>/<session>.jsonl files) written to a temp dir.
Each filter in the extractor is load-bearing: removing it turns at least one check red.

Pure stdlib, ASCII-only source and output (Windows cp1252 console).
Exits non-zero on any failure. Run from anywhere:
    python tests/extract_prompts_tests.py
"""

import io
import json
import os
import subprocess
import sys
import tempfile

# import the module under test from the sibling scripts/ dir without installing
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, _SCRIPTS)
import extract_prompts as ep  # noqa: E402

_SCRIPT = os.path.join(_SCRIPTS, "extract_prompts.py")

# the repository the person worked in, and paths around it that must stay out of scope
_BASE = tempfile.mkdtemp()
ROOT = os.path.join(_BASE, "repo-a")
SUB = os.path.join(ROOT, "src", "lib")
SIBLING = os.path.join(_BASE, "repo-a-generator")
OUTSIDE = os.path.join(_BASE, "elsewhere")

T1 = "2026-09-01T10:00:00.000Z"
T2 = "2026-09-01T11:00:00.000Z"
T3 = "2026-09-01T12:00:00.000Z"


# every check records (group, name, ok, detail),
# so a manual run can list the greens, not only the reds.
# the group is the test function currently running.
_results = []
_group = ""


def check(name, got, want):
    ok = got == want
    _results.append((_group, name, ok, "" if ok else "got %r, want %r" % (got, want)))


# ----- synthetic transcripts ---------------------------------------------------

def _user(content, session="s1", cwd=ROOT, ts=T1, origin="human", **extra):
    """A user record shaped like a Claude Code transcript line.
    origin=None leaves the field out, as a headless `claude -p` prompt does."""
    record = {"parentUuid": None, "isSidechain": False, "type": "user",
              "message": {"role": "user", "content": content},
              "uuid": "u-" + ts, "timestamp": ts, "cwd": cwd, "sessionId": session}
    if origin is not None:
        record["origin"] = {"kind": origin}
    record.update(extra)
    return record


def _assistant(text, session="s1", cwd=ROOT, ts=T1):
    return {"parentUuid": None, "isSidechain": False, "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            "uuid": "a-" + ts, "timestamp": ts, "cwd": cwd, "sessionId": session}


def _projects(files):
    """Write {relative path: [record, ...]} under a fresh projects dir.
    Lines are compact and raw UTF-8, which is how Claude Code writes them,
    and the extractor's line pre-filters depend on that exact spelling."""
    d = tempfile.mkdtemp()
    for rel, records in files.items():
        path = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write((r if isinstance(r, str) else json.dumps(r, separators=(",", ":"), ensure_ascii=False)) + "\n")
    return d


def _extract(files, root=ROOT):
    return ep.extract([ep.normalise(root)], _projects(files))


def _one(records):
    return _extract({"proj/s1.jsonl": records})


# ----- extract: what counts as a typed prompt ------------------------------------

def test_human_prompt_extracted():
    check("string content extracted", _one([_user("fix the build")]),
          (1, [{"session": "s1", "time": T1, "cwd": ROOT, "text": "fix the build"}]))
    # list content keeps only the text blocks, one per line
    check("list content of text blocks extracted",
          _one([_user([{"type": "text", "text": "first"}, {"type": "text", "text": "second"}])]),
          (1, [{"session": "s1", "time": T1, "cwd": ROOT, "text": "first\nsecond"}]))


def test_model_written_records_excluded():
    # each of these is in scope, so the session is counted while no prompt is
    check("task-notification origin excluded",
          _one([_user("<task-notification>done</task-notification>", origin="task-notification")]), (1, []))
    check("peer origin with isMeta excluded",
          _one([_user("From another session", origin="peer", isMeta=True)]), (1, []))
    check("skill body with isMeta and no origin excluded",
          _one([_user([{"type": "text", "text": "Base directory for this skill"}], origin=None, isMeta=True)]),
          (1, []))
    # origin human is on the line, so only the flag itself can exclude it
    check("isCompactSummary excluded despite human origin",
          _one([_user("This session is being continued", isCompactSummary=True)]), (1, []))
    check("isMeta excluded despite human origin",
          _one([_user("Base directory for this skill", isMeta=True)]), (1, []))
    check("isSidechain excluded despite human origin",
          _one([_user("subagent turn", isSidechain=True)]), (1, []))
    check("headless prompt with no origin excluded", _one([_user("run headless", origin=None)]), (1, []))
    # real transcripts write tool results as user records with no origin at all
    check("tool_result-only user record excluded",
          _one([_user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], origin=None,
                      toolUseResult={"stdout": "ok"})]), (1, []))
    check("assistant record excluded", _one([_assistant("human")]), (1, []))


def test_human_prompt_predicate():
    # called directly so each clause is exercised without extract's line pre-filter in front of it
    check("human user record", ep.human_prompt(_user("x")), True)
    check("assistant type", ep.human_prompt(dict(_assistant("x"), origin={"kind": "human"})), False)
    check("task-notification origin", ep.human_prompt(_user("x", origin="task-notification")), False)
    check("peer origin", ep.human_prompt(_user("x", origin="peer")), False)
    check("no origin", ep.human_prompt(_user("x", origin=None)), False)
    check("null origin", ep.human_prompt(dict(_user("x", origin=None), origin=None)), False)
    check("isMeta", ep.human_prompt(_user("x", isMeta=True)), False)
    check("isCompactSummary", ep.human_prompt(_user("x", isCompactSummary=True)), False)
    check("isSidechain", ep.human_prompt(_user("x", isSidechain=True)), False)


# ----- scope: path boundaries, not string prefixes ------------------------------

def test_scope():
    roots = [ep.normalise(ROOT)]
    check("root itself in scope", ep.in_scope(ROOT, roots), True)
    check("subdirectory in scope", ep.in_scope(SUB, roots), True)
    check("trailing separator in scope", ep.in_scope(ROOT + os.sep, roots), True)
    check("dot-dot back into root in scope", ep.in_scope(os.path.join(SUB, "..", ".."), roots), True)
    check("sibling sharing the name as a prefix out of scope", ep.in_scope(SIBLING, roots), False)
    check("parent out of scope", ep.in_scope(_BASE, roots), False)
    check("unrelated dir out of scope", ep.in_scope(OUTSIDE, roots), False)
    check("second root counts", ep.in_scope(SIBLING, roots + [ep.normalise(SIBLING)]), True)

    check("extract: subdirectory cwd in scope", _one([_user("in sub", cwd=SUB)]),
          (1, [{"session": "s1", "time": T1, "cwd": SUB, "text": "in sub"}]))
    check("extract: sibling-prefix cwd out of scope", _one([_user("in sibling", cwd=SIBLING)]), (0, []))


def test_normalise():
    if os.name == "nt":
        # git bash hands over /c/Users/... on Windows
        check("git bash drive path", ep.normalise("/c/Users/someone/repo-a"), ep.normalise("C:\\Users\\someone\\repo-a"))
        check("bare git bash drive", ep.normalise("/c"), ep.normalise("C:\\"))
        check("case folded", ep.in_scope(ROOT.upper(), [ep.normalise(ROOT)]), True)
    else:
        check("posix path left alone", ep.normalise("/c/Users/someone/repo-a"), "/c/Users/someone/repo-a")
    check("home expanded", ep.normalise("~"), os.path.normcase(os.path.normpath(os.path.expanduser("~"))))


# ----- the record's own cwd decides, not the first cwd on the line -------------

def test_nested_cwd_does_not_decide():
    # the nested object comes first in the line, so the line pattern lands on its in-scope cwd
    record = {"toolUseResult": {"cwd": ROOT}}
    record.update(_user("typed elsewhere", cwd=OUTSIDE))
    line = json.dumps(record, separators=(",", ":"))
    check("fixture: nested cwd precedes the record's own", line.index('"cwd":') < line.rindex('"cwd":'), True)
    check("record with out-of-scope own cwd excluded", _one([record])[1], [])


# ----- pastes are replaced by their size ----------------------------------------

def test_strip_pastes():
    # 16 for <pasted_content>, 3 for abc, 17 for </pasted_content>
    check("plain paste", ep.strip_pastes("see <pasted_content>abc</pasted_content> here"),
          "see [pasted text, 36 characters] here")
    # 26 for <pasted_content id="ab12">, 3 for xyz, 27 for </pasted_content id="ab12">
    check("paste with id", ep.strip_pastes('<pasted_content id="ab12">xyz</pasted_content id="ab12">'),
          "[pasted text, 56 characters]")
    # 16 + 5 for a\nb\nc + 17, and the dot has to cross the newlines
    check("multi-line paste", ep.strip_pastes("<pasted_content>a\nb\nc</pasted_content>"),
          "[pasted text, 38 characters]")
    # two blocks are two replacements, and the text between them survives
    check("two pastes replaced separately",
          ep.strip_pastes("<pasted_content>a</pasted_content> and <pasted_content>bb</pasted_content>"),
          "[pasted text, 34 characters] and [pasted text, 35 characters]")
    check("no paste untouched", ep.strip_pastes("plain words"), "plain words")

    got = _one([_user('look at <pasted_content id="ab12">xyz</pasted_content id="ab12"> please')])[1]
    check("extract strips pastes", [p["text"] for p in got], ["look at [pasted text, 56 characters] please"])


def test_text_of():
    check("string", ep.text_of("hello"), "hello")
    check("text blocks joined by newline",
          ep.text_of([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]), "a\nb")
    check("non-text blocks dropped",
          ep.text_of([{"type": "image", "source": {}}, {"type": "text", "text": "a"},
                      {"type": "tool_result", "content": "r"}, "stray"]), "a")
    check("tool_result-only list is empty", ep.text_of([{"type": "tool_result", "content": "r"}]), "")
    check("None is empty", ep.text_of(None), "")


# ----- sessions are counted by id, not by file ----------------------------------

def test_sessions_in_scope():
    sessions, prompts = _extract({
        "proj/s1.jsonl": [_user("main session", session="s1", ts=T1)],
        # a subagent writes its own file under the session's directory, carrying the parent's id
        "proj/s1/subagents/agent-abc123.jsonl": [_assistant("working", session="s1", ts=T2)],
        "proj/s2.jsonl": [_assistant("reply", session="s2", ts=T3)],
    })
    check("subagent file adds no session", sessions, 2)
    check("only the typed prompt extracted", [p["text"] for p in prompts], ["main session"])

    # a line with no cwd cannot be placed, so it neither counts a session nor yields a prompt
    check("line without cwd ignored",
          _one([{"type": "summary", "summary": "x", "leafUuid": "l", "sessionId": "s9"}]), (0, []))
    # a truncated line that still carries cwd and human counts its session and is skipped, not raised
    check("unparseable line skipped",
          _one(['{"type":"user","origin":{"kind":"human"},"cwd":%s,"sessionId":"s1"' % json.dumps(ROOT)]),
          (1, []))


def test_session_falls_back_to_file_name():
    record = _user("no id")
    del record["sessionId"]
    got = _extract({"proj/abc-123.jsonl": [record]})
    check("file stem stands in for the session", got, (1, [{"session": "abc-123", "time": T1, "cwd": ROOT, "text": "no id"}]))


def test_sorted_by_timestamp():
    _, prompts = _extract({
        "proj/s1.jsonl": [_user("third", session="s1", ts=T3), _user("first", session="s1", ts=T1)],
        "proj/s2.jsonl": [_user("second", session="s2", ts=T2)],
    })
    check("prompts ordered by time", [p["text"] for p in prompts], ["first", "second", "third"])


def test_sorted_ties_broken_by_session():
    # the file name says nothing about the session inside it,
    # and running both arrangements puts the last-sorting session in the file read first whatever order glob reads them in
    #
    # the texts sort against the sessions on purpose,
    # so a tie broken on the text instead of the session gives the opposite order
    want = [{"session": "s-a", "time": T1, "cwd": ROOT, "text": "zzz"},
            {"session": "s-z", "time": T1, "cwd": ROOT, "text": "aaa"}]
    _, prompts = _extract({"proj/a.jsonl": [_user("aaa", session="s-z", ts=T1)],
                           "proj/b.jsonl": [_user("zzz", session="s-a", ts=T1)]})
    check("equal times ordered by session, s-z in a.jsonl", prompts, want)
    _, prompts = _extract({"proj/a.jsonl": [_user("zzz", session="s-a", ts=T1)],
                           "proj/b.jsonl": [_user("aaa", session="s-z", ts=T1)]})
    check("equal times ordered by session, s-a in a.jsonl", prompts, want)


# ----- whoami runs are left out whole -------------------------------------------

WHOAMI_TAG = "<command-name>/whoami</command-name>"


def _prompt(text, session="s1", ts=T1, cwd=ROOT):
    """A prompt as extract returns it."""
    return {"session": session, "time": ts, "cwd": cwd, "text": text}


def test_drop_whoami_sessions_detection():
    check("/whoami command detected", ep.drop_whoami_sessions([_prompt(WHOAMI_TAG)]), ([], 1))
    # a plugin skill can also be invoked by its plugin-qualified name
    check("/whoami:whoami command detected",
          ep.drop_whoami_sessions([_prompt("<command-name>/whoami:whoami</command-name>")]), ([], 1))

    longer = _prompt("<command-name>/whoamix</command-name>")
    check("longer command name not detected", ep.drop_whoami_sessions([longer]), ([longer], 0))
    other = _prompt("<command-name>/other:whoami</command-name>")
    check("another plugin's whoami not detected", ep.drop_whoami_sessions([other]), ([other], 0))
    bare = _prompt("tell me whoami")
    check("the bare word not detected", ep.drop_whoami_sessions([bare]), ([bare], 0))

    # only the whoami skill itself counts, not any other skill the same plugin might gain
    sibling_skill = _prompt("<command-name>/whoami:other</command-name>")
    check("another skill in the whoami plugin not detected",
          ep.drop_whoami_sessions([sibling_skill]), ([sibling_skill], 0))
    # the match is case-sensitive by design,
    # and how Claude Code records the casing of a typed command has not been verified
    upper = _prompt("<command-name>/WHOAMI</command-name>")
    check("upper-case command name not detected", ep.drop_whoami_sessions([upper]), ([upper], 0))
    # without its opening tag the text is only quoted markup, not a command the person ran
    untagged = _prompt("/whoami</command-name>")
    check("closing tag without the opening tag not detected",
          ep.drop_whoami_sessions([untagged]), ([untagged], 0))


def test_drop_whoami_sessions_drops_whole_session():
    first = _prompt("first", session="s1", ts=T1)
    # the answer carries no command tag, so only its session ties it to the run
    run = [_prompt("<command-message>whoami</command-message>\n" + WHOAMI_TAG, session="s2", ts=T1),
           _prompt("my answer to its question", session="s2", ts=T2)]
    second = _prompt("second", session="s3", ts=T2)
    third = _prompt("third", session="s1", ts=T3)
    check("whoami session dropped whole, others kept in order",
          ep.drop_whoami_sessions([first, run[0], run[1], second, third]), ([first, second, third], 1))

    # both prompts carry the tag, so a count of prompts would give 2
    check("one whoami session with two tagged prompts counts 1",
          ep.drop_whoami_sessions([_prompt(WHOAMI_TAG, ts=T1), _prompt(WHOAMI_TAG, ts=T2)]), ([], 1))
    check("two whoami sessions count 2",
          ep.drop_whoami_sessions([_prompt(WHOAMI_TAG, session="s1"), _prompt(WHOAMI_TAG, session="s2")]),
          ([], 2))
    check("empty input", ep.drop_whoami_sessions([]), ([], 0))


# ----- copies of one prompt are dropped -----------------------------------------

def test_drop_copies():
    first = _prompt("fix the build", session="s1", ts=T1)
    copy = _prompt("fix the build", session="s2", ts=T1)
    check("same time and text in another session dropped", ep.drop_copies([first, copy]), ([first], 1))
    # the count is of prompts dropped, not of prompts that had a copy
    check("three copies of one prompt count 2",
          ep.drop_copies([first, copy, _prompt("fix the build", session="s3", ts=T1)]), ([first], 2))

    later = _prompt("fix the build", session="s2", ts=T2)
    check("same text at another time kept", ep.drop_copies([first, later]), ([first, later], 0))
    other = _prompt("new work", session="s2", ts=T1)
    check("same time with other text kept", ep.drop_copies([first, other]), ([first, other], 0))
    check("empty input", ep.drop_copies([]), ([], 0))

    # the lower session is kept only because extract sorts it first, so here input order alone decides
    s2_copy = _prompt("fix the build", session="s2", ts=T1)
    s1_copy = _prompt("fix the build", session="s1", ts=T1)
    check("first in input order kept, whatever its session", ep.drop_copies([s2_copy, s1_copy]), ([s2_copy], 1))


# ----- as_text: plain text grouped by session -----------------------------------

def test_as_text():
    # the expected text is written from the format, not from a run of it,
    # a counts line and then, per session, a blank line, a header with its first prompt's cwd, and one line per prompt,
    # ending in exactly one newline, which the whole-string comparison already pins.
    # time[:16] cuts each timestamp to the minute, so T1 reads 2026-09-01T10:00.
    prompts = [_prompt("fix\nthe\nbuild", session="s1", ts=T1, cwd=ROOT),
               _prompt("second", session="s2", ts=T2, cwd=SUB),
               _prompt("third", session="s1", ts=T3, cwd=SUB)]
    check("interleaved sessions grouped, newlines joined",
          ep.as_text(4, 1, 2, prompts),
          "sessions in scope: 4, whoami runs left out: 1, copied prompts left out: 2, prompts: 3\n"
          "\n"
          "## session s1  " + ROOT + "\n"
          "[2026-09-01T10:00] fix / the / build\n"
          "[2026-09-01T12:00] third\n"
          "\n"
          "## session s2  " + SUB + "\n"
          "[2026-09-01T11:00] second\n")

    check("zero prompts is the counts line only", ep.as_text(3, 1, 2, []),
          "sessions in scope: 3, whoami runs left out: 1, copied prompts left out: 2, prompts: 0\n")


# ----- main: exit status and output ---------------------------------------------

def _main(files):
    """Run main in-process with stdout and stderr captured.
    stdout must be a real text wrapper, because main reconfigures it to UTF-8."""
    projects = _projects(files)
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")
    err = io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = ep.main([ROOT, "--projects", projects])
        out.flush()
    finally:
        sys.stdout, sys.stderr = saved
    return code, json.loads(raw.getvalue().decode("utf-8")), err.getvalue()


def test_main_exit_status():
    code, doc, err = _main({"proj/s1.jsonl": [_user("run headless", origin=None)]})
    check("in scope but no prompt exits FORMAT_CHANGED", code, 3)
    check("FORMAT_CHANGED constant", ep.FORMAT_CHANGED, 3)
    check("in scope but no prompt still prints the document", doc, {"sessions_in_scope": 1, "whoami_sessions_excluded": 0, "copied_prompts_dropped": 0, "prompts": []})
    check("in scope but no prompt explains on stderr",
          err.startswith("1 sessions ran in scope but none held a human-typed prompt;"), True)

    code, doc, err = _main({"proj/s1.jsonl": [_user("fix the build")]})
    check("prompts found exits 0", code, 0)
    check("prompts found document", doc,
          {"sessions_in_scope": 1, "whoami_sessions_excluded": 0, "copied_prompts_dropped": 0,
           "prompts": [{"session": "s1", "time": T1, "cwd": ROOT, "text": "fix the build"}]})
    check("prompts found stderr silent", err, "")

    code, doc, err = _main({"proj/s1.jsonl": [_user("elsewhere", cwd=SIBLING)]})
    check("nothing in scope exits 0", code, 0)
    check("nothing in scope document", doc, {"sessions_in_scope": 0, "whoami_sessions_excluded": 0, "copied_prompts_dropped": 0, "prompts": []})
    check("nothing in scope stderr silent", err, "")


def test_non_ascii_on_cp1252_console():
    text = "\u7e41\u9ad4\u4e2d\u6587 prompt"
    projects = _projects({"proj/s1.jsonl": [_user(text)]})
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run([sys.executable, _SCRIPT, ROOT, "--projects", projects],
                          capture_output=True, env=env, timeout=60)
    check("subprocess exits 0", proc.returncode, 0)
    check("subprocess stderr empty", proc.stderr, b"")
    doc = json.loads(proc.stdout.decode("utf-8"))
    check("non-ASCII text survives as UTF-8", [p["text"] for p in doc["prompts"]], [text])


def _whoami_run(session="s1"):
    """A whoami run: the command prompt, then the person's answer to its question."""
    return [_user(WHOAMI_TAG, session=session, ts=T1), _user("my answer", session=session, ts=T2)]


def test_main_leaves_out_whoami_runs():
    code, doc, err = _main({"proj/s1.jsonl": _whoami_run("s1"),
                            "proj/s2.jsonl": [_user("fix the build", session="s2", ts=T3)]})
    check("whoami run alongside another exits 0", code, 0)
    # the run still ran in scope, so it is counted as a session while its prompts are dropped
    check("whoami run alongside another document", doc,
          {"sessions_in_scope": 2, "whoami_sessions_excluded": 1, "copied_prompts_dropped": 0,
           "prompts": [{"session": "s2", "time": T3, "cwd": ROOT, "text": "fix the build"}]})
    check("whoami run alongside another stderr silent", err, "")


def test_main_exit_status_judged_before_drop():
    # the run in progress is always a whoami run, so a first run in a repository sees only that
    code, doc, err = _main({"proj/s1.jsonl": _whoami_run("s1")})
    check("only a whoami run exits 0, not FORMAT_CHANGED", code, 0)
    check("only a whoami run document", doc, {"sessions_in_scope": 1, "whoami_sessions_excluded": 1, "copied_prompts_dropped": 0, "prompts": []})
    check("only a whoami run stderr silent", err, "")


def test_main_keeps_the_copy_from_the_first_session():
    # both arrangements are run, so in one of them the file read first holds the session that sorts last
    want = {"sessions_in_scope": 2, "whoami_sessions_excluded": 0, "copied_prompts_dropped": 1,
            "prompts": [{"session": "s-a", "time": T1, "cwd": ROOT, "text": "fix the build"}]}
    _, doc, _ = _main({"proj/a.jsonl": [_user("fix the build", session="s-z", ts=T1)],
                       "proj/b.jsonl": [_user("fix the build", session="s-a", ts=T1)]})
    check("copy from s-a kept, s-z in a.jsonl", doc, want)
    _, doc, _ = _main({"proj/a.jsonl": [_user("fix the build", session="s-a", ts=T1)],
                       "proj/b.jsonl": [_user("fix the build", session="s-z", ts=T1)]})
    check("copy from s-a kept, s-a in a.jsonl", doc, want)


def test_main_reports_copies_dropped():
    # s2 resumed s1, so its transcript opens with a copy of s1's prompt before the new work
    code, doc, err = _main({"proj/s1.jsonl": [_user("fix the build", session="s1", ts=T1)],
                            "proj/s2.jsonl": [_user("fix the build", session="s2", ts=T1),
                                              _user("new work", session="s2", ts=T2)]})
    check("resumed session exits 0", code, 0)
    check("resumed session document", doc,
          {"sessions_in_scope": 2, "whoami_sessions_excluded": 0, "copied_prompts_dropped": 1,
           "prompts": [{"session": "s1", "time": T1, "cwd": ROOT, "text": "fix the build"},
                       {"session": "s2", "time": T2, "cwd": ROOT, "text": "new work"}]})
    check("resumed session stderr silent", err, "")


def test_main_drops_whoami_runs_before_copies():
    # s2 resumed the whoami run s1, so its only tagged prompt is a copy of s1's,
    # and dropping copies first would leave s2's new answer behind as an ordinary prompt
    code, doc, err = _main({"proj/s1.jsonl": _whoami_run("s1"),
                            "proj/s2.jsonl": [_user(WHOAMI_TAG, session="s2", ts=T1),
                                              _user("my answer", session="s2", ts=T2),
                                              _user("my next answer", session="s2", ts=T3)]})
    check("resumed whoami run exits 0", code, 0)
    check("resumed whoami run document", doc,
          {"sessions_in_scope": 2, "whoami_sessions_excluded": 2, "copied_prompts_dropped": 0, "prompts": []})
    check("resumed whoami run stderr silent", err, "")


def _main_text(files):
    """Run main in-process with --text, returning (code, stdout text, stderr text)."""
    projects = _projects(files)
    raw = io.BytesIO()
    # newline="\n" stops the wrapper writing \r\n on Windows, so the text compares exactly
    out = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")
    err = io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = ep.main([ROOT, "--projects", projects, "--text"])
        out.flush()
    finally:
        sys.stdout, sys.stderr = saved
    return code, raw.getvalue().decode("utf-8"), err.getvalue()


def test_main_text():
    code, out, err = _main_text({"proj/s1.jsonl": _whoami_run("s1"),
                                 "proj/s2.jsonl": [_user("fix the build", session="s2", ts=T3)]})
    check("--text exits 0", code, 0)
    # the prompt count is taken after the whoami run is dropped, and time[:16] of T3 is 2026-09-01T12:00
    check("--text output", out,
          "sessions in scope: 2, whoami runs left out: 1, copied prompts left out: 0, prompts: 1\n"
          "\n"
          "## session s2  " + ROOT + "\n"
          "[2026-09-01T12:00] fix the build\n")
    check("--text stderr silent", err, "")


def test_main_text_reports_copies_dropped():
    code, out, err = _main_text({"proj/s1.jsonl": [_user("fix the build", session="s1", ts=T1)],
                                 "proj/s2.jsonl": [_user("fix the build", session="s2", ts=T1),
                                                   _user("new work", session="s2", ts=T2)]})
    check("--text with a copy exits 0", code, 0)
    # s2 keeps only its new prompt, and time[:16] of T2 is 2026-09-01T11:00
    check("--text with a copy output", out,
          "sessions in scope: 2, whoami runs left out: 0, copied prompts left out: 1, prompts: 2\n"
          "\n"
          "## session s1  " + ROOT + "\n"
          "[2026-09-01T10:00] fix the build\n"
          "\n"
          "## session s2  " + ROOT + "\n"
          "[2026-09-01T11:00] new work\n")
    check("--text with a copy stderr silent", err, "")


def test_text_on_cp1252_console():
    text = "\u7e41\u9ad4\u4e2d\u6587 prompt"
    projects = _projects({"proj/s1.jsonl": [_user(text)]})
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run([sys.executable, _SCRIPT, ROOT, "--projects", projects, "--text"],
                          capture_output=True, env=env, timeout=60)
    check("--text subprocess exits 0", proc.returncode, 0)
    check("--text subprocess stderr empty", proc.stderr, b"")
    # the script's text-mode stdout writes \r\n on Windows, so only that is folded back before the whole output is compared
    check("--text output with a non-ASCII prompt survives as UTF-8",
          proc.stdout.replace(b"\r\n", b"\n").decode("utf-8"),
          "sessions in scope: 1, whoami runs left out: 0, copied prompts left out: 0, prompts: 1\n"
          "\n"
          "## session s1  " + ROOT + "\n"
          "[2026-09-01T10:00] " + text + "\n")


_TESTS = (test_human_prompt_extracted, test_model_written_records_excluded, test_human_prompt_predicate,
          test_scope, test_normalise, test_nested_cwd_does_not_decide, test_strip_pastes, test_text_of,
          test_sessions_in_scope, test_session_falls_back_to_file_name, test_sorted_by_timestamp,
          test_sorted_ties_broken_by_session,
          test_drop_whoami_sessions_detection, test_drop_whoami_sessions_drops_whole_session, test_drop_copies,
          test_as_text,
          test_main_exit_status, test_non_ascii_on_cp1252_console,
          test_main_leaves_out_whoami_runs, test_main_exit_status_judged_before_drop,
          test_main_keeps_the_copy_from_the_first_session, test_main_reports_copies_dropped,
          test_main_drops_whoami_runs_before_copies, test_main_text, test_main_text_reports_copies_dropped,
          test_text_on_cp1252_console)


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
