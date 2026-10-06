# CLAUDE.md

This covers the `remind-me` plugin. The registry-level rules are in the repository root's `CLAUDE.md`.

## What it is

One skill and two scripts. `scripts/collect.py` turns a day of transcripts into a digest and looks up what is true now; `scripts/render.py` turns the model's judgement and that digest into an HTML page and the summary the session prints. The model reads only the digest and writes only the judgement: topics, what got done, what is open. It came out of a manual reconstruction of one working day, which settled its shape.

## Decisions that are easy to undo by accident

- **Live state is required, not an option.** The manual reconstruction listed six pull requests as awaiting review that had been completed the same day. A transcript records what was pending when a session stopped, so every pull request a session names is looked up, and a state that cannot be read is reported as unknown, never as pending.
- **Figures come from scripts, judgement from the model.** Times, token usage, pull request states, folders and the commands that reopen a session are copied from the digest by the renderer, never written by the model, so no figure on the page can be invented.
- **Resuming from the page is a link, not a command.** A `claude-cli://open` link with the session folder as `cwd` and `/resume <id>` as the prompt opens a new terminal in that folder with the command filled in, behind Claude Code's own "Prompt from an external link" warning, and Enter resumes the session there (checked by hand on Windows, 2026-10-06). The copyable command stays for when the link handler is not registered.
- **Cache reads are shown apart, not dropped.** They are the conversation history re-read on every request, so they measure how long and how large the sessions grew. They are cheap per token (0.05 times the base input price on Opus 5.5) but not free: on the day of the manual trial, 213M of them came to about 10.7M tokens at the base input price, more than the 4.3M written to the cache even with every write counted at the one-hour price of twice the base (8.6M).
- **No session title.** Claude Code's generated title names a session after its first prompt, and sessions drift: the one that started the manual trial was titled after listing last Friday's sessions and spent the day on mods and READMEs. The collector does not carry the title, and the model lists topics.
- **The day defaults to the last day with sessions, not to yesterday.** A Monday run reports Friday, and no calendar is needed to know that.
- **The current session counts.** A session resumed today in the repository where yesterday's work happened holds that work, so excluding the current session would drop it. Only the request that runs this skill is left out.
- **Resume always from the session's folder.** `claude --resume <id>` finds a session from any folder, but Claude Code is folder-scoped and a resume from elsewhere runs the session in the wrong one. Every resume command changes to the folder first, and each card also offers a plain terminal there, for when opening Claude Code is not allowed.
- **Azure DevOps ids are looked up across the collection, GitHub only from a full URL.** Azure numbers pull requests across a whole collection, so a bare `!151943` is enough with the organisation of any Azure repository in the digest. A GitHub number means nothing without its repository. A session in a GitHub repository can name Azure pull requests, and looking them up through `gh` returned nothing for all nine in the first run.
- **Lookups run in parallel.** One session that labelled old pull requests named 43 of them, and looking them up one at a time took 75 seconds against 14 in parallel.
- **No tables in the session summary.** The terminal prints a `<br>` inside a table cell literally, so the summary is headings and bullets.
- **A session is one bar, from its first entry to its last.** Drawing each stretch of activity as its own block read as several sessions. The stretches stay in the digest and show in the session's header, and two sessions that ran at the same time in one repository get a row each.
- **A running session cannot be resumed from the page, the current one included.** Resuming it would open the same session twice. A new session in the same folder is safe, because each session has its own id: two ran side by side in one repository on the day of the manual trial. The repository the report was asked from is marked rather than disabled.
- **Retention is not read.** The page never needed the value: the default day is one that has sessions, so its transcripts exist. Only a day with nothing found needs it, and there a sentence naming `cleanupPeriodDays` and its default does the job. Reading it properly meant five sources in Claude Code's own precedence, one of them an undocumented cache file, for that one sentence.
- **The headline carries no counts.** It is the one line the model writes above the figures, and the first version said 51 pull requests where the day had opened 7.
- **`claude -p` runs are left out by `entrypoint`.** Their transcripts say `sdk-cli`. They are scripts and probes, not work a person left open.
