# Gameplay HUD Template

## Purpose
- support gameplay without blocking it
- expose score, progress, timer, status, pause
- preserve readability

## Typical regions
- top bar
- corner indicators
- occasional warning layer
- optional bottom utility hints

## Required outputs
- gameplay safe area
- HUD region allocation
- dynamic states
- component tree
- motion recommendations

## Common components
- Root
- SafeGameplayRegion
- TopBar
- ScoreText
- ProgressBar
- PauseButton
- WarningHint
