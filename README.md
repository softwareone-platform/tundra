<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner/banner-dark.jpg">
  <img alt="tundra, Claude Code plugins" src="docs/banner/banner-light.jpg">
</picture>

[![Claude Code plugin marketplace](https://img.shields.io/badge/Claude%20Code-plugin%20marketplace-blue)](#install) [![License: Apache-2.0](https://img.shields.io/github/license/softwareone-platform/tundra)](LICENSE)

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

</div>

A Claude Code plugin marketplace for SoftwareOne Platform.

<a id="install"></a>
## 📦 Install

```
/plugin marketplace add https://github.com/softwareone-platform/tundra.git
```

Then install the plugins you want from it:

```
/plugin install <plugin>@tundra
```

Run `/reload-plugins` afterwards to activate them.

Claude Code leaves auto-update off for a marketplace like this one, so you will not receive new versions until you turn it on: `/plugin` → **Marketplaces** → `tundra` → **Enable auto-update**.

Use the full HTTPS URL above. The `softwareone-platform/tundra` shorthand is also accepted, but it clones over SSH, which is a different instruction.

<a id="what-is-in-it"></a>
## 🗂️ What is in it

Every skill except `whoami` starts when you describe the task in your own words, and each can also be run as `/<plugin>:<skill>`.

### 🔁 From ticket to pull request

Four plugins that take a ticket to a reviewed pull request. They live in [issue-to-pr](https://github.com/softwareone-platform/issue-to-pr) and are released together. `issue-to-pr-pipeline` runs the whole flow, and on Claude Code v2.1.143 or later installing it installs the other three, each of which also works on its own.

```
/plugin install issue-to-pr-pipeline@tundra
```

| Plugin | What it does | Skills |
|---|---|---|
| [`issue-to-pr-pipeline`](https://github.com/softwareone-platform/issue-to-pr#issue-to-pr-pipeline) | Takes one ticket from diagnosis to a reviewed pull request, stopping for your approval of the plan and again before the PR opens | `resolve-issue`<br>`resolve-issue-dashboard`<br>`resolve-issue-learnings` |
| [`disconfirm-first`](https://github.com/softwareone-platform/issue-to-pr#disconfirm-first) | Adversarial review of an issue, a plan, or an implemented fix, before the next step builds on it | `review-issue-fact`<br>`review-plan-risk`<br>`review-code-risk` |
| [`test-authoring`](https://github.com/softwareone-platform/issue-to-pr#test-authoring) | Finds test gaps and writes unit and integration tests, each checked by an independent verifier | `scan-test-gaps`<br>`add-unit-test`<br>`add-integration-test`<br>`update-unit-test`<br>`update-integration-test`<br>`setup-test-context` |
| [`pr-lifecycle`](https://github.com/softwareone-platform/issue-to-pr#pr-lifecycle) | Opens a pull request in the style of your past ones and resolves its review comments, on Azure DevOps or GitHub | `open-pr`<br>`resolve-pr-comments` |

![The resolve-issue-dashboard visualising a run mid-pipeline](https://raw.githubusercontent.com/softwareone-platform/issue-to-pr/main/docs/resolve-issue-dashboard.png)

### 🪞 How you work

| Plugin | What it does | Skills |
|---|---|---|
| [`whoami`](plugins/whoami/README.md) | A self-assessment from your code, your prompts, and your CLAUDE.md, as an HTML report | `/whoami:whoami`, which only you can start |
| [`remind-me`](plugins/remind-me/README.md) | What you left open in the sessions you ran on a given day, checked against the live state of each pull request and branch, with a way back into each session | `remind-me` |

`whoami` reads the code that shipped under your name, the prompts you gave Claude, and your `CLAUDE.md`, tests each recurring pattern against what could explain it away, and concludes what kind of question you reliably get right and what kind you reliably miss. It reads only the repositories you name, and your prompts and instructions only if you agree. It writes the report in whichever language you ask for.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="plugins/whoami/docs/report-overview-dark.png">
  <img alt="A whoami report on two fictional repositories: a timeline of what was read, the conclusion with one axis, and a flow diagram" src="plugins/whoami/docs/report-overview-light.png">
</picture>

`remind-me` reads the transcripts Claude Code keeps on this machine and looks up the live state of every pull request and branch they named, so a pull request merged the same afternoon is not reported as pending. It writes a summary in the session and an HTML page with a timeline of the day by repository, in whichever language you ask for.
