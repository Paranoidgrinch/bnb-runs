# bnb-runs

Every run played of **Bureaucrats & Broomsticks**, recorded by the game and filed here by player.

A recording is not a summary of a run. It is the run itself, reduced to what cannot be recomputed: the
**seed**, how the run began (character, map generator, the meta profile), and **every answer the player
gave**, in order — which door, which card, which target, which shop item, end turn. The engine is
deterministic, so seed + answers replay the whole run step for step. One fingerprint per room (act, room,
hp, deck, relics, resources) is stored alongside, so a replay that goes wrong says where.

A whole run is a few kilobytes.

## Where things are

- `runs/index.csv` — one row per run: when, who, seed, character, result, how far it got, how many answers,
  engine build and content hash.
- `runs/<player>-<id>/<date>-seed<seed>-<result>-<hash>.json` — the recordings. The id tells two players with
  the same name apart.
- `tools/ingest.py` + `.github/workflows/ingest.yml` — the collector (hourly, or run it by hand under
  *Actions → Ingest runs from Discord → Run workflow*).

## How a run gets here

1. The game records the run while it is played (`RogueDeck-Core`: `RunRecorder`), saves the recording next to
   the save file, and when the run ends — won, lost, or abandoned by starting another — posts it to a Discord
   webhook (`bnb-godot`: `scripts/RunLog.cs`). A run that could not be sent waits and goes with the next start.
2. This repository's workflow reads the channel with a bot, accepts only messages from the game's webhook,
   checks each file, skips duplicates, and commits it here.

Settings: secret `DISCORD_BOT_TOKEN`, variables `DISCORD_CHANNEL_ID` and `DISCORD_WEBHOOK_ID`.

## Replaying a run

From a `RogueDeck-Core` checkout, against the content the run was played on (the recording names its hash):

```bash
dotnet run --project src/RogueDeck.Bot.Cli -c Release -- \
  --game ../bnb-godot/content/game.roguedeck.json \
  --replay-run runs/<player>/<file>.json \
  --decisions decisions.jsonl
```

It prints `REPRODUCED` or the first room where the replay differs. `--decisions` writes one JSON object per
answer — the situation (act, room, hp), what was on offer (doors, cards, the hand) and what was chosen: the
view to read a run by, or to train on.
