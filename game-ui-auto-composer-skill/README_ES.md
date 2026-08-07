# Game-UI-Auto-Composer-Skill

<p align="center">
  <strong>Compón automáticamente interfaces de juego a partir de recursos existentes.</strong>
</p>

<p align="center">
  <em>"El desarrollador solo aporta la información necesaria; la IA hace el resto siempre que sea posible."</em>
</p>

<p align="center">
  <a href="./README.md">中文</a> ·
  <a href="./README_EN.md">English</a> ·
  <a href="./README_JA.md">日本語</a> ·
  <a href="./README_KO.md">한국어</a>
</p>

<p align="center">
  <img alt="License MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg">
  <img alt="Claude Code Skill" src="https://img.shields.io/badge/Claude%20Code-Skill-7C3AED">
  <img alt="skills.sh Compatible" src="https://img.shields.io/badge/skills.sh-Compatible-84cc16">
  <img alt="Game UI" src="https://img.shields.io/badge/Game%20UI-Auto%20Composer-0ea5e9">
</p>

---

## ¿Qué es esto?

**Game-UI-Auto-Composer-Skill** es una **skill de composición automática de UI para juegos**.

Está pensada para situaciones en las que tienes:
- recursos visuales como fondos, botones, iconos, frames de personajes y decoraciones
- una breve descripción del juego
- pero todavía no tienes un mockup completo de diseño

La skill intenta automáticamente:
- entender el tipo de juego, la plataforma y la interacción
- identificar roles y prioridades de los recursos
- inferir el conjunto de páginas necesarias
- planificar layouts, árboles de componentes y coherencia visual
- generar una **Engine-Neutral UI Spec**
- producir salidas orientadas a implementación

---

## Casos ideales

La versión v1 / MVP funciona mejor para:
- mini-juegos de WeChat
- mini-juegos ligeros en HTML5
- juegos casuales
- runner / cambio de carril / tap-switch / merge ligero / rhythm ligero
- proyectos con recursos pero sin un diseño completo

Páginas prioritarias:
1. Home
2. Gameplay HUD
3. Result

---

## Instalación

### Opción 1: con `npx skills add`
```bash
npx skills add Flycan-Fanc/game-ui-auto-composer-skill
```

### Opción 2: instalación manual
1. Descarga este repositorio
2. Localiza la carpeta `game-ui-auto-composer-skill/`
3. Súbela a Claude (o súbela comprimida en zip)
4. O colócala en el directorio de skills de Claude Code

---

## Alcance actual

La v1 no intenta resolver:
- todos los géneros de juegos
- el acabado visual comercial final
- exportadores profundos específicos de motor
- sistemas completos de UI para MMO / SLG / RPG

Esto es un alcance intencional de MVP.

---

## License

MIT

---

## Créditos

- **Author:** Flycan-Fanc
- **Co-created-with:** OpenAI ChatGPT
