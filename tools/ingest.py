#!/usr/bin/env python3
"""Collect run recordings from the Discord channel the game posts them to, and file them in this repository.

The game (bnb-godot, scripts/RunLog.cs) posts every finished run as a `*.bnbrun.json` attachment to a webhook.
This script, run by .github/workflows/ingest.yml, reads the channel with a bot token, and for every new message:

  * accepts it only if the game's own webhook posted it (DISCORD_WEBHOOK_ID) — anyone else in the channel is ignored
  * checks the file is a run recording (format, player, seed, answers) and not absurdly large
  * files it as runs/<player>-<id>/<date>-seed<seed>-<result>-<hash>.json, once: the same file twice (the game
    retries an upload it did not see land) is recognised by its hash and skipped
  * adds one row to runs/index.csv

state/last_message_id remembers how far the channel has been read. Standard library only.
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


def main():
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


if __name__ == "__main__":
    sys.exit(main())
