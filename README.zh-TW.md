<!-- translated from README.md, source sha256 75de0b8f03095f15f5d7dccc3b5aa6075597aef81ec957e0ed4b9c91e139dc23; see CLAUDE.md "Translations of the root README" before editing -->
# tundra

<div align="center">

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

| Plugin | 提供什麼 |
|---|---|
| [`issue-to-pr-pipeline`](#issue-to-pr-pipeline) | 把一張工單從診斷帶到經過審查的 pull request，串起下面三個 plugin |
| [`disconfirm-first`](#disconfirm-first) | 對 issue、計畫或已實作的修正做對抗式審查 |
| [`test-authoring`](#test-authoring) | 撰寫單元測試與整合測試，每個測試由一個 agent 撰寫、另一個 agent 檢查 |
| [`pr-lifecycle`](#pr-lifecycle) | 在 Azure DevOps 或 GitHub 上開出 pull request，並處理它的審查意見 |
| [`whoami`](#whoami) | 從你的程式碼、你的 prompt 和你的 CLAUDE.md 做一份自我評估，歸結出你的工作方式，輸出成 HTML 報告 |

### 來自 [issue-to-pr](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md)

這四個 plugin 放在它們自己的 repo，並且一起發布。下面每一節都是該 plugin 自己文件的開頭。

#### issue-to-pr-pipeline

`resolve-issue` 帶著一張工單走過這些步驟：事實查核 issue、起草並強化計畫、實作、撰寫測試、審查修正，以及開出 PR。它在任何程式碼變更之前會停下來等你核准計畫，在開出 PR 之前也會再停一次。

![resolve-issue-dashboard 正在顯示一次執行到一半的 pipeline](https://raw.githubusercontent.com/softwareone-platform/issue-to-pr/main/docs/resolve-issue-dashboard.png)

它把另外三個 plugin 宣告為相依套件，所以在 Claude Code v2.1.143 或更新的版本上，安裝它就會一併安裝那三個：

```
/plugin install issue-to-pr-pipeline@tundra
```

[issue-to-pr-pipeline 的完整說明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#issue-to-pr-pipeline)

#### disconfirm-first

三個對抗式審查者，每個層級一個：`review-issue-fact` 在規劃修正之前對照程式碼檢查 issue，`review-plan-risk` 對計畫或規格做事前驗屍，`review-code-risk` 在 PR 開出之前質疑已實作的修正。

[disconfirm-first 的完整說明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#disconfirm-first)

#### test-authoring

找出測試缺口，撰寫或更新單元測試與整合測試。每個測試都出自一個撰寫者 agent，它會學習最接近的相鄰測試的慣例，再由一個獨立的驗證者檢查。不會有任何東西被複製到你的 repo。

[test-authoring 的完整說明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#test-authoring)

#### pr-lifecycle

`open-pr` 開出一個 PR，標題和描述遵循你自己過去的 PR；`resolve-pr-comments` 逐一判斷如何處理一個 PR 的審查討論串，並起草修正和回覆。兩者都會先讓你看到它們要做什麼，等你同意之後，才會在你的電腦之外做任何變更。

[pr-lifecycle 的完整說明](https://github.com/softwareone-platform/issue-to-pr/blob/main/README.zh-TW.md#pr-lifecycle)

### 來自這個 repo

#### whoami

`/whoami:whoami` 讀取以你的名義交付的程式碼（不論是你親手打的，還是你指揮模型寫的）、你給 Claude 的 prompt，以及你在 `CLAUDE.md` 裡留給它的指示。它找出反覆出現的模式，並逐一檢驗是否有其他原因能解釋它，再歸結出哪一類問題你穩定做對、哪一類問題你穩定漏掉。結果是一份單一檔案、不依賴外部資源的 HTML 報告，開頭是一條時間軸，顯示這個結論建立在哪些材料上。它只讀你指定的 repo，程式碼只透過 git 讀取，你的 prompt 和指示也只在你同意之後才會讀。報告會用你要求的語言撰寫。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/whoami/report-overview-dark.zh-TW.png">
  <img alt="一份針對兩個虛構 repo 的 whoami 報告：讀取範圍的時間軸、只有一個軸的結論，以及一張流程圖" src="docs/whoami/report-overview-light.zh-TW.png">
</picture>

[whoami 的完整說明（英文）](plugins/whoami/README.md)
