# First Stage Runner v0.1

## 1. Purpose

`First Stage Runner` 用于组织第一阶段 UI 生成流水线。当前固定流程为：

```text
A Layout Reference -> A1

B Style References -> B1 -> B2

A1 Final
+ B2 Final
+ User Requirement
-> Composer
-> ui-compose-plan.json
```

Runner 只负责：

- 创建统一的 run workspace；
- 保存本次任务的原始输入；
- 决定阶段执行顺序；
- 规定各阶段的输入、输出路径；
- 要求 Agent 在执行每个阶段前重新读取对应的 `SKILL.md`；
- 使用阶段当前的 validator 验证 final；
- 控制阶段之间的 dependency gate；
- 在 manifest 中记录整个 run 的执行状态。

Runner 不负责：

- 重新定义 A1 的布局分析规则；
- 重新定义 B1、B2 的风格分析规则；
- 重新定义 Composer 的设计规则；
- 修改 Skill 或 schema；
- 修复 Skill 输出；
- 自行补充缺失的业务字段；
- 翻译、总结、润色或重新解释用户需求。

核心原则：

> Runner 决定流程怎么走、文件写到哪里。
> Skill 决定阶段任务怎么执行、文件内容应该是什么。

---

## 2. Repository

仓库中的长期 Workflow 规则保存在：

```text
runner/first-stage-runner.md
```

阶段能力由以下当前 Skill 提供：

```text
game-ui-layout-reference-analyzer/
game-ui-style-reference-analyzer/
game-ui-auto-composer-skill/
```

每次实际执行产生的数据只保存在：

```text
runs/
```

---

## 3. Current Pipeline Scope

First Stage Runner v0.1 只覆盖：

```text
A1 -> B1 -> B2 -> Composer Input -> Composer
```

当前不包含：

```text
A2
Prompt Compiler
Provider
GPT Image
Preview Adapter
FairyGUI
```

A2 已从当前正式流程中移除。

---

## 4. Run Namespace

每次新的用户任务必须在 `runs/` 下创建一个独立 run，命名格式为：

```text
YYYYMMDD-HHMMSS_<scenario>_<index>
```

例如：

```text
20260811-160500_guild-shop_001
```

规则：

- 一个用户任务只能使用一个 namespace；
- 同一次 run 的所有阶段必须写入同一个 namespace；
- A、B、Composer 不得各自创建独立 run；
- 阶段执行过程中不得切换 namespace；
- 不得覆盖历史 run；
- Runner 不自动判断 scenario 名称，调用方必须提供或明确指定。

---

## 5. Run Structure

每次 run 使用以下统一结构：

```text
runs/<run-id>/
|
|-- 00-input/
|   |-- request.json
|   |
|   |-- layout-reference/
|   |   `-- ref-001.<ext>
|   |
|   `-- style-reference/
|       |-- ref-001.<ext>
|       |-- ref-002.<ext>
|       `-- ...
|
|-- 10-layout-reference/
|   `-- layout-analysis.json
|
|-- 20-style-reference/
|   |-- asset-analysis/
|   |   |-- ref-001.json
|   |   |-- ref-002.json
|   |   `-- ...
|   |
|   `-- style-profile.json
|
|-- 30-composer/
|   |-- ui-compose-input.json
|   `-- ui-compose-plan.json
|
`-- run-manifest.json
```

---

## 6. Original Input Rule

Runner 必须保存本次任务的全部原始输入：

- 用户原始 requirement；
- A Layout Reference；
- B Style References。

所有输入统一保存在 `00-input/`。

### User Requirement

用户需求必须：

- 保留用户原文；
- 使用 UTF-8；
- 不翻译；
- 不总结；
- 不润色；
- 不用重新解释后的文本覆盖原文；
- 不为了适配 schema 修改原始文本。

后续 Composer 使用的 requirement 必须能够按 JSON 值追溯到 `00-input/request.json` 中保存的原始 `user_requirement`。

---

## 7. request.json

`00-input/request.json` 只描述本次 Runner 输入。v0.1 保持最小化，不承载 A、B 或 Composer 的业务分析结果。

当前逻辑结构：

```json
{
  "user_requirement": "用户原始需求",
  "layout_references": [
    "00-input/layout-reference/ref-001.png"
  ],
  "style_references": [
    "00-input/style-reference/ref-001.png",
    "00-input/style-reference/ref-002.png"
  ]
}
```

