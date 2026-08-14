---
name: proton-battleye-eac-toggle
description: |
  Toggle BattlEye or Easy Anti-Cheat runtime support for a Steam Proton game.
  Use when: a multiplayer game refuses to launch with an anti-cheat error under Proton.
---

## Procedure
1. Open the game's properties in Steam and check the Compatibility tab.
2. Right-click the game, choose Properties > Installed Files > Browse, and confirm the
   anti-cheat runtime appid is installed alongside the game.
3. Enable "Steam Play" for the anti-cheat helper appid specifically, not just the game.

## Pitfalls
- Some anti-cheat vendors do not support Linux/Proton at all; check ProtonDB first.

## Examples
+ "multiplayer game says EAC not installed under proton" → full procedure
