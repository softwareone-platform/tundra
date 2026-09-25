---
name: whoami
description: A self-assessment from the code shipped under your name, the prompts you gave Claude, and the instructions you left it. It tests each pattern against what could explain it away, and reports what it concludes about how you work.
argument-hint: "[repositories and report language, in your own words]"
disable-model-invocation: true
---

# whoami

Read what one person produced, find what recurs in it, test each pattern against what could explain it away, and conclude what it says about how they work. The person running this is the person being assessed. There are three sources:

- the **code** that shipped under their name, typed by them or by a model they directed, which shows what they build and what they let through;
- the **prompts** they gave Claude, which show how they plan and steer;
- the **instructions** they left for Claude in `CLAUDE.md` files, which record what they decided matters, and often what went wrong before they wrote it down.

The report is a conclusion backed by evidence, rendered as an HTML file with a Markdown summary in the session. It carries no type, score, rating, or label, and it compares the person with no other named person.

**Ask only what the material cannot show.** The person answers questions about scope and context in steps 1 and 2. Every finding after that is tested by you against the material, never put to the person for confirmation.

## 1. Scope

Arguments: $ARGUMENTS

The arguments are a request in the person's own words, such as "~/source/repos/orders and ~/source/repos/accounts, in German". Read two things from it:

- **where**: repositories, or directories whose immediate subdirectories are repositories. A relative path is relative to the working directory. Check that each one exists, and ask about one that does not in the confirmation below rather than searching the disk for it, because the scope is only what the person names. With no path in the request, the scope is the repository this session is in.
- **the language** the report is written in. Without one, it is the language the person has been using with you.

State what you read from the request in the confirmation, so a misreading is caught before anything is read.

Read code **through git** only: `git log`, `git show <sha>`, `git show <sha>:<path>`, `git grep <pattern> <sha>`, `git blame <sha> -- <path>`. Leave the working tree alone, so untracked and ignored files (local settings, secrets, unfinished work) stay out of the analysis whatever the scope directory holds. The one exception is the instruction files step 3 names, read by name and never by listing the tree. In Git Bash on Windows, set `MSYS_NO_PATHCONV=1` for any command that takes `<rev>:<path>`, because the shell otherwise rewrites the argument into a Windows path.

Confirm the repositories, the language, and the sources in one AskUserQuestion call. Say plainly what each source sends to the model:

- **code**: the diffs of their commits and the code around them, the same way a file reaches the model when they ask Claude to read it;
- **prompts**: only the text they typed, with pasted content replaced by its size, from the transcripts on this machine. Those prompts reached the model once already, when they were typed. The transcripts go back only as far as Claude Code's retention setting keeps them;
- **instructions**: the `CLAUDE.md` in each repository, read through git; the instruction files in each repository that git does not track, such as a gitignored `CLAUDE.md` or `CLAUDE.local.md`, read from the working copy; and their own user-level `~/.claude/CLAUDE.md` and `~/.claude/rules/`, which live outside git.

Tell what each repository is from the material rather than asking: an experiment or proof of concept usually says so in its name, its README, or its commit messages, and judge its patterns against what an experiment needs. A clone kept only for reading holds none of the person's commits, so it drops out at step 3 without being singled out.

Do not ask who typed the code or when an AI tool started taking part. Nobody remembers that date, and it does not decide what the code says about the person: code a model wrote under their direction shipped under their name, shaped by what they asked for and what they let through.

Done when the person has confirmed the repositories, the language, and the sources.

## 2. Identity

One person commits under several identities: a different email per host, a name a web UI writes as `Last, First`, the same address in different letter case, a GitHub noreply address.

1. Seed from `git config user.name` and `git config user.email` in each repository.
2. Normalise authors: email lower-cased, `Last, First` turned to `First Last`, `<id>+<login>@users.noreply.github.com` reduced to its login.
3. Link transitively: an author whose normalised name or email matches one already in the set joins it.

List only the linked candidates, never every author in the history. Confirm them with AskUserQuestion (multiSelect), with commit counts. A common name links strangers, so the confirmation is what makes the set correct.