这些路径相对于本次 run 根目录。若后续建立正式的 `request.schema.json`，字段以正式 schema 为准。

Runner 不得将自己的 request contract 与 A、B、Composer schema 混合。

---

## 8. Execution Workflow

### Stage 0 - Initialize Run

Runner 首先：

1. 创建唯一 `<run-id>`；
2. 一次性创建完整 run directory；
3. 将原始 A、B 图片复制到 `00-input/` 对应目录；
4. 保存用户原始 requirement；
5. 创建 UTF-8 `request.json`；
6. 创建 `run-manifest.json`。

Stage 0 完成并记为 `completed` 后，才能开始任何 Skill。

---

## 9. Stage 1 - Run A1

执行前必须重新读取：

```text
game-ui-layout-reference-analyzer/SKILL.md
```

并按该 Skill 的当前要求读取其 schema、taxonomy、validation reference 与必要执行说明。不得凭 Agent 记忆执行。

### Input

A1 的业务输入仅来自：

```text
00-input/layout-reference/
```

A1 只负责分析 Layout Reference。除非 A1 当前 Skill 明确要求，否则 Runner 不得额外向 A1 注入：

- B Style；
- Composer Plan；
- 后续设计结论。

### Output

A1 最终输出统一保存为：

```text
10-layout-reference/layout-analysis.json
```

其内容必须遵守 A1 当前 Skill 与 schema。Runner 不定义该文件的内部字段。

### Validation

从仓库根目录运行当前 A1 validator：

```powershell
python game-ui-layout-reference-analyzer/scripts/validate_layout_reference_analysis.py `
  runs/<run-id>/10-layout-reference/layout-analysis.json
```

只有命令退出码为 `0` 时，A1 才是 `VALID`，才能作为 Composer 的后续输入。

如果 validation failed，立即执行失败规则。Runner 不得手动修改 JSON、自动补字段、删除非法字段、修改 schema，或伪造一个合法的 A1 final。

---

## 10. Stage 2 - Run B1

执行前必须重新读取：

```text
game-ui-style-reference-analyzer/SKILL.md
```

并按该 Skill 的当前要求读取 B1 schema、taxonomy、validation reference 与 B1 workflow reference。

每一张 Style Reference 必须独立执行一次 B1：

```text
00-input/style-reference/ref-001.png
-> 20-style-reference/asset-analysis/ref-001.json

00-input/style-reference/ref-002.png
-> 20-style-reference/asset-analysis/ref-002.json
```

B1 只描述单张参考图。Runner 不得把多张图片的综合结论提前写入任一 B1 输出。

每个 B1 输出都必须单独验证：

```powershell
python game-ui-style-reference-analyzer/scripts/validate_asset_analysis.py `
  runs/<run-id>/20-style-reference/asset-analysis/ref-001.json
```

只有本次 B2 synthesis 将使用的每个 B1 final 都验证通过后，B1 阶段才是 `completed`。

---

## 11. Stage 3 - Run B2

只有本次参与 synthesis 的全部 B1 final 均合法后，才能运行 B2。

B2 输入来自：

```text
20-style-reference/asset-analysis/
```

执行前必须再次重新读取：

```text
game-ui-style-reference-analyzer/SKILL.md
```

并遵循其中当前的 B2 workflow。B2 的输入数量、内容和合成规则完全由当前 Skill contract 决定。

### Output

B2 最终输出保存为：

```text
20-style-reference/style-profile.json
```

Runner 不定义该文件的内部字段。

### Validation

从仓库根目录运行当前 B2 validator：

```powershell
python game-ui-style-reference-analyzer/scripts/validate_style_profile.py `
  runs/<run-id>/20-style-reference/style-profile.json
