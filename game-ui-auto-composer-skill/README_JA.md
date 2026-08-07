# Game-UI-Auto-Composer-Skill

<p align="center">
  <strong>既存アセットからゲームUIを自動構成。</strong>
</p>

<p align="center">
  <em>「開発者は必要最小限の情報だけを渡し、残りは可能な限りAIが自動で補う。」</em>
</p>

<p align="center">
  <a href="./README.md">中文</a> ·
  <a href="./README_EN.md">English</a> ·
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

## これは何ですか？

**Game-UI-Auto-Composer-Skill** は、ゲーム開発向けの **UI 自動編成 Skill** です。

以下のような状況を想定しています：
- 背景、ボタン、アイコン、キャラクターフレームなどの画像アセットはある
- ゲーム説明はある
- しかし完成したデザイン稿はまだない

この Skill は可能な限り自動で：
- ゲームタイプ、プラットフォーム、操作特性を理解し
- アセットの役割と優先度を推定し
- 必要なページ構成を推論し
- レイアウトとコンポーネント構造を計画し
- **Engine-Neutral UI Spec** を生成し
- 実装しやすい形で出力します

---

## 向いている用途

v1 / MVP が特に向いているのは：
- WeChat ミニゲーム
- 軽量な HTML5 ミニゲーム
- カジュアルゲーム
- ランナー / レーン切替 / タップ切替 / 軽マージ / 軽リズム型
- アセットはあるが完成したUI設計稿がないプロジェクト

優先ページ：
1. Home
2. Gameplay HUD
3. Result

---

## できること

1. ゲーム情報の分析  
2. アセット役割の認識  
3. ページ範囲の推論  
4. UI 構造の設計  
5. 実装向け出力の生成  

主な出力：
- ページ一覧
- アセット役割マッピング
- ページごとのレイアウト案
- コンポーネントツリー
- レイアウト制約
- Engine-Neutral UI Spec
- HTML/CSS プロトタイプ用ガイド
- 軽量モーション案

---

## インストール

### 方法1：`npx skills add`
```bash
npx skills add Flycan-Fanc/game-ui-auto-composer-skill
```

### 方法2：手動インストール
1. このリポジトリをダウンロード
2. `game-ui-auto-composer-skill/` フォルダを探す
3. Claude にそのフォルダをアップロード（または zip 化してアップロード）
4. あるいは Claude Code の skills ディレクトリに配置する

---

## 現在の範囲

v1 は以下を主目的としていません：
- すべてのゲームジャンルへの完全対応
- 商用最終ビジュアルの完全自動化
- 深いエンジン専用エクスポート
- 大規模 MMO / SLG / RPG UI システム

これは意図的な MVP 範囲です。

---

## License

MIT

---

## クレジット

- **Author:** Flycan-Fanc
- **Co-created-with:** OpenAI ChatGPT