The confirmed set must still contain a seed identity. When it does not, the assessment is of someone else, so say that and stop.

Done when the person has confirmed their identities.

## 3. Material

**Code.** Take the person's non-merge commits on each repository's default branch (`origin/HEAD`, or `HEAD` when there is no remote), with one `--author` per confirmed email. The default branch is what shipped. Feature branches hold work in progress, and release branches mostly hold backports of changes the default branch already has.

Leave out what was not authored, judged by shape: **generated files**, meaning many similar files added in one commit under the path the ecosystem's tool writes to (migrations a tool generated, lock files, clients generated from a spec), and **imports**, meaning a large commit that only adds files and deletes nothing. A large commit that changes existing files is authored work however many files it touches, so read a sample of it rather than leaving it out.

Read up to 100 changes, unless the person asked for a different number: the 60 newest, and 40 spread evenly across everything older. The recent past is read densely because it shows how the person works now. The older history is read thinly, but it is read, because step 5 needs instances from before AI shows up, and a gap that stops appearing is worth reporting too. With 100 or fewer changes, read them all. Read them a commit at a time.

**Where AI shows up.** For each repository, find the earliest evidence that an AI tool took part: the commit that added `CLAUDE.md` or `.claude/`, the first Claude Code session in the repository, a pipeline's artefacts committed alongside the code (review reports, learnings files), or a `Co-Authored-By` trailer naming a model. This date excludes nothing. It lets step 5 ask whether a code pattern is the person's or the tool's, and it goes in the scope. When there is no such evidence, say so.

**Prompts**, when the person agreed:

```
python "${CLAUDE_SKILL_DIR}/scripts/extract_prompts.py" <repository> [<repository> ...] --text
```

It prints the typed prompts as text, grouped by session, with how many sessions ran inside the repositories. Read that output as it is. It leaves out earlier whoami runs, whose prompts are answers to this skill's own questions, and the copies a resumed or forked session carries of an earlier conversation. Exit status 3 means sessions were found but none held a typed prompt, which is what a change to the transcript format looks like. Say so, and carry on without prompts rather than reporting that there were none. When Python is not available, say so and stop, because the report cannot be rendered without it either.

**Instructions**, when the person agreed. Read each repository's `CLAUDE.md` and `.claude/rules/` at the default branch's tip. A repository's file is often shared, so attribute its lines with `git blame` and take only the person's, dated by their commits. Blame names who committed a line, not who wrote it. Then read, from each repository's working copy, the instruction files git does not track: `CLAUDE.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md`, and `.claude/rules/*.md`, and nothing else in the working copy, since `.claude/` also holds local settings. A file kept out of git is written for the person's own sessions, so it says more about how they steer than a shared one does. Read the user-level `~/.claude/CLAUDE.md` and `~/.claude/rules/` whole. Both the untracked files and the user-level ones are the person's own, but they have no history, so they are undated.

Every instruction the person keeps counts as their choice, whoever first wrote it, for the same reason code a model wrote counts as theirs: they chose it to steer with. Do not guess at copying from how often a file changed. When a file states where it came from, such as a generator's header or a note that it was copied, say so in the scope, and describe a pattern drawn from it as a rule the person adopted rather than one they wrote.

Done when you hold the changes to read, one line per group you left out saying why, the prompts or the reason there are none, and the instructions or the reason there are none.

## 4. Patterns

**In code**, read each change's diff, then enough of the code at that commit to see the **other end of each seam** it touches: the callers of what it changed, the consumers of a type it altered, the configuration it relies on.

**Read the code in parallel** so the person does not wait on one reader. Split the changes into batches by size, about 3,000 changed lines and at most a dozen changes to a batch, counting lines with `git show --shortstat` and leaving out generated files. A reader's context grows with what it reads, not with how many commits it is given: in one trial, 14 changes holding 12,000 changed lines went to four readers, and each still reached about 200,000 tokens. Spawn one fresh subagent per batch, all in the same message. Use fresh subagents, not forks: a fork carries this whole session's context into every call it makes, and in one trial four forks read over five times the tokens the session itself did. Give each subagent everything it needs in its prompt, since it sees nothing else:

