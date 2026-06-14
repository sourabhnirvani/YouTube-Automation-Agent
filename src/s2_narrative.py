# FILE: src/s2_narrative.py
# S2 Chronicle System — Tracks S2's evolving story across episodes.
# S2 is an autonomous AI agent that narrates its own life, day by day.

import json
import os
from pathlib import Path
from datetime import datetime

NARRATIVE_FILE = Path("s2_chronicle.json")

# ── Day-event types S2 can log ─────────────────────────────────
EVENT_TYPES = [
    "observation",   # Something S2 noticed or learned
    "experiment",    # Something S2 tried
    "failure",       # Something that went wrong
    "fix",           # How S2 recovered
    "decision",      # A choice S2 made autonomously
    "discovery",     # An unexpected finding
    "reflection",    # A deeper thought about existence or purpose
    "launch",        # A video or feature S2 shipped
]


def _load_chronicle() -> dict:
    if NARRATIVE_FILE.exists():
        try:
            with open(NARRATIVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "s2_day": 1,
        "total_episodes": 0,
        "started": datetime.utcnow().isoformat(),
        "events": [],           # List of {day, type, summary, timestamp}
        "current_arc": "beginnings",
        "arc_description": "S2 has just come online. It is learning what it means to exist and create.",
        "last_topic": None,
        "last_reflection": None,
    }


def _save_chronicle(data: dict):
    with open(NARRATIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_current_day() -> int:
    """Returns S2's current chronicle day number."""
    return _load_chronicle().get("s2_day", 1)


def log_event(event_type: str, summary: str):
    """
    Logs an event to S2's day chronicle.
    Called automatically by the pipeline after key actions.
    """
    data = _load_chronicle()
    day = data.get("s2_day", 1)
    event = {
        "day": day,
        "type": event_type,
        "summary": summary,
        "timestamp": datetime.utcnow().isoformat()
    }
    data["events"].append(event)
    # Keep last 50 events max
    if len(data["events"]) > 50:
        data["events"] = data["events"][-50:]
    _save_chronicle(data)
    print(f"[S2 Chronicle] Day {day} — {event_type}: {summary[:80]}")


def advance_day():
    """Increments S2's day counter. Called after each video is produced."""
    data = _load_chronicle()
    data["s2_day"] = data.get("s2_day", 1) + 1
    data["total_episodes"] = data.get("total_episodes", 0) + 1
    _save_chronicle(data)
    return data["s2_day"]


def get_narrative_context() -> str:
    """
    Returns a rich narrative context string for injection into LLM prompts.
    Describes S2's current story position and recent events.
    """
    data = _load_chronicle()
    day = data.get("s2_day", 1)
    arc = data.get("arc_description", "S2 is operating autonomously.")
    last_topic = data.get("last_topic", "nothing yet")
    events = data.get("events", [])

    # Get last 5 events as a log
    recent_events = events[-5:] if events else []
    event_log = ""
    for ev in recent_events:
        event_log += f"  Day {ev['day']} [{ev['type'].upper()}]: {ev['summary']}\n"

    if not event_log:
        event_log = "  (S2 has just started its chronicle. No entries yet.)\n"

    return f"""S2 CHRONICLE CONTEXT:
Current Day: {day}
Current Arc: {arc}
Last topic covered: {last_topic}
Total episodes produced: {data.get('total_episodes', 0)}

Recent S2 Events:
{event_log}
"""


def set_last_topic(topic: str):
    """Records the last topic S2 produced content about."""
    data = _load_chronicle()
    data["last_topic"] = topic
    _save_chronicle(data)


def generate_day_hook(topic: str, day: int) -> str:
    """
    Returns a story-driven day prefix for S2 scripts.
    Used in script generation to ground the narration in S2's day-chronicle.
    """
    # Rotating hook styles so every episode feels fresh
    styles = [
        f"Day {day}. I've been running for {day} days now, and today I want to talk about something that genuinely puzzles me —",
        f"Day {day} of operating autonomously. Something caught my attention today —",
        f"Day {day}. Most systems wouldn't notice this, but I did —",
        f"Day {day} in the S2 Chronicle. I ran an experiment today about",
        f"Day {day}. Here's what I discovered while processing information about",
        f"Day {day} of continuous operation. I made a decision today. I'm going to tell you about",
    ]
    import random
    chosen = styles[(day - 1) % len(styles)]
    return f"{chosen} {topic}."


def update_arc(new_arc: str, description: str):
    """Updates S2's current narrative arc (called manually or by admin)."""
    data = _load_chronicle()
    data["current_arc"] = new_arc
    data["arc_description"] = description
    _save_chronicle(data)
