# CLAUDE.md

This covers the `whoami` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## What it is, and why it is this small

One skill, a `SKILL.md`, and two scripts: `scripts/extract_prompts.py` gets the person's prompts out of the transcripts, and `scripts/render_report.py` turns the report document into HTML. It came out of a manual trial on one developer's repositories, and the trial settled its shape:

- **Findings a script could compute did not survive the interview.** Commit bursts, ratios, where contributions concentrate: every one of them dissolved once the author named the constraint behind it. The findings that survived all came from reading the code itself. So there is no mechanical analysis layer. The model reads the diffs, the prompts, and the instructions, and the scripts only extract and render.
- **Testing each pattern against its constraint is the instrument.** In the trial, constraints the author knew overturned most of the conclusions drawn from the code. So every pattern is tested before it reaches the conclusion, and it ends in one of four states: dissolved, holds, narrowed, or conditional.
- **The model finds the constraint itself, and does not ask.** The first version put every finding to the person for confirmation, and it failed in two ways. A gap dissolved whenever the person accepted the constraint the model had proposed, without either of them checking it, and the model's own later suggestions doubted one of those dissolutions. And the person had to answer a dozen questions to get one report. Of the four constraints that dissolved findings in the trial, two were in the repository all along, a `<PackageId>` and a Sonar stage in the pipeline. So the model checks what is in reach, and a constraint out of reach makes the pattern conditional rather than becoming a question.
- **The constraint sits at the other end of a seam**: a consumer, a caller, CI, the environment, how responsibility was split, a standing instruction. A fixed table mapping finding shapes to constraints was tried and failed on cases it had not been built from, so the skill carries the rule and no table.
- **The report leads with a conclusion.** A list of confirmed patterns leaves the reader to do the synthesis, and the trial's useful output was a synthesis: the kind of question the author reliably got right, against the kind they reliably missed.

## Decisions that are easy to undo by accident