- the repository path, its batch of commit SHAs, and the confirmed author emails;
- that it reads through git only, never the working tree, and sets `MSYS_NO_PATHCONV=1` in Git Bash;
- the rules of this step: the other end of each seam, one mechanism per pattern, a strength with its exceptions, and each instance in plain language with its citation;
- that it returns its candidate patterns and also every **single event**, because an event in one batch and an event in another can make a pattern only you can see.

While they read, read the prompts and instructions yourself. When they report, merge their findings: combine events across batches into patterns, and join candidates from different batches that share a mechanism. Calibration, testing, and the conclusion stay with you.

**In prompts**, read them session by session, in order, and look at how the person plans and steers: whether they state a constraint up front or add it after the model has gone wrong, which kinds of decision they overturn or correct, and what they ask to have verified and what they accept without asking.

**In instructions**, read each rule as a decision about what matters. A prohibition usually records a failure the person saw, and the rules that cluster show where their attention goes. The wording may have been drafted by a model at their request, so read the decision, not the prose style.

A **pattern** has instances in at least two different changes, sessions, or rules, and one mechanism. A single occurrence is an event, not a pattern. Instances that would each need a different explanation are separate patterns, however alike they look. A pattern is one of three kinds:

- a **strength** is something the person does consistently well. List the instances where they did not do it too, because a strength with its exceptions is evidence, and a strength without them is flattery;
- a **gap** is something that keeps recurring. List the instances;
- a **style** is a way of working that is neither, a trade-off. State what it gives and what it costs.

Record each instance twice: in plain language, as what happened, and as a citation. Cite code as `repository sha path:line` with the line number at that commit, a prompt as `session time` with a short quote of the person's words, and a rule as `file:line`.

**Calibrate** a code pattern against the rest of the repository: look for it in code the person did not write, at the default branch's tip. When the rest of the repository does the same, it is the repository's convention, not the person's, so drop it. Use other people's code only in aggregate. Name, count, or quote no other author. A rate is only evidence alongside its counts, so write it as the counts (`4 of 21 migrations`), never as a rate alone.

Done when every change, session, and rule has been read, and each pattern has two or more cited instances, a strength has its exceptions, and a code pattern has a line of calibration.

## 5. Test each pattern

For each pattern, name the **constraint** that would make it something other than what it looks like. For a gap, that is a constraint under which it is not a weakness. For a strength, it is one under which it is not the person's doing. For a style, it is one under which the trade-off was not a choice. The constraint sits at the other end of the seam: a consumer outside the repository, a caller that guarantees the input, CI that enforces it, an environment that cannot be reproduced locally, the way responsibility was split, a standing instruction that already tells the model what the prompt seems to leave out.

**Look for the constraint yourself.** Much of it is in reach:

- a `<PackageId>` or a publish step shows a type is a contract consumed elsewhere;
- a pipeline shows what CI enforces, such as a coverage gate or an analyser;
- the code at that commit shows whether a caller guarantees the input, or whether a later process repairs what a script leaves behind;
- the instructions show whether a prompt's apparent omission is already a standing rule, and a rule dated before a correction shows the model broke it rather than the person leaving it out;
- **the tool's default** is the constraint for a code pattern that could be how a model or a pipeline writes rather than how the person works. Instances on both sides of the date AI shows up make the pattern the person's. When every instance comes after it, a gap and a strength part ways. A gap is still the person's, because they let it through. A strength or a style is not theirs merely because they let it through, since leaving well-made code alone is not the skill: it needs the prompts or instructions to show they asked for it, and without that it is conditional on not being how the model writes by default. Record this check in the pattern's `checked` for every pattern whose instances all come after the date.

Each pattern ends in one of four states:

