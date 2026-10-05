<!-- translated from README.md, source sha256 75de0b8f03095f15f5d7dccc3b5aa6075597aef81ec957e0ed4b9c91e139dc23; see CLAUDE.md "Translations of the root README" before editing -->
# tundra

<div align="center">

[![Claude Code plugin marketplace](https://img.shields.io/badge/Claude%20Code-plugin%20marketplace-blue)](#install) [![License: Apache-2.0](https://img.shields.io/github/license/softwareone-platform/tundra)](LICENSE)

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

</div>

SoftwareOne Platform 的 Claude Code plugin marketplace。

<a id="install"></a>
## 📦 安装

```
/plugin marketplace add https://github.com/softwareone-platform/tundra.git
```

然后从中安装你需要的 plugin：

```
/plugin install <plugin>@tundra
```

之后运行 `/reload-plugins` 来启用它们。

对于这样的 marketplace，Claude Code 默认不开启自动更新，所以在你开启之前都不会收到新版本：`/plugin` → **Marketplaces** → `tundra` → **Enable auto-update**。

请使用上面完整的 HTTPS URL。`softwareone-platform/tundra` 这种简写也能用，但它通过 SSH clone，是不同的指令。

<a id="what-is-in-it"></a>
## 🗂️ 内容

| Plugin | 提供什么 |
|---|---|
| [`issue-to-pr-pipeline`](#issue-to-pr-pipeline) | 把一个工单从诊断带到经过审查的 pull request，串起下面三个 plugin |
| [`disconfirm-first`](#disconfirm-first) | 对 issue、计划或已实现的修复进行对抗式审查 |
| [`test-authoring`](#test-authoring) | 编写单元测试和集成测试，每个测试由一个 agent 编写、另一个 agent 检查 |
| [`pr-lifecycle`](#pr-lifecycle) | 在 Azure DevOps 或 GitHub 上创建 pull request，并处理它的评审意见 |
| [`whoami`](#whoami) | 根据你的代码、你的 prompt 和你的 CLAUDE.md 做一份自我评估，总结出你的工作方式，输出为 HTML 报告 |

### 来自 [issue-to-pr](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md)

这四个 plugin 放在它们自己的 repo 中，并且一起发布。下面每一节都是该 plugin 自身文档的开头。

#### issue-to-pr-pipeline

`resolve-issue` 带着一个工单走完这些步骤：对 issue 进行事实核查、起草并强化计划、实现、编写测试、审查修复，以及创建 PR。它在任何代码变更之前会停下来等你批准计划，在创建 PR 之前也会再停一次。

![resolve-issue-dashboard 正在展示一次运行到一半的 pipeline](https://raw.githubusercontent.com/softwareone-platform/issue-to-pr/main/docs/resolve-issue-dashboard.png)

它把另外三个 plugin 声明为依赖，所以在 Claude Code v2.1.143 或更高版本上，安装它就会一并安装那三个：

```
/plugin install issue-to-pr-pipeline@tundra
```

[issue-to-pr-pipeline 的完整说明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#issue-to-pr-pipeline)

#### disconfirm-first

三个对抗式审查者，每个层级一个：`review-issue-fact` 在规划修复之前对照代码库检查 issue，`review-plan-risk` 对计划或规格进行事前验尸，`review-code-risk` 在 PR 创建之前质疑已实现的修复。

[disconfirm-first 的完整说明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#disconfirm-first)

#### test-authoring

找出测试缺口，编写或刷新单元测试和集成测试。每个测试都出自一个编写者 agent，它会学习最接近的相邻测试的约定，再由一个独立的验证者检查。不会有任何东西被复制到你的 repo。

[test-authoring 的完整说明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#test-authoring)

#### pr-lifecycle

`open-pr` 创建一个 PR，标题和描述遵循你自己以往的 PR；`resolve-pr-comments` 逐一判断如何处理一个 PR 的评审讨论，并起草修复和回复。两者都会先让你看到它们要做什么，等你同意之后，才会在你的电脑之外做任何变更。

[pr-lifecycle 的完整说明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#pr-lifecycle)

### 来自这个 repo

#### whoami

`/whoami:whoami` 读取以你的名义交付的代码（无论是你亲手敲的，还是你指挥模型写的）、你给 Claude 的 prompt，以及你在 `CLAUDE.md` 里留给它的指令。它找出反复出现的模式，并逐一检验有没有其他原因能解释它，再总结出哪一类问题你稳定做对、哪一类问题你稳定漏掉。结果是一份单个文件、不依赖外部资源的 HTML 报告，开头是一条时间线，显示这个结论建立在哪些材料之上。它只读你指定的 repo，代码只通过 git 读取，你的 prompt 和指令也只在你同意之后才会读。报告会用你要求的语言撰写。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/whoami/report-overview-dark.zh-CN.png">
  <img alt="一份针对两个虚构 repo 的 whoami 报告：读取范围的时间线、只有一个轴的结论，以及一张流程图" src="docs/whoami/report-overview-light.zh-CN.png">
</picture>

[whoami 的完整说明（英文）](plugins/whoami/README.md)
