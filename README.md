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

### 🔁 From ticket to pull request

![Thumbnails of the resolve-issue-dashboard: its runs, the pipeline ring, an agent's tool calls and the token figures](docs/issue-to-pr/thumbnails.png)

Four plugins that take a ticket to a reviewed pull request. They live in [issue-to-pr](https://github.com/softwareone-platform/issue-to-pr) and are released together. `issue-to-pr-pipeline` runs the whole flow, and on Claude Code v2.1.143 or later installing it installs the other three, each of which also works on its own.

```
/plugin install issue-to-pr-pipeline@tundra
```

| Plugin | What it does |
|---|---|
| [`issue-to-pr-pipeline`](https://github.com/softwareone-platform/issue-to-pr#issue-to-pr-pipeline) | Takes one ticket from diagnosis to a reviewed pull request, stopping for your approval of the plan and again before the PR opens |
| [`disconfirm-first`](https://github.com/softwareone-platform/issue-to-pr#disconfirm-first) | Adversarial review of an issue, a plan, or an implemented fix, before the next step builds on it |
| [`test-authoring`](https://github.com/softwareone-platform/issue-to-pr#test-authoring) | Finds test gaps and writes unit and integration tests, each checked by an independent verifier |
| [`pr-lifecycle`](https://github.com/softwareone-platform/issue-to-pr#pr-lifecycle) | Opens a pull request in the style of your past ones and resolves its review comments, on Azure DevOps or GitHub |

### 🪞 How you work

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/whoami/thumbnails-dark.png">
  <img alt="Thumbnails of a whoami report: its timeline, its conclusion, a flow diagram and its tables" src="docs/whoami/thumbnails-light.png">
</picture>

| Plugin | What it does |
|---|---|
| [`whoami`](plugins/whoami/README.md) | A self-assessment from your code, your prompts, and your CLAUDE.md, as an HTML report. Only you can start it, with `/whoami:whoami` |
| [`remind-me`](plugins/remind-me/README.md) | What you left open in the sessions you ran on a given day, checked against the live state of each pull request and branch, with a way back into each session |