```

只有命令退出码为 `0` 时，B2 才是 `VALID`，才能进入 Composer gate。

---

## 12. Composer Gate

进入 Composer Input 阶段前必须同时满足：

```text
A1 Final = VALID
AND
B2 Final = VALID
AND
User Requirement = AVAILABLE
```

对应的唯一当前 run 来源为：

```text
10-layout-reference/layout-analysis.json
20-style-reference/style-profile.json
00-input/request.json -> user_requirement
```

三者缺一不可。任何依赖项失败，不得构建 Composer Input 或运行 Composer。

---

## 13. Stage 4 - Build Composer Input

执行前必须重新读取：

```text
game-ui-auto-composer-skill/SKILL.md
game-ui-auto-composer-skill/schemas/ui-compose-input.schema.json
```

Composer Input 必须由当前 Composer Skill 提供的 deterministic builder 构建，不得由 Agent 手写或重组：

```powershell
python game-ui-auto-composer-skill/scripts/build_compose_input.py `
  --request runs/<run-id>/00-input/request.json `
  --layout runs/<run-id>/10-layout-reference/layout-analysis.json `
  --style runs/<run-id>/20-style-reference/style-profile.json `
  --output runs/<run-id>/30-composer/ui-compose-input.json
```

Runner 不得：

- 自行发明 Composer input 字段；
- 根据旧版 schema 猜字段；
- 把历史 run 数据混入当前 run；
- 为了让 Composer 更容易工作而补充设计结论；
- 重录、改写或规范化原始 requirement；
- 改写 A1 或 B2 payload。

构建完成后必须验证 schema 和上游完整性：

```powershell
python game-ui-auto-composer-skill/scripts/validate_input.py `
  runs/<run-id>/30-composer/ui-compose-input.json `
  --layout-source runs/<run-id>/10-layout-reference/layout-analysis.json `
  --style-source runs/<run-id>/20-style-reference/style-profile.json
```

只有命令退出码为 `0` 时，Composer Input 才是 `VALID`，才能调用 Composer。

---

## 14. Stage 5 - Run Composer

Composer 的执行逻辑为：

```text
A Layout Evidence
+ B Style Evidence
+ User Requirement
-> New UI Design Intent
```

执行前必须再次重新读取：

```text
game-ui-auto-composer-skill/SKILL.md
```

并按 Composer 当前 schema、reference 和 workflow 执行。Runner 不参与 UI 设计决策。

最终输出保存为：

```text
30-composer/ui-compose-plan.json
```

随后使用当前 Composer validator 严格验证：

```powershell
python game-ui-auto-composer-skill/scripts/validate_plan.py `
  runs/<run-id>/30-composer/ui-compose-plan.json `
  --input runs/<run-id>/30-composer/ui-compose-input.json
```

只有命令退出码为 `0` 时，Composer 才是 `VALID`。此时可将 `run-manifest.json` 的整体 `status` 标记为 `completed`。

---

## 15. Existing Final Reuse

v0.1 允许复用调用方已经提供的 final，但必须先把该文件保存到当前 run 的规定路径，再使用当前 validator 验证。

例如复用 A1 final：

1. 保存为 `10-layout-reference/layout-analysis.json`；
2. 按当前 A1 schema 与 validator 验证；
3. 验证通过后将 A1 stage 标记为 `reused`；
4. 不重复运行 A1。

合法的 `style-profile.json` 可按同样规则直接作为 B2 final 使用，并将 B2 stage 标记为 `reused`。

> Reuse means validate and reuse.

Reuse 不意味着默认信任。若当前 validator 判定失败，即使该文件来自历史成功 run，也不得继续使用。

复用 B2 final 时，不要求为当前 run 伪造 B1 finals；未执行的 B1 应在 manifest 中如实标记为 `skipped`。

---

## 16. Failure Rule

任何阶段 validation failed：

1. 将当前阶段标记为 `failed`；
2. 将整个 run 标记为 `failed`；
3. 停止所有依赖该结果的后续阶段；
4. 保存已经生成的原始失败输出用于排查；
5. 在 `run-manifest.json` 中记录失败阶段与 validator 返回的错误；
6. 不伪造替代结果。

禁止：

```text
validation failed
-> Runner 偷偷修改 JSON
-> validation passed
```

也禁止：

```text
validation failed
-> 修改 Skill / schema
-> 继续当前 run
```

Skill 或 schema 的修改属于独立开发行为，不属于 Runner 本次执行职责。若需要修改，结束当前失败 run，在独立任务中修改并验证 Skill，然后为用户任务创建新的 run。

---

## 17. run-manifest.json

`run-manifest.json` 用于描述一次 run 的执行状态，而不是复杂事件日志。

最小示例：

```json
{
  "run_id": "20260811-160500_guild-shop_001",
  "status": "running",
  "stages": {
    "input": {
      "status": "completed"
    },
    "a1": {
      "status": "completed"
    },
    "b1": {
      "status": "completed"
    },
    "b2": {
      "status": "completed"
    },
    "composer_input": {
      "status": "completed"
    },
    "composer": {
      "status": "pending"
    }
  }
}
```

