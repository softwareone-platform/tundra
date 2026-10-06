# remind-me

What you left open in the Claude Code sessions you ran on a given day, grouped by repository and checked against the live state of every pull request and branch they touched, with a way back into each session in its own folder.

```
/plugin install remind-me@tundra
```

## Use

Ask in your own words, for example "what did I leave open yesterday?", or run `/remind-me:remind-me`, optionally with a day and a language: `/remind-me:remind-me last Friday, in German`. Without a day it reports the most recent day before today that has sessions, so on a Monday it reports Friday.

You get a summary in the session, and an HTML page that opens in your browser: the day's figures across all repositories, including token usage, a timeline of every session by repository, and everything left open. Select a repository or a session on the timeline for its topics, what it left open, what it finished, and buttons that take you back into it in its own folder.

## What it reads

It reads the transcripts Claude Code keeps on this machine under `~/.claude/projects`, for every repository you worked in that day, and nothing leaves the machine. It runs `git` in each of those repositories, and `az` or `gh` to look up the pull requests the sessions named. Without `az` or `gh`, or without a login, a pull request's state is reported as unknown. The digest and the reports are written to the plugin's own data folder. Claude Code deletes transcripts older than `cleanupPeriodDays`, 30 days by default, so a day older than that cannot be reported; set it higher in `~/.claude/settings.json` to look further back.

## Requirements

Python 3.9 or later and `git`. The Azure CLI with the `azure-devops` extension, or the GitHub CLI, for pull request states.