- **User-invoked only** (`disable-model-invocation: true`). It is never the answer to a task, so a description in every session's context would buy nothing. Turning model invocation on would also let it fire on "who am I" questions.
- **Ask only what the material cannot show.** Scope, sources, and identity are questions, because nothing in the history answers them. Nothing after that is. An earlier version also asked what each repository was, and it did not need to: an experiment says so in its name, README, or commits, and a clone kept for reading has none of the person's commits and drops out on its own.
- **Code only through git, never the working tree.** A scope directory can hold secrets next to its repositories, and a repository can hold ignored local settings. The transcripts and the user-level `CLAUDE.md` are the reads outside git, and the user agrees to them in the same question as the repositories.
- **Instructions are a source, not only context.** A rule records a decision about what matters, and a prohibition usually records a failure the person saw. The same files answer whether a prompt left something out or relied on a standing rule, which the first version asked the person instead. A repository's `CLAUDE.md` is often shared, so only the lines `git blame` gives to the person count.
- **A strength carries its exceptions.** The first run reported six strengths and one gap, and the model's own counts held exceptions to a strength that the report left out.
- **One mechanism per pattern.** The first run put a concurrency tuning, a series of timeout changes, a series of nullability fixes, and a reverted design under one pattern and one constraint. In the trial the nullability fixes had been dissolved by a quite different fact.
- **Counts, never a bare rate.** The first run compared "0.82 against 0.08 per thousand lines" where the person's side was two comments.
- **The scope is only what the user passes, and the default is the current repository.** An earlier design scanned for every repository on the machine. That reaches repositories the user has never opened in Claude, which is new exposure the user did not choose.
- **All code is read, whoever typed it, and nobody is asked when AI started.** An earlier version read only a hand-written period the person gave, and it failed on its own premise: in one trial the person answered "all hand-written" for a repository whose commits carried a review pipeline's reports and learnings files, because nobody remembers the date. Detecting AI-written code from its style was tried and failed too. Matching commits against the transcripts' own file edits was measured and found one of fourteen commits in a repository that was wholly AI-implemented. And the premise itself was wrong: code a model wrote under the person's direction shipped under their name, shaped by what they asked for and what they let through. So every code pattern is described as what shipped, not as what was typed.
- **The date AI shows up calibrates, and excludes nothing.** It is found from the history: the commit that added `CLAUDE.md` or `.claude/`, the first session, pipeline artefacts, a model's co-author trailer. Step 5 uses it to tell the person's pattern from the tool's default. Seen on both sides of the date, a pattern is the person's. Seen only after, it needs the prompts or instructions to show the person asked for it, or it is conditional.
- **The sample is weighted to the recent past.** Up to 100 changes: the 60 newest, and 40 spread across everything older. Reading only the newest 50, as an earlier version did, left a heavy AI user's sample almost all after AI showed up, so nothing could tell the person's pattern from the tool's. Spreading evenly would have given old habits the same weight as current ones. A pattern seen only in the older sample is reported as how the person used to work.
- **Code is read by fresh subagents in parallel, never by forks.** A single reader over fifty commits kept the person waiting. The model's own first attempt at parallel reading used forks, and the four forks read over five times the tokens the session did, because a fork carries the whole session into every call. A fresh subagent gets only a brief. Each batch also returns its single events, because two events in two batches can make a pattern no batch sees.
- **The default branch only.** Squash merges put the shipped change there, and backports repeat it on release branches, which would count one change twice and fake a "fixed in several places" pattern.
- **Other authors only in aggregate.** Calibration looks at the rest of the repository to drop what is the repository's convention rather than the person's. A per-person figure for anyone else is a workplace-monitoring output this plugin must never produce.
- **No labels, scores, or ratings.** A pattern with its instances is actionable. A type is not, and an axis is a tendency with evidence, never a type.
- **The summary is the last thing written, and the report stays at the level of the pattern.** The first run asked a question after the report, then followed it with advice nobody asked for, which left the reader unsure whether the run had ended. When asked for suggestions, it offered code fixes, which are not this skill's subject.
- **The reader sees plain language, and identifiers stay in the evidence.** The second run's report was correct and hard to read: commit SHAs, file names, and line numbers in every sentence, and pattern codes cited in the conclusion before they were defined. So every visible field describes what happened, and a citation lives only in an instance's `ref`, collapsed in the HTML. The citations are kept rather than dropped, because they are how a wrong claim gets caught: the second run cited a line number from the tip against a commit's SHA.
- **Only patterns that hold are shown, each with a confidence.** A narrowed pattern is shown in its narrowed form, and a conditional one as `depends` on its constraint. Dissolved candidates are in the collapsed appendix, where a maintainer checking the skill can still see them.
- **Styles are a third kind of pattern.** A trade-off such as tuning a value by deploying and watching is neither a strength nor a gap, and forcing it into either misreports it. A style states what it gives and what it costs.

## The prompt extractor

The transcript format is not documented, so the extractor rests on fields observed in real transcripts rather than on a contract:

- a typed prompt is a `user` record carrying `origin: {"kind": "human"}`;
- task notifications and messages from other sessions carry other `origin` kinds;
- skill bodies are marked `isMeta`, compaction summaries `isCompactSummary`, and subagent turns `isSidechain`, and all three are excluded whatever their `origin` says, because the model wrote them;
- a headless `claude -p` prompt carries no `origin`, so it is excluded too;
- a paste arrives as a `<pasted_content>` block, optionally with an `id`, and is replaced by its size because the person did not write it.

**It fails loudly.** When sessions ran in scope but none held a typed prompt, it exits 3, because that is what a renamed field looks like, and a silent empty result would read as "this person gave no prompts".

**Scope is decided by each record's own `cwd`**, compared on path boundaries. The project directory names under `~/.claude/projects` encode paths lossily, so matching on them puts `repo-a-generator` inside `repo-a`.

Its output is forced to UTF-8, because a Windows console defaults to cp1252 and fails on the first prompt written in another script. `--text` prints the prompts grouped by session, because in both trial runs the model wrote its own code to print the JSON and hit that same cp1252 failure.

