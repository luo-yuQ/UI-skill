# First Stage Runner v0.1

## Purpose

组织第一阶段 UI 生成流水线：

```text
A Layout
+
B Style
+
User Requirement
→
Composer
```

Runner 只负责：

* 创建统一 run workspace
* 决定执行顺序
* 规定阶段输入输出路径
* 要求 Agent 阅读对应 Skill 后执行

Runner 不重新定义 A、B、Composer 的内部规则。

---

## Repository

仓库根目录：

`ui-skill`

---

## Run Namespace

每次执行先在：

`runs/`

创建：

`YYYYMMDD-HHMMSS_<scenario>_<index>`

例如：

`20260810-110500_guild-shop_001`

一次用户任务只能使用一个 namespace。

---

## Run Structure

```text
runs/<run-id>/
├── 00-input/
│   └── request.json
├── 10-layout-reference/
│   └── layout-analysis.json
├── 20-style-reference/
│   ├── asset-analysis/
│   │   ├── ref-001.json
│   │   └── ...
│   └── style-profile.json
├── 30-composer/
│   ├── ui-compose-input.json
│   └── ui-compose-plan.json
└── run-manifest.json
```

---

## Execution

### 1. Save Request

将本次：

* 用户需求
* A 输入路径
* B 输入路径

记录到：

`00-input/request.json`

---

### 2. Run A

读取：

`game-ui-layout-analysis-verifier/SKILL.md`

严格按照 A 当前 Skill 和 schema 执行。

最终合法输出统一保存为：

`10-layout-reference/layout-analysis.json`

如果调用方已经提供合法 A final，则验证后直接复用，不重复分析。

---

### 3. Run B

读取：

`game-ui-style-reference-analyzer/SKILL.md`

严格按照当前 B1 / B2 workflow 执行。

B1 输出统一保存：

`20-style-reference/asset-analysis/ref-XXX.json`

B2 最终输出：

`20-style-reference/style-profile.json`

如果调用方已经提供合法 B2 final，则验证后直接复用。

---

### 4. Build Composer Input

只有 A final 和 B2 final 都验证通过后才能继续。

读取：

* 用户需求
* `10-layout-reference/layout-analysis.json`
* `20-style-reference/style-profile.json`

再读取：

`game-ui-auto-composer-skill/SKILL.md`

以及当前 Composer input schema。

按照真实 schema 生成：

`30-composer/ui-compose-input.json`

不要自行发明字段。

---

### 5. Run Composer

按照 Composer 当前 Skill 执行：

```text
A layout
+
B style
+
user requirement
→
new UI design intent
```

最终输出：

`30-composer/ui-compose-plan.json`

并按 Composer 当前 schema / validator 验证。

---

## Failure Rule

任何阶段 validation failed：

* 停止依赖该结果的后续阶段
* 不伪造 JSON
* 不偷偷修改 Skill/schema
* 在 `run-manifest.json` 记录失败阶段

---

## Important

Skill 不决定 `runs/` 路径。

**Runner 决定文件写到哪里，Skill 决定文件应该包含什么。**

Agent 执行本 Runner 时，应在进入每个阶段前重新读取对应 `SKILL.md`，不要凭记忆执行。
