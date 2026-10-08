# remind-me

What you left open in the Claude Code sessions you ran on a given day, grouped by repository and checked against the live state of the pull requests and branches they named, with a way back into each session in its own folder.

```
/plugin install remind-me@tundra
```

## Use

Ask in your own words, for example "what did I leave open yesterday?", or run `/remind-me:remind-me`, optionally with a day and a language: `/remind-me:remind-me last Friday, in German`. Without a day it reports the most recent day before today on which you typed a prompt in an interactive session, so on a Monday it reports Friday, and a day that holds only `claude -p` runs is skipped.

You get a summary in the session, and an HTML page that opens in your browser: the day's figures across all repositories, including token usage, a timeline of every session by repository, and everything left open. Select a repository or a session in the side panel or on the timeline for its topics, what it left open, what it decided, did and found out, and buttons that take you back into it in its own folder. The page is the day as it was, judged from that day's prompts alone, while pull requests and branches show their state as of now. It records the work rather than the data the work touched, so credentials, people's names, customer and account identifiers, and anything read from a production system are left out.

## What it reads

It reads the transcripts Claude Code keeps on this machine under `~/.claude/projects`, for every repository you worked in that day, and nothing leaves the machine. It runs `git` in each of those repositories, and `az` or `gh` to look up the pull requests the sessions named. Without `az` or `gh`, or without a login, a pull request's state is reported as unknown. The reports are written to the plugin's own data folder; the digest the report is built from, which holds excerpts of your transcripts, is deleted once the report is rendered. Claude Code deletes transcripts older than `cleanupPeriodDays`, 30 days by default, so a day older than that cannot be reported; set it higher in `~/.claude/settings.json` to look further back.

## Requirements

Python 3.9 or later and `git`. The Azure CLI with the `azure-devops` extension, or the GitHub CLI, for pull request states.
