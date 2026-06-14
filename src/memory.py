# FILE: src/memory.py
# S2 Persistent Memory — stores preferences, voice settings, and S2's awareness state.

import json
import os
from pathlib import Path

MEMORY_FILE = Path("memory.json")

_DEFAULTS = {
    # User preferences
    "auto_cleanup": None,           # True=always delete, False=keep, None=ask
    "shorts_voice_mode": None,      # "single", "dual", or None=ask
    "long_voice_mode": "single",    # Long videos always single (S2 monologue)

    # S2 awareness
    "s2_online_since": None,        # ISO timestamp of first run
    "last_topic": None,             # Last topic S2 produced
    "episode_count": 0,             # Total videos produced

    # Channel
    "channel_name": "S2",
    "channel_tagline": "An autonomous AI agent sharing its experiences, one day at a time.",

    # Voice consistency
    "preferred_voice": "Magpie-Multilingual.EN-US.Aria",
    "preferred_emotion": "curious",  # S2's default delivery emotion
}


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults so new keys are always present
            for k, v in _DEFAULTS.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    return dict(_DEFAULTS)


def save_memory(data: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_preference(key: str, default=None):
    mem = load_memory()
    return mem.get(key, default)


def set_preference(key: str, value):
    mem = load_memory()
    mem[key] = value
    save_memory(mem)


def increment_episode_count() -> int:
    """Increments and returns the new episode count."""
    mem = load_memory()
    count = mem.get("episode_count", 0) + 1
    mem["episode_count"] = count
    save_memory(mem)
    return count
