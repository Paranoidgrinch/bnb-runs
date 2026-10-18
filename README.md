# bnb-runs

Recordings of every played run of Bureaucrats & Broomsticks, filed by player. Part of the game's four
repositories: [RogueDeck-Core](https://github.com/Paranoidgrinch/RogueDeck-Core) (engine),
[bnb-content](https://github.com/Paranoidgrinch/bnb-content) (content),
[bnb-godot](https://github.com/Paranoidgrinch/bnb-godot) (frontend) and bnb-runs (recordings).

A recording contains only what cannot be recomputed: the seed, the starting conditions (character, map
generator, meta profile) and every answer the player gave, in order (doors, cards, targets, shop items, end
of turn). Because the engine is deterministic, seed and answers reproduce the whole run. A fingerprint per
room (act, room, HP, deck, relics, resources) is stored as well, so a diverging replay reports where it
diverged. A run takes a few kilobytes.

## Contents

- `runs/index.csv`: one row per run with date, player, seed, character, result, progress, number of answers,
  engine build and content hash.
- `runs/<player>-<id>/<date>-seed<seed>-<result>-<hash>.json`: the recordings. The id distinguishes players
  with the same name.
- `leaderboard.json`: the closed-alpha ranking shown on the game's title screen (runs, wins, enemies, elite
  and boss fights won per player).
- `reports/cards.md` and `.csv`: per card, how often it was offered and taken, seen and bought in shops,
  removed from a deck and played, and the win rate of runs that took it.
- `reports/feedback.md` and `.csv`: the end-of-run feedback forms (Form B-7) with a 1 to 5 fairness rating
  and a comment.
- `tools/ingest.py` and `.github/workflows/ingest.yml`: the collector. It runs hourly or manually under
  Actions, "Ingest runs from Discord". All reports are rebuilt on every ingest;
  `python3 tools/ingest.py --leaderboard` rebuilds them by hand.

## How runs arrive

1. The game records the run while it is played (`RunRecorder` in RogueDeck-Core) and stores the recording
   next to the save file. When the run ends (won, lost, or abandoned by starting a new one), it posts the
   recording to a Discord webhook (`scripts/RunLog.cs` in bnb-godot). A recording that could not be sent is
   sent with the next start.
2. The workflow in this repository reads the channel with a bot, accepts only messages from the game's
   webhook, validates each file, skips duplicates and commits the rest.

Configuration: secret `DISCORD_BOT_TOKEN`, variables `DISCORD_CHANNEL_ID` and `DISCORD_WEBHOOK_ID`.

## Replaying a run

From a RogueDeck-Core checkout, with the content version the run was played on (the recording contains its
hash):

```bash
dotnet run --project src/RogueDeck.Bot.Cli -c Release -- \
  --game ../bnb-godot/content/game.roguedeck.json \
  --replay-run runs/<player>/<file>.json \
  --decisions decisions.jsonl
```

The output is `REPRODUCED` or the first room where the replay differs. `--decisions` writes one JSON object
per answer with the situation (act, room, HP), the options offered (doors, cards, hand) and the choice made.

## License

The collector code is licensed under [PolyForm Noncommercial 1.0.0](LICENSE). The recordings are playtest
data and may be used for noncommercial analysis with attribution.
