#!/usr/bin/env python3
"""Collect run recordings from the Discord channel the game posts them to, and file them in this repository.

The game (bnb-godot, scripts/RunLog.cs) posts every finished run as a `*.bnbrun.json` attachment to a webhook.
This script, run by .github/workflows/ingest.yml, reads the channel with a bot token, and for every new message:

  * accepts it only if the game's own webhook posted it (DISCORD_WEBHOOK_ID) — anyone else in the channel is ignored
  * checks the file is a run recording (format, player, seed, answers) and not absurdly large
  * files it as runs/<player>-<id>/<date>-seed<seed>-<result>-<hash>.json, once: the same file twice (the game
    retries an upload it did not see land) is recognised by its hash and skipped
  * adds one row to runs/index.csv

and then rewrites leaderboard.json — the closed-alpha ranking the game's title screen shows: per player, runs
played, wins, enemies felled, elite fights and boss fights won. It is rebuilt from every filed recording each
time (never added to), so it can never drift from the files; the kill counts come from each recording's
Tallies, which older recordings do not have (they count as runs and wins only).

It also rewrites the reports/ folder, the balancing view of the same files:

  * reports/cards.csv + cards.md — per card: how often it was offered as a pick and taken, seen and bought in
    shops, struck from a deck, played, and the win rate of the runs that took it. From each recording's Picks
    and CardPlays (a card is counted by its base id; an improved copy counts for the card it improves).
  * reports/feedback.csv + feedback.md — every Form B-7 the players filed: the rating (1 rigged … 5 entirely
    fair), their line, and the run it is about.

state/last_message_id remembers how far the channel has been read. Standard library only.
`python3 tools/ingest.py --leaderboard` rebuilds the ranking and the reports without reading Discord.
"""
import csv
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API = "https://discord.com/api/v10"
ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
INDEX = RUNS / "index.csv"
STATE = ROOT / "state" / "last_message_id"
LEADERBOARD = ROOT / "leaderboard.json"
REPORTS = ROOT / "reports"
MAX_BYTES = 2_000_000
MAX_ANSWERS = 500_000
COLUMNS = ["ended_utc", "player", "player_id", "seed", "character", "result", "reached", "answers",
           "rooms", "resumes", "engine", "content", "file", "sha256"]


def get(url, token=None):
    request = urllib.request.Request(url, headers={"User-Agent": "bnb-runs-ingest (github.com/Paranoidgrinch/bnb-runs, 1)"})
    if token:
        request.add_header("Authorization", f"Bot {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def slug(text, fallback):
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text or "").strip("-.").lower()
    return text[:40] or fallback


def valid(recording):
    """Why this is not a run recording, or None when it is one."""
    if not isinstance(recording, dict):
        return "not an object"
    if not isinstance(recording.get("Format"), int):
        return "no Format"
    player = recording.get("Player")
    if not isinstance(player, dict) or not isinstance(player.get("Name"), str) or not isinstance(player.get("Id"), str):
        return "no Player"
    start = recording.get("Start")
    if not isinstance(start, dict) or not isinstance(start.get("Seed"), int):
        return "no Start.Seed"
    answers = recording.get("Answers")
    if not isinstance(answers, list) or len(answers) > MAX_ANSWERS:
        return "no Answers"
    if not all(isinstance(a, list) and a and all(isinstance(p, str) for p in a) for a in answers):
        return "an answer is not a list of strings"
    return None


def known_hashes():
    if not INDEX.exists():
        return set()
    with INDEX.open(newline="") as handle:
        return {row["sha256"] for row in csv.DictReader(handle)}


def file_one(data, known):
    digest = hashlib.sha256(data).hexdigest()
    if digest in known:
        return "duplicate"
    try:
        recording = json.loads(data)
    except ValueError:
        return "not JSON"
    if (problem := valid(recording)) is not None:
        return problem

    player = recording["Player"]
    start = recording["Start"]
    rooms = recording.get("Rooms") or []
    ended = recording.get("EndedUtc") or recording.get("StartedUtc") or ""
    result = recording.get("Result") or "open"
    folder = RUNS / f"{slug(player['Name'], 'unnamed')}-{slug(player['Id'], 'noid')}"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{ended[:10] or 'undated'}-seed{start['Seed']}-{slug(result, 'open')}-{digest[:8]}.json"
    (folder / name).write_bytes(data)

    new_index = not INDEX.exists()
    with INDEX.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if new_index:
            writer.writeheader()
        game = recording.get("Game") or {}
        writer.writerow({
            "ended_utc": ended,
            "player": player["Name"],
            "player_id": player["Id"],
            "seed": start["Seed"],
            "character": start.get("Character") or "",
            "result": result,
            "reached": rooms[-1]["State"].split(" | ")[0] if rooms else "",
            "answers": len(recording["Answers"]),
            "rooms": len(rooms),
            "resumes": recording.get("Resumes", 0),
            "engine": game.get("Engine") or "",
            "content": game.get("Content") or "",
            "file": str((folder / name).relative_to(ROOT)),
            "sha256": digest,
        })
    known.add(digest)
    return None