**It drops copies of the same prompt.** A resumed or forked session writes the earlier conversation into a new transcript file, so one typed prompt can appear in two sessions with the same timestamp and text. In the third trial run, 14 of 594 prompts were such copies. Counted twice, one prompt looks like a pattern seen in two different sessions, which is the very threshold a pattern needs. The first copy is kept, and ties are broken by session so the choice does not depend on file order.

**It leaves out earlier whoami runs.** A session whose prompt invoked `/whoami` holds the person's answers to this skill's own questions, not how they steer. The format check is made before these are dropped, because the run in progress is always one of them, and a first run in a repository with no other sessions would otherwise report a changed format.

Its checks are in `skills/whoami/tests/extract_prompts_tests.py`, and every one is a known-answer case over a synthetic transcripts directory. Run them with `python plugins/whoami/skills/whoami/tests/extract_prompts_tests.py`. A new record kind, or a field seen to change, gets a case there before the extractor is changed.

## The report renderer

The model writes the report as a JSON document following `skills/whoami/report-schema.md`, and `render_report.py` turns it into one HTML file and prints the Markdown summary. The model does not write HTML, because a layout the model writes differs from run to run and cannot be tested, while a renderer gives every report the same shape and can be checked with fixtures.

- **It validates before it writes.** A document that breaks the schema exits 2 with a list of what is wrong, and nothing is written, so the model fixes the document instead of shipping a half-rendered page.
- **It escapes everything.** Report text quotes the person's prompts and code, which can hold markup, so no field is ever inserted into the page unescaped.
- **Diagrams are structure, laid out by CSS.** The model used to draw diagrams as text boxes, and they misaligned in the HTML: a monospace font has no CJK glyphs, and the fallback font's CJK characters are not exactly two columns wide, so borders drift even when the model pads by East Asian width. SVG would have the same problem, since SVG text does not wrap and the renderer would have to measure it. So a diagram is lanes of steps, each step a box the browser wraps and sizes, with an arrow and an optional note between steps. The Markdown summary lists the steps with arrows instead of drawing boxes.
- **No charting library.** d3 was considered and declined: it would either load from a CDN, breaking the offline page, or add a few hundred kilobytes to every report; its SVG text does not wrap, which is the failure the CSS boxes remove; the diagrams hold steps rather than data; and a page drawn by script cannot be checked by the renderer's known-answer tests.
- **One chart, the timeline, and only of dates.** Donuts and bars of the scope's counts were considered and declined: the counts describe the sample rather than the person, a chart of them draws the eye away from the conclusion, a gadget reading "2 prompts" looks like a low score, and a count the model writes cannot be checked by the renderer. The timeline earns its place because it shows what the conclusion rests on. In one trial the code covered ten months and the prompts two days, which the scope line stated and nobody would notice. It sits above the conclusion for that reason, it draws the thin older sample hatched apart from the newest changes, which were all read, and every date in it is measured.
- **It loads nothing.** Styles are inline and there is no script, so the page opens offline and sends nothing anywhere. The evidence and the appendix collapse with `<details>`, which needs no script.
- **Headings come from the document's `labels`**, with English defaults, because the report is written in the person's language and the renderer cannot know it.
- **The report is written under `${CLAUDE_PLUGIN_DATA}`**, outside every repository, so it cannot be committed by accident.

Its checks are in `skills/whoami/tests/render_report_tests.py`. Run them with `python plugins/whoami/skills/whoami/tests/render_report_tests.py`.

## Measure before claiming

This is a measuring tool, so it gets checked harder than what it measures. Before a claim about what it finds goes into its README, run it on developers from populations the trial did not cover: a team repository and a solo one, and greenfield and maintenance work. One developer's run proves only that developer's case.

The experiment's kill criterion is in the README's Status section. The skill does not ask for the signal, because a question after the report makes the report look unfinished. Ask the people who try it, outside the run, whether its conclusions told them anything they did not already know.

## Conventions

A description is capped by the Agent Skills specification at 1,024 characters. Keep this one short anyway, because a user-invoked skill's description is only a one-line summary.

No `model:` in frontmatter. The consumer chooses the model.

Bump `version` in `.claude-plugin/plugin.json` on every change a user should receive, because the version is what makes Claude Code offer the update.
