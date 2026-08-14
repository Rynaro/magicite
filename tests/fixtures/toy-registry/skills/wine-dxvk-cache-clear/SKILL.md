---
name: wine-dxvk-cache-clear
description: |
  Clear a stale DXVK shader cache that is causing graphical corruption in a
  Wine or Proton game. Use when: a game shows corrupted textures or shader
  flicker that started after a driver or DXVK version change.
---

## Procedure
1. Locate the game's DXVK cache file, usually named after the game's exe with a .dxvk-cache suffix.
2. Close the game and delete that cache file.
3. Relaunch the game and allow shaders to recompile (expect a one-time stutter pass).

## Pitfalls
- Deleting the cache mid-session can crash the game; always fully quit first.

## Examples
+ "textures look corrupted since I updated my gpu driver" → full procedure