- **dissolved**: you found the constraint and it holds. Keep the evidence;
- **holds**: you looked where the constraint would be, and it is not there;
- **narrowed**: the constraint holds for part of the instances, or it turns the pattern into something smaller. Often that is an invariant nobody wrote down;
- **conditional**: the constraint lies outside anything you can read, such as a read-only environment or how work was assigned. The pattern stands, stated as holding unless that constraint does.

Done when every pattern is in one of the four states, each with what you checked.

## 6. Conclusion

Read the patterns that hold, the narrowed ones, and the conditional ones together, and find what they share. The conclusion names an **axis** of the person's work: the kind of question they reliably get right, and the kind they reliably miss. Examples are whether a thing should exist against what happens at its boundary once it does, or the thing being built against the instrument that checks it.

One axis is the default. Name a second only when its mechanism differs from the first. The test is the summary: if one sentence can state both, they are one axis, so merge them. In one trial two axes split what the summary itself called four forms of one failure, and the report read as fragmented.

Each axis cites the patterns that support it, from more than one source where the sources agree. An axis is a tendency with evidence, never a type. When the patterns share nothing, say so rather than forcing an axis.

Weigh the recent past over the older history. A pattern whose instances all come from the older sample describes how the person used to work, so its description says so, and a gap that has stopped appearing is reported as possibly outgrown rather than as a current weakness.

Done when each axis cites the patterns behind it, or the report says the patterns do not add up to one.

## 7. Report

Write the report as one JSON document in the language from step 1, with its `language` code and every label in that language, following [`report-schema.md`](report-schema.md). Read the schema before you write the document. The reader is the person, not a reviewer of the analysis, so every field they see is in plain language: what happened, what it means, and why it matters. Identifiers such as commit SHAs, file paths, line numbers, and class names go only in an instance's `ref`, which the HTML keeps inside the collapsed evidence.

- **Summary**: several sentences a reader can take in without the tables, then the axis, with a description of what it is about, and a second one only when step 6 found one.
- **Diagrams**: add one wherever a shape explains better than a sentence, such as how a kind of defect gets found or missed, or how the axis plays out. Give it as structure, steps in lanes as the schema describes, never as text drawn into boxes. One idea per diagram, and a comparison as lanes side by side in one diagram. Each diagram shows an axis or a pattern from the report, never a dimension the conclusion does not name.
- **Strengths, gaps, and styles**: each pattern that holds or narrowed, stated in its narrowed form. Its confidence is `verified`, or `depends` with the constraint for a conditional one. Describe a code pattern as what shipped under the person's name, not as what they typed.
- **Implications**: what the axis means for how the person works. Say where a strength is leverage, where a gap will recur, and what their standing rules do not yet cover. Stay at the level of the pattern. How to fix a particular piece of code is not this report's subject.
- **Timeline**: the dates behind the scope, taken from what you read rather than estimated. For each repository, the dates of the oldest and newest changes read, from `git log`, and the date AI shows up. For the sample, the date where the newest changes begin. For the prompts, the first and last timestamps `extract_prompts.py` returned.
- **Scope**: the repositories and where AI shows up in each, the identities, how many changes were read from the recent past and from the older history and the dates each covers, how many sessions and rules were read, the dates the prompts span, and what was left out and why.
- **Dissolved** candidates and single **events** go in the appendix, which the HTML keeps collapsed.

Write the document to `${CLAUDE_PLUGIN_DATA}/reports/<date>-<scope>.json`. When a report of that name already exists, from an earlier run the same day, add `-2`, `-3`, and so on before the extension rather than replacing it, so both runs can be compared. Then render it:

```
python "${CLAUDE_SKILL_DIR}/scripts/render_report.py" <document.json> --out <same path, .html>
```

Exit status 2 lists what does not match the schema. Fix the document and render again. The renderer prints a Markdown summary of the report ending with the HTML file's path. Your last message is that summary, as printed, and nothing follows it: no question and no offer.

When the person asks for suggestions afterwards, keep them at the level of the pattern: how to work with a strength, and how to catch a gap where it recurs.