def write_leaderboard():
    """Every player's totals across every filed recording, newest name kept."""
    players = {}
    for path in sorted(RUNS.glob("*/*.json")):
        try:
            recording = json.loads(path.read_bytes())
        except ValueError:
            continue
        if valid(recording) is not None:
            continue
        player = recording["Player"]
        ended = recording.get("EndedUtc") or recording.get("StartedUtc") or ""
        entry = players.setdefault(player["Id"], {
            "id": player["Id"], "name": player["Name"], "runs": 0, "wins": 0,
            "enemies": 0, "elites": 0, "bosses": 0, "last": ""})
        if ended >= entry["last"]:
            entry["last"] = ended
            entry["name"] = player["Name"]
        tallies = recording.get("Tallies") or {}
        entry["runs"] += 1
        entry["wins"] += 1 if recording.get("Result") == "Victory" else 0
        for key in ("enemies", "elites", "bosses"):
            value = tallies.get(key, 0)
            entry[key] += value if isinstance(value, int) and 0 <= value < 100_000 else 0
    board = {
        "generated_utc": max((p["last"] for p in players.values()), default=""),
        "players": sorted(players.values(), key=lambda p: (-p["runs"], p["name"].lower())),
    }
    text = json.dumps(board, indent=1, ensure_ascii=False) + "\n"
    if not LEADERBOARD.exists() or LEADERBOARD.read_text() != text:
        LEADERBOARD.write_text(text)
    print(f"leaderboard: {len(players)} players")


def recordings():
    for path in sorted(RUNS.glob("*/*.json")):
        try:
            recording = json.loads(path.read_bytes())
        except ValueError:
            continue
        if valid(recording) is None:
            yield path, recording