Stage status：

```text
pending
running
completed
failed
skipped
reused
```

整个 run 的 status 至少支持：

```text
running
completed
failed
```

每次阶段开始、验证结束、失败或复用后都必须立即更新 manifest。发生失败时，失败 stage 还应记录足以定位问题的 validator 错误；不得把失败输出内容改写后再记录。

Manifest 首先用于回答：

> 当前 run 跑到哪里了？

以及：

> 如果失败，是在哪个阶段失败？

---

## 18. Stop-after Support

First Stage Runner v0.1 的完整 Workflow 为：

```text
A1 -> B1 -> B2 -> Composer Input -> Composer
```

测试过程中必须允许在以下阶段边界停止：

```text
stop after A1
stop after B1
stop after B2
stop after Composer Input
stop after Composer
```

v0.1 不要求实现正式 CLI 参数。Agent 根据测试要求，在指定阶段完成并验证、更新 manifest 后停止，不得启动下一个阶段。

该能力用于：

- 独立检查 A1；
- 独立检查 B1、B2；
- 检查 Composer Input；
- 定位 Pipeline 错误；
- 做阶段对照实验。

---

## 19. Skill Boundary

Runner 可以：

```text
创建 run
创建目录
复制原始输入
保存原始 requirement
选择执行顺序
读取 Skill
调用阶段任务
调用 validator
根据退出码判断 pass / fail
使用当前 Skill 的正式工具组装下一阶段合法输入
维护 manifest
```

Runner 不可以：

```text
分析布局
总结风格
决定 UI 设计
重写用户需求
修正 Skill 输出
为 Skill 发明字段
改变 schema
绕过 validator
```

---

## 20. Agent Execution Rule

Agent 执行 Runner 时，每进入一个阶段，都必须重新读取对应的当前 `SKILL.md`。

禁止：

> “我之前已经看过，所以按记忆执行。”

执行依据始终是仓库当前版本。Runner 本身只定义：

```text
WHEN
WHERE
IN WHAT ORDER
```

具体 Skill 定义：

```text
HOW
WHAT
```

如果 Runner 文档中的示例命令与当前 Skill contract 冲突，以当前 Skill 与其 validator 为阶段内容和验证权威；同时停止本次 run 并把 contract drift 作为独立维护问题报告，不能边跑边修改契约。

---

## 21. v0.1 Non-goals

First Stage Runner v0.1 暂不解决：

- 自动识别用户上传图片属于 A 还是 B；
- 自动判断 scenario 名称；
- 多 Agent 并行；
- B1 并发执行；
- retry strategy；
- 自动修复失败结果；
- Prompt Compiler；
- Provider；
- GPT Image 调用；
- 自动生成最终效果图；
- UI 可视化；
- FairyGUI；
- 完整 CLI；
- Python orchestration runtime。

这些内容在 Workflow contract 稳定后再评估。

---

## 22. v0.1 Success Condition

一次完整 First Stage Runner run 成功的最低标准：

```text
原始 A
+ 原始 B
+ 原始 User Requirement
-> 统一 run workspace
-> 合法 A1 Final
-> 合法 B1 Finals（或合法复用的 B2 Final）
-> 合法 B2 Final
-> 合法 Composer Input
-> 合法 Composer Plan
```

完整执行且未复用 B2 时，最终至少存在：

```text
00-input/request.json

10-layout-reference/layout-analysis.json

20-style-reference/asset-analysis/ref-XXX.json
20-style-reference/style-profile.json

30-composer/ui-compose-input.json
30-composer/ui-compose-plan.json

run-manifest.json
```

并且：

```text
run-manifest.status = completed
```

所有 final 与 Composer Input、Composer Plan 都必须由各自当前 validator 判定为合法。

---

## 23. Core Principle

First Stage Runner v0.1 的目标不是让 Agent 更聪明，而是让当前已经存在的 A1、B1、B2、Composer 第一次形成一条正式 Workflow：

```text
固定
可重复
可验证
可追踪
可分段停止
```

最终原则保持为：

> **Runner 决定文件写到哪里、什么时候执行。**
> **Skill 决定文件里面是什么、阶段应该怎么做。**
