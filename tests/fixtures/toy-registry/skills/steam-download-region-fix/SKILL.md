---
name: steam-download-region-fix
description: |
  Fix a Steam client stuck at very low download speed by changing its
  download region. Use when: Steam downloads are far below the local
  internet speed for every game, not just one.
---

## Procedure
1. Open Steam Settings > Downloads and note the current "Download Region".
2. Switch to a nearby alternate region and start a download to benchmark it.
3. Repeat for two or three candidate regions and keep the fastest one.

## Pitfalls
- Router-level QoS rules can look identical to a bad Steam region; rule those out first.

## Examples
+ "steam downloads cap at 500kb/s no matter what game" → full procedure
