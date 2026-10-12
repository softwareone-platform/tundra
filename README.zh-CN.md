<!-- translated from README.md, source sha256 3e331d2ff9d72d6b8fa02b5eae808ca8661a7741052075c2e9f6b25d098e7f0d; see CLAUDE.md "Translations of the root README" before editing -->
<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner/banner-dark.jpg">
  <img alt="tundra, Claude Code plugins" src="docs/banner/banner-light.jpg">
</picture>

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

### 🔁 从工单到 pull request

四个 plugin，把一个工单带到经过审查的 pull request。它们放在 [issue-to-pr](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md) 中，并且一起发布。`issue-to-pr-pipeline` 运行整个流程；在 Claude Code v2.1.143 或更高版本上，安装它就会一并安装另外三个，而那三个也都可以独立使用。

<img alt="resolve-issue-dashboard 正在跟踪一次运行：按 repository 分组的运行列表、pipeline 的十一个步骤排成的环、token 统计，以及每个 agent 的工具调用" src="docs/issue-to-pr/dashboard.png" width="560">

| Plugin | 做什么 |
|---|---|
| [`issue-to-pr-pipeline`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#issue-to-pr-pipeline) | 把一个工单从诊断带到经过审查的 pull request，批准计划前和创建 PR 前各停下来等你一次 |
| [`disconfirm-first`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#disconfirm-first) | 在下一步以它为基础之前，对 issue、计划或已实现的修复进行对抗式审查 |
| [`test-authoring`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#test-authoring) | 找出测试缺口，编写单元测试和集成测试，每个测试都由独立的验证者检查 |
| [`pr-lifecycle`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-CN.md#pr-lifecycle) | 在 Azure DevOps 或 GitHub 上按你以往 PR 的风格创建 pull request，并处理它的评审意见 |

一次安装全部四个：

```
/plugin install issue-to-pr-pipeline@tundra
```

### 🪞 你的工作方式

#### [`whoami`](plugins/whoami/README.zh-CN.md)

根据你的代码、你的 prompt 和你的 CLAUDE.md 做一份自我评估，输出为 HTML 报告。只有你能用 `/whoami:whoami` 启动它。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/whoami/thumbnails-dark.png">
  <img alt="whoami 报告的缩略图：标题与摘要、你做得好和常漏掉的一个维度、一张流程图，以及表格" src="docs/whoami/thumbnails-light.png" width="360">
</picture>

安装命令：

```
/plugin install whoami@tundra
```

#### [`remind-me`](plugins/remind-me/README.zh-CN.md)

你在某一天或一段日期里运行过的 session 还留下哪些没做完的事，逐一对照每个 pull request 和 branch 的实时状态，并提供回到每个 session 的入口。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/remind-me/thumbnails-dark.png">
  <img alt="remind-me 页面的缩略图：单日概要、放大一张卡片的期间报告、一个 repository 在整段期间的页面，以及一个 session" src="docs/remind-me/thumbnails-light.png" width="360">
</picture>

安装命令：

```
/plugin install remind-me@tundra
```
