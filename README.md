# tundra

A Claude Code plugin marketplace for SoftwareOne Platform.

## Install

```
/plugin marketplace add https://github.com/softwareone-platform/tundra.git
```

Then install the plugins you want from it:

```
/plugin install <plugin>@tundra
```

Run `/reload-plugins` afterwards to activate them.

Use the full HTTPS URL above. The `softwareone-platform/tundra` shorthand is also accepted, but it clones over SSH, which is a different instruction.

## What is in it

| Plugin | What it gives you |
|---|---|
| [`issue-to-pr-pipeline`](#issue-to-pr-pipeline) | Takes one ticket from diagnosis to a reviewed pull request, chaining the three below |
| [`disconfirm-first`](#disconfirm-first) | Adversarial review of an issue, a plan, or an implemented fix |
| [`test-authoring`](#test-authoring) | Unit and integration test authoring, each written by one agent and checked by another |
| [`pr-lifecycle`](#pr-lifecycle) | Opening a pull request and resolving its review comments, on Azure DevOps or GitHub |

### From [issue-to-pr](https://github.com/softwareone-platform/issue-to-pr)

These four plugins live in their own repository and are released together. Each section below is the opening of that plugin's own documentation.

#### issue-to-pr-pipeline

`resolve-issue` drives one ticket through fact-checking the issue, drafting and hardening a plan, implementing, writing tests, reviewing the fix, and opening the PR. It stops for your approval of the plan before any code changes, and again before the PR is opened.

![The resolve-issue-dashboard visualising a run mid-pipeline](https://raw.githubusercontent.com/softwareone-platform/issue-to-pr/main/docs/resolve-issue-dashboard.png)

It declares the other three plugins as dependencies, so on Claude Code v2.1.143 or later installing it installs them too:

```
/plugin install issue-to-pr-pipeline@tundra
```

[Full details of issue-to-pr-pipeline](https://github.com/softwareone-platform/issue-to-pr#issue-to-pr-pipeline)

#### disconfirm-first

Three adversarial reviewers, one per altitude: `review-issue-fact` checks an issue against the code before a fix is planned, `review-plan-risk` pre-mortems a plan or spec, and `review-code-risk` challenges an implemented fix before the PR opens.

[Full details of disconfirm-first](https://github.com/softwareone-platform/issue-to-pr#disconfirm-first)

#### test-authoring

Finds test gaps and writes or refreshes unit and integration tests. Every test comes from a writer agent that learns the conventions of the nearest sibling tests, and is checked by an independent verifier. Nothing is copied into your repository.

[Full details of test-authoring](https://github.com/softwareone-platform/issue-to-pr#test-authoring)

#### pr-lifecycle

`open-pr` opens a PR whose title and description follow your own past PRs, and `resolve-pr-comments` triages a PR's review threads and drafts the fixes and replies. Both show you what they will do and wait for a yes before changing anything outside your machine.

[Full details of pr-lifecycle](https://github.com/softwareone-platform/issue-to-pr#pr-lifecycle)

## Licence

Apache-2.0, covering everything in the repository.
