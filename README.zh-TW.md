<!-- translated from README.md, source sha256 b614f156d5b22e6bef95d3d6c1674cbb09f70f4f589827ad20aa780d6bc40306; see CLAUDE.md "Translations of the root README" before editing -->
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
## 📦 安裝

```
/plugin marketplace add https://github.com/softwareone-platform/tundra.git
```

然後從中安裝你想要的 plugin：

```
/plugin install <plugin>@tundra
```

之後執行 `/reload-plugins` 來啟用它們。

像這樣的 marketplace，Claude Code 預設不開啟自動更新，所以在你開啟之前都不會收到新版本：`/plugin` → **Marketplaces** → `tundra` → **Enable auto-update**。

請使用上面完整的 HTTPS URL。`softwareone-platform/tundra` 這種簡寫也能用，但它透過 SSH clone，是不同的指令。

<a id="what-is-in-it"></a>
## 🗂️ 內容

### 🔁 從工單到 pull request

![resolve-issue-dashboard 的縮圖：執行清單、pipeline 環形進度、agent 的工具呼叫和 token 統計](docs/issue-to-pr/thumbnails.png)

四個 plugin，把一張工單帶到經過審查的 pull request。它們放在 [issue-to-pr](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md)，並且一起發布。`issue-to-pr-pipeline` 執行整個流程；在 Claude Code v2.1.143 或更新的版本上，安裝它就會一併安裝另外三個，而那三個也都能獨立使用。

```
/plugin install issue-to-pr-pipeline@tundra
```

| Plugin | 做什麼 |
|---|---|
| [`issue-to-pr-pipeline`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#issue-to-pr-pipeline) | 把一張工單從診斷帶到經過審查的 pull request，核准計畫前和開出 PR 前各停下來等你一次 |
| [`disconfirm-first`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#disconfirm-first) | 在下一步以它為基礎之前，對 issue、計畫或已實作的修正做對抗式審查 |
| [`test-authoring`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#test-authoring) | 找出測試缺口，撰寫單元測試與整合測試，每個測試都由獨立的驗證者檢查 |
| [`pr-lifecycle`](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#pr-lifecycle) | 在 Azure DevOps 或 GitHub 上依你過去 PR 的風格開出 pull request，並處理它的審查意見 |

### 🪞 你的工作方式

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/whoami/thumbnails-dark.png">
  <img alt="whoami 報告的縮圖：時間軸、結論、流程圖和表格" src="docs/whoami/thumbnails-light.png">
</picture>

| Plugin | 做什麼 |
|---|---|
| [`whoami`](plugins/whoami/README.md) | 從你的程式碼、你的 prompt 和你的 CLAUDE.md 做一份自我評估，輸出成 HTML 報告。只有你能用 `/whoami:whoami` 啟動它 |
| [`remind-me`](plugins/remind-me/README.md) | 你在某一天跑過的 session 還留下哪些沒做完的事，逐一對照每個 pull request 和 branch 的即時狀態，並提供回到每個 session 的入口 |
