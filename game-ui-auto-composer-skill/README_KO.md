# Game-UI-Auto-Composer-Skill

<p align="center">
  <strong>기존 리소스로 게임 UI를 자동 구성합니다.</strong>
</p>

<p align="center">
  <em>"개발자는 꼭 필요한 정보만 제공하고, 나머지는 가능한 한 AI가 자동으로 처리합니다."</em>
</p>

<p align="center">
  <a href="./README.md">中文</a> ·
  <a href="./README_EN.md">English</a> ·
  <a href="./README_JA.md">日本語</a> ·
  <a href="./README_ES.md">Español</a>
</p>

<p align="center">
  <img alt="License MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg">
  <img alt="Claude Code Skill" src="https://img.shields.io/badge/Claude%20Code-Skill-7C3AED">
  <img alt="skills.sh Compatible" src="https://img.shields.io/badge/skills.sh-Compatible-84cc16">
  <img alt="Game UI" src="https://img.shields.io/badge/Game%20UI-Auto%20Composer-0ea5e9">
</p>

---

## 이것은 무엇인가요?

**Game-UI-Auto-Composer-Skill** 은 게임 개발용 **UI 자동 구성 Skill** 입니다.

다음과 같은 상황을 위해 만들어졌습니다:
- 배경, 버튼, 아이콘, 캐릭터 프레임 등 이미지 리소스는 있음
- 게임 설명도 있음
- 하지만 완성된 디자인 시안은 없음

이 Skill 은 가능한 한 자동으로:
- 게임 유형, 플랫폼, 인터랙션을 이해하고
- 리소스 역할과 우선순위를 식별하고
- 필요한 페이지 범위를 추론하고
- 레이아웃과 컴포넌트 구조를 설계하며
- **Engine-Neutral UI Spec** 를 생성하고
- 구현 친화적인 결과를 출력합니다

---

## 잘 맞는 용도

v1 / MVP 는 특히 다음에 적합합니다:
- WeChat 미니게임
- 가벼운 HTML5 미니게임
- 캐주얼 게임
- 러너 / 레인 전환 / 탭 전환 / 가벼운 머지 / 가벼운 리듬 장르
- 리소스는 있지만 완성된 UI 시안이 없는 프로젝트

기본 우선 페이지:
1. Home
2. Gameplay HUD
3. Result

---

## 설치

### 방법 1: `npx skills add`
```bash
npx skills add Flycan-Fanc/game-ui-auto-composer-skill
```

### 방법 2: 수동 설치
1. 이 저장소를 다운로드
2. `game-ui-auto-composer-skill/` 폴더를 찾기
3. Claude 에 해당 폴더를 업로드하거나 zip 으로 업로드
4. 또는 Claude Code skills 디렉터리에 배치

---

## 현재 범위

v1 은 다음을 목표로 하지 않습니다:
- 모든 게임 장르의 완전 지원
- 상업용 최종 비주얼 완전 자동화
- 깊은 엔진 전용 익스포트
- 대규모 MMO / SLG / RPG UI 시스템 자동화

이는 의도적인 MVP 범위입니다.

---

## License

MIT

---

## 크레딧

- **Author:** Flycan-Fanc
- **Co-created-with:** OpenAI ChatGPT
