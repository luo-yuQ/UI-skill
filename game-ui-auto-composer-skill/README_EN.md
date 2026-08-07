# Game-UI-Auto-Composer-Skill

<p align="center">
  <strong>Automatically compose game UI from existing assets.</strong>
</p>

<p align="center">
  <em>"The developer provides only the necessary information. AI does the rest whenever possible."</em>
</p>

<p align="center">
  <a href="./README.md">中文</a> ·
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

## What is this?

**Game-UI-Auto-Composer-Skill** is a **game UI auto-composition skill**.

It is designed for situations where you have:
- image assets such as backgrounds, buttons, icons, character frames, and decorations
- a short game description
- but no complete design mockup

It tries to automatically:
- understand game type, platform, and interaction pattern
- identify asset roles and priorities
- infer page scope and page structure
- compose layouts, component trees, and style harmonization
- generate an **Engine-Neutral UI Spec**
- produce implementation-friendly output

---

## Best fit

v1 / MVP is best for:
- WeChat mini-games
- lightweight HTML5 mini-games
- casual games
- runner / lane-switch / tap-switch / merge-lite / rhythm-lite patterns
- projects with assets but without a full design draft

Default priority pages:
1. Home
2. Gameplay HUD
3. Result

---

## What does it do?

Given assets and a short game description, the skill tries to:

1. **Analyze the game**
2. **Recognize asset roles**
3. **Infer a compact page set**
4. **Compose UI structure**
5. **Output implementation-friendly results**

Typical output includes:
- page list
- asset-role mapping
- page layout plans
- component trees
- layout constraints
- Engine-Neutral UI Spec
- HTML/CSS prototype guidance
- light motion suggestions

---

## How it works

The internal logic is roughly:

**assets + game description → game understanding → asset understanding → page inference → layout planning → style harmonization → implementation-oriented output**

Core principles:
- minimum developer burden
- minimum questions
- core page closure first
- implementation-first output
- engine-friendly representation
- light motion by default
- conservative fallback when confidence is low

---

## Installation

### Option 1: via `npx skills add`
```bash
npx skills add Flycan-Fanc/game-ui-auto-composer-skill
```

### Option 2: manual install
1. Download this repository
2. Locate the skill folder `game-ui-auto-composer-skill/`
3. Upload that folder to Claude (or upload it as a zip)
4. Or place it in your Claude Code skills directory

---

## Repository structure

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

## Current scope

v1 does **not** aim to solve:
- every game genre
- final premium commercial visual polish
- deep engine-specific exporters
- rich cinematic UI performance systems
- full MMO / SLG / RPG UI automation

This is intentional MVP scope, not a hard limitation forever.

---

## Why this skill?

A common problem in real projects is:

- you already have character art
- button assets
- backgrounds
- partial UI fragments
- but no complete UI draft

So developers end up manually guessing, placing, revising, and repeating.

This skill tries to reduce that friction:

> **It helps AI act like a game-UI-aware assistant that understands structure, asset assignment, and implementation constraints — and helps bridge the gap from raw assets to usable UI plans.**

---

## License

MIT

---

## Credits

- **Author:** Flycan-Fanc
- **Co-created-with:** OpenAI ChatGPT