def write_if_changed(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def base(card):
    return card.rstrip("+")


def write_reports():
    """The balancing view: what happened to every card, and what the players said."""
    cards = {}
    feedback = []

    def card(cid):
        return cards.setdefault(base(cid), {
            "card": base(cid), "offered": 0, "taken": 0, "shop_seen": 0, "shop_bought": 0,
            "removed": 0, "plays": 0, "runs_taken": 0, "wins_taken": 0})

    for path, recording in recordings():
        won = recording.get("Result") == "Victory"
        took = set()
        for pick in recording.get("Picks") or []:
            if not isinstance(pick, dict):
                continue
            purpose = str(pick.get("Purpose") or "")
            offered = [c for c in pick.get("Offered") or [] if isinstance(c, str)]
            taken = [c for c in pick.get("Taken") or [] if isinstance(c, str)]
            if purpose.startswith("remove|"):
                for c in taken:
                    card(c)["removed"] += 1
            elif purpose == "shop":
                for c in offered:
                    card(c)["shop_seen"] += 1
                for c in taken:
                    card(c)["shop_bought"] += 1
                    took.add(base(c))
            else:
                for c in offered:
                    card(c)["offered"] += 1
                for c in taken:
                    card(c)["taken"] += 1
                    took.add(base(c))
        for c in took:
            card(c)["runs_taken"] += 1
            card(c)["wins_taken"] += 1 if won else 0
        for c, times in (recording.get("CardPlays") or {}).items():
            if isinstance(times, int) and 0 <= times < 1_000_000:
                card(c)["plays"] += times
        word = recording.get("Feedback")
        if isinstance(word, dict) and isinstance(word.get("Rating"), int):
            rooms = recording.get("Rooms") or []
            feedback.append({
                "ended_utc": recording.get("EndedUtc") or "",
                "player": recording["Player"]["Name"],
                "result": recording.get("Result") or "",
                "reached": rooms[-1]["State"].split(" | ")[0] if rooms else "",
                "rating": max(1, min(5, word["Rating"])),
                "comment": str(word.get("Comment") or "").replace("\n", " ")[:500],
                "file": str(path.relative_to(ROOT)),
            })

    def rate(part, whole):
        return f"{100 * part / whole:.0f}%" if whole else ""

    rows = sorted(cards.values(), key=lambda c: (-c["offered"], c["card"]))
    columns = ["card", "offered", "taken", "take_rate", "shop_seen", "shop_bought", "removed", "plays",
               "runs_taken", "win_rate_taken"]
    lines = [",".join(columns)]
    md = ["# Card report", "",
          "Every card the recordings mention. *Offered/taken* are picks (rewards and every other choice that",
          "gains a card); shops are counted apart. *Win rate taken* is over the runs that took the card at least",
          "once. Rebuilt with every ingest — do not edit.", "",
          "| card | offered | taken | take rate | shop seen | bought | removed | plays | runs taken | win rate |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c in rows:
        take = rate(c["taken"], c["offered"])
        win = rate(c["wins_taken"], c["runs_taken"])
        lines.append(",".join(str(v) for v in [c["card"], c["offered"], c["taken"], take, c["shop_seen"],
                                               c["shop_bought"], c["removed"], c["plays"], c["runs_taken"], win]))
        md.append(f"| {c['card']} | {c['offered']} | {c['taken']} | {take} | {c['shop_seen']} | {c['shop_bought']} "
                  f"| {c['removed']} | {c['plays']} | {c['runs_taken']} | {win} |")
    write_if_changed(REPORTS / "cards.csv", "\n".join(lines) + "\n")
    write_if_changed(REPORTS / "cards.md", "\n".join(md) + "\n")

    feedback.sort(key=lambda f: f["ended_utc"], reverse=True)
    with_rating = [f["rating"] for f in feedback]
    fmd = ["# Form B-7 — what the players said", "",
           f"{len(feedback)} filed · average {sum(with_rating) / len(with_rating):.1f} of 5" if with_rating
           else "Nothing filed yet.", ""]
    for result in ("Victory", "Defeat"):
        part = [f["rating"] for f in feedback if f["result"] == result]
        if part:
            fmd.append(f"* {result}: {len(part)} filed, average {sum(part) / len(part):.1f}")
    fmd += ["", "| filed | player | result | reached | rating | remarks |", "|---|---|---|---|---:|---|"]
    for f in feedback:
        fmd.append(f"| {f['ended_utc'][:16]} | {f['player']} | {f['result']} | {f['reached']} | {f['rating']} "
                   f"| {f['comment'].replace('|', '/')} |")
    header = ["ended_utc", "player", "result", "reached", "rating", "comment", "file"]
    import io
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header)
    writer.writeheader()
    writer.writerows(feedback)
    write_if_changed(REPORTS / "feedback.csv", out.getvalue())
    write_if_changed(REPORTS / "feedback.md", "\n".join(fmd) + "\n")
    print(f"reports: {len(cards)} cards, {len(feedback)} feedback forms")


def main():
    if "--leaderboard" in sys.argv:
        write_leaderboard()
        write_reports()
        return
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel = os.environ["DISCORD_CHANNEL_ID"]
    webhook = os.environ.get("DISCORD_WEBHOOK_ID", "")
    after = STATE.read_text().strip() if STATE.exists() else "0"
    known = known_hashes()
    filed = skipped = 0

    while True:
        batch = json.loads(get(f"{API}/channels/{channel}/messages?limit=100&after={after}", token))
        if not batch:
            break
        for message in sorted(batch, key=lambda m: int(m["id"])):
            after = message["id"]
            if webhook and message.get("webhook_id") != webhook:
                continue
            for attachment in message.get("attachments", []):
                if not attachment.get("filename", "").endswith(".bnbrun.json"):
                    continue
                if attachment.get("size", 0) > MAX_BYTES:
                    print(f"skip {attachment['filename']}: too large")
                    skipped += 1
                    continue
                problem = file_one(get(attachment["url"]), known)
                if problem is None:
                    filed += 1
                else:
                    print(f"skip {attachment['filename']} (message {message['id']}): {problem}")
                    skipped += 1
        if len(batch) < 100:
            break

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(after + "\n")
    print(f"filed {filed}, skipped {skipped}, read up to message {after}")
    write_leaderboard()
    write_reports()


if __name__ == "__main__":
    sys.exit(main())
