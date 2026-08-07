# Game-UI-Auto-Composer-Skill

<p align="center">
  <strong>从资源出发，自动编排游戏 UI。</strong>
</p>

<p align="center">
  <em>「开发者只提供必要信息，其余尽量交给 AI 自动完成。」</em>
</p>

<p align="center">
  <a href="./README_EN.md">English</a> ·
  <a href="./README_JA.md">日本語</a> ·
  <a href="./README_KO.md">한국어</a> ·
  <a href="./README_ES.md">Español</a>
</p>

<p align="center">
  <img alt="License MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg">
  <img alt="Claude Code Skill" src="https://img.shields.io/badge/Claude%20Code-Skill-7C3AED">
  <img alt="skills.sh Compatible" src="https://img.shields.io/badge/skills.sh-Compatible-84cc16">
  <img alt="Game UI" src="https://img.shields.io/badge/Game%20UI-Auto%20Composer-0ea5e9">
</p>

---

## 这是什么？

**Game-UI-Auto-Composer-Skill** 是一个面向游戏开发的 **UI 自动编排 Skill**。

当你只有：
- 一些图片资源（背景、按钮底板、图标、角色帧、装饰等）
- 一段简要游戏描述
- 但还没有完整设计稿

它会尽量自动完成：

- 理解游戏类型、平台与玩法特征
- 识别资源用途与优先级
- 推理页面范围与页面结构
- 规划布局、组件树与风格收敛
- 生成 **Engine-Neutral UI Spec**
- 输出适合实现的说明，减少开发者手动参与

---

## 适合什么场景？

这个 Skill 当前的 **v1 / MVP** 更适合：

- 微信小游戏
- H5 轻量小游戏
- 轻度休闲游戏
- 跑酷 / 换道 / 点击切换 / 轻合成 / 轻节奏类
- 没有设计稿，但已有部分资源
- 希望先快速得到核心页面 UI 方案的项目

默认优先处理的核心页面：

1. 首页（Home）
2. 游戏中 HUD（Gameplay HUD）
3. 结算页（Result）

---

## 它能做什么？

给定图片资源与游戏描述后，这个 Skill 会尽量自动：

1. **分析游戏信息**  
   提取平台、横竖屏、玩法节奏、核心交互、适合的 UI 密度

2. **识别资源用途**  
   判断哪些图更适合做背景、按钮、标题框、面板、角色展示、装饰等

3. **定义页面范围**  
   默认优先形成核心页面闭环，而不是盲目扩页

4. **组合生成 UI 方案**  
   输出页面结构、资源映射、组件层级、布局约束、动效建议

5. **输出实现友好的结果**  
   优先给出结构化结果，而不是只给一张“看起来好看但难实现”的图

---

## 工作原理

这个 Skill 的内部思路大致是：

**输入资源与描述 → 识别游戏类型 → 识别资源角色 → 推理页面 → 规划布局 → 风格收敛 → 输出实现规格**

它遵循几个核心原则：

- **开发者最小参与**
- **最小提问策略**
- **核心页面闭环优先**
- **可落地实现优先**
- **引擎友好表达**
- **轻量动效优先**
- **低置信度时保守降级**

它不是单纯“画图”，而是一个偏 **规则 + 推断** 的游戏 UI 自动实现引擎雏形。

---

## 安装

### 方式一：使用 `npx skills add`
```bash
npx skills add Flycan-Fanc/game-ui-auto-composer-skill
```

### 方式二：手动安装到 Claude / Claude Code
1. 下载本仓库
2. 找到 skill 文件夹 `game-ui-auto-composer-skill/`
3. 在 Claude 中上传该 skill 文件夹（或压缩后上传）
4. 或将其放入 Claude Code 的 skills 目录中

---

## 仓库结构

```text
game-ui-auto-composer-skill/
├── .git/
├── assets/
├── references/
├── scripts/
├── LICENSE
├── README.md
├── README_EN.md
├── README_ES.md
├── README_JA.md
├── README_KO.md
└── SKILL.md
```

---

## 输出重点

当前版本优先输出：

- 页面清单
- 资源用途映射
- 页面布局方案
- 组件树 / 约束布局
- Engine-Neutral UI Spec
- HTML + CSS 原型说明
- 轻量动效建议
- 质量检查与保守 fallback 说明

---

## 当前边界

v1 暂不追求：

- 全类型大型游戏全面支持
- 品牌级商业视觉终稿
- 深度引擎专用导出
- 复杂富演出动态页面
- 完整 MMO / SLG / RPG UI 系统自动化

这不是能力上限，而是 **MVP 迭代策略**：先聚焦真实可落地场景，再逐步扩展到 v2 / v3。

---

## 为什么要做这个 Skill？

因为现实里经常会遇到一种尴尬情况：

- 有角色图
- 有按钮图
- 有背景图
- 甚至有些 UI 碎片素材
- 但没有完整设计稿
- 也不想从零手动画页面草图

于是开发者只能自己一边猜、一边摆、一边反复试错。

这个 Skill 想解决的，就是这个问题：

> **让 AI 像一个懂游戏 UI 结构、懂资源编排、懂实现约束的助手一样，先帮你把“从资源到 UI”的这一步走通。**

---

## License

MIT

---

## 作者

- **Author:** Flycan-Fanc
- **Co-created-with:** OpenAI ChatGPT
