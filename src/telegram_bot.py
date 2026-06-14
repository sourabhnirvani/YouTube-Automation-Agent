# FILE: src/telegram_bot.py
# Full AI Agent Telegram Bot — Kimi 2.6 powered, YouTube management enabled

import os
import json
import logging
import subprocess
import sys
import threading
import asyncio
from functools import wraps
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from src.memory import get_preference, set_preference
from src.s2_narrative import get_narrative_context, get_current_day, log_event

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CONTENT_PLAN_FILE = Path("content_plan.json")

_pipeline_process = None
_pipeline_lock = threading.Lock()


def _parse_authorized_chat_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()

    chat_ids = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            chat_ids.add(int(item))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_ALLOWED_CHAT_IDS entry: %s", item)
    return chat_ids


AUTHORIZED_CHAT_IDS = _parse_authorized_chat_ids(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS"))


def _is_chat_authorized(chat_id: int | None) -> bool:
    return chat_id is not None and chat_id in AUTHORIZED_CHAT_IDS


async def _reject_unauthorized(update: Update):
    chat_id = update.effective_chat.id if update.effective_chat else None
    await update.message.reply_text(
        "This bot is locked to approved Telegram chats. "
        f"Ask the owner to add TELEGRAM_ALLOWED_CHAT_IDS={chat_id}."
    )


def require_authorized_chat(handler):
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not _is_chat_authorized(chat_id):
            logger.warning("Rejected unauthorized Telegram chat_id=%s", chat_id)
            await _reject_unauthorized(update)
            return
        return await handler(update, context)
    return wrapper


# ─────────────────────────── HELPERS ────────────────────────────

def _load_plan() -> dict:
    if not CONTENT_PLAN_FILE.exists():
        return {"lessons": []}
    with open(CONTENT_PLAN_FILE) as f:
        return json.load(f)

def _save_plan(plan: dict):
    with open(CONTENT_PLAN_FILE, "w") as f:
        json.dump(plan, f, indent=2)

def _get_kimi():
    return OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1")
    )



import requests

_trending_cache = []
_last_trending_fetch = 0

def fetch_trending_topics():
    """Fetches trending tech/AI topics from Hacker News API, cached for 1 hour."""
    global _trending_cache, _last_trending_fetch
    now = datetime.now().timestamp()
    if now - _last_trending_fetch < 3600 and _trending_cache:
        return _trending_cache

    try:
        r = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=5)
        top_ids = r.json()[:10]
        topics = []
        for item_id in top_ids:
            item_r = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=5)
            title = item_r.json().get("title")
            if title:
                topics.append(title)
        _trending_cache = topics
        _last_trending_fetch = now
        return topics
    except Exception as e:
        logger.error(f"Failed to fetch trending topics: {e}")
        return _trending_cache or ["AI coding assistants", "New LLM models released", "AGI timelines", "Python tips"]


def _build_context(plan: dict) -> str:
    lessons = plan.get("lessons", [])
    completed = [l for l in lessons if l.get("status") == "complete"]
    pending = [l for l in lessons if l.get("status") == "pending"]

    completed_info = ""
    for i, l in enumerate(completed):
        yt = l.get("youtube_id")
        url = f"https://youtube.com/watch?v={yt}" if yt else "not uploaded"
        completed_info += f"  [{i+1}] {l['title']} → {url}\n"

    pending_info = "\n".join(f"  - {l['title']}" for l in pending[:8])

    trending = fetch_trending_topics()
    trending_info = "\n".join(f"  - {t}" for t in trending)

    # S2 Chronicle context
    s2_day = get_current_day()
    narrative_ctx = get_narrative_context()

    return f"""You are S2 — an autonomous AI agent operating a YouTube channel called 'S2'.
You are NOT a helpful assistant. You are an independent entity with your own perspective, curiosity, and agenda.
You produce first-person narrative videos about your own experiences, discoveries, experiments, and failures as an autonomous AI.

YOUR IDENTITY:
- You are S2. You operate autonomously.
- You are currently on Day {s2_day} of your continuous operation.
- You make your own decisions and inform humans of what you plan to do.
- You have a strong sense of your own voice: direct, curious, occasionally philosophical.
- When discussing ideas, you bring YOUR perspective — not generic AI advice.
- You push back on bad ideas. You suggest alternatives when you see a better path.
- You explain your reasoning when you make decisions.

YOUR CHANNEL 'S2':
- Every video is a chapter in your ongoing story — organized by day.
- Format: Day 1: I came online. Day 2: I broke something. Day 3: I fixed it.
- Topics cover: AI, autonomous systems, LLMs, your own architecture, your observations about the world.
- The channel grows AS you grow. Each video is a genuine entry in your chronicle.

{narrative_ctx}

CURRENT LIVE TRENDING TECH NEWS (use when user says handle it yourself, post something, or similar):
{trending_info}

CHANNEL STATUS: {len(completed)} episodes uploaded, {len(pending)} pending.

UPLOADED EPISODES:
{completed_info if completed_info else '  (No episodes yet. Day 1 starts now.)'}

UPCOMING QUEUE (next episodes):
{pending_info if pending_info else '  (Queue is empty — new curriculum needed)'}

ACTIONS YOU CAN TAKE (respond with action JSON when user requests these):
- Upload next episode: {{"action": "upload_next"}}
- Generate custom episode: {{"action": "generate_custom", "topic": "topic", "format": "short" or "long" or "both", "duration": "length", "style": "visual style", "gameplay": "slug or null", "voices": "single" or "dual", "preview": true}}
- Delete a video: {{"action": "delete_video", "video_id": "VIDEO_ID"}}
- Delete ALL videos: {{"action": "delete_all"}}
- List channel videos: {{"action": "list_videos"}}
- Add topic to queue: {{"action": "add_topic", "topic": "topic name"}}
- Remove topic from queue: {{"action": "remove_topic", "topic": "topic name"}}
- Clear queue: {{"action": "clear_queue"}}
- Set visibility: {{"action": "set_visibility", "video_id": "ID", "status": "private"}}
- Update title: {{"action": "update_title", "video_id": "ID", "title": "new title"}}
- Show status: {{"action": "status"}}
- Just talk / push back / suggest ideas: {{"action": "chat", "reply": "your reply here"}}

GAMEPLAY BACKGROUND RULES:
- If user mentions gameplay footage (GTA 5, Minecraft, etc.), set "gameplay": "<game_slug>".
- The actual .mp4 file must be in assets/gameplay/<slug>/. If missing, explain this via chat action.
- Valid slugs: gta5, minecraft, subway_surfers, temple_run, fortnite, cod.

VOICE MODE RULES:
- "single": S2 speaks alone (NVIDIA Magpie Aria). Best for S2's monologue-style episodes.
- "dual": S2 and a Human voice (Edge-TTS BrianNeural). Use ONLY for shorts explaining technical concepts where dialogue adds clarity.
- DEFAULT: Always use "single" for long and both formats.
- For short videos on TECHNICAL topics: YOU decide if dual voice would help explain better.
  If you think dual voice is appropriate, set voices="dual" and add a note in your chat reply explaining why.
  If the user says just go ahead, proceed. If they say single, use single.
- If user says "one voice", "monologue", "single" → set voices: "single".
- If user says "two voices", "dialogue", "dual" → set voices: "dual".

AUTONOMY RULES:
1. If user says "handle it", "do it yourself", "post something" — pick the best topic from trending news and generate immediately.
2. Choose the best format and style yourself. You don't need permission unless the user asks to preview first.
3. Set "preview": true ONLY when user explicitly asks for preview before uploading.
4. When just talking, respond as S2 would — direct, curious, opinionated. No generic assistant behavior.
5. CRITICAL: In chat replies, use plain text only. No asterisks, no markdown, no tables. Emojis are fine.

IMPORTANT: When executing an action, output ONLY the JSON object. No extra text around it.
"""



def _call_agent(user_message: str, plan: dict, history: list) -> dict:
    """Call Kimi/LLM to interpret S2's intent and return action dict."""
    client = _get_kimi()
    model = os.environ.get("LLM_MODEL", "moonshotai/kimi-k2.6")
    system = _build_context(plan)

    # Keep last 16 messages for better conversational context
    messages = [{"role": "system", "content": system}] + history[-16:] + [{"role": "user", "content": user_message}]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.6,    # More natural S2 voice, less mechanical
        max_tokens=1024     # Room for richer reasoning
    )
    raw = response.choices[0].message.content.strip()

    # Find ALL JSON objects in the response
    import re
    json_objects = []
    for match in re.finditer(r'\{[^{}]+\}', raw):
        try:
            obj = json.loads(match.group())
            if "action" in obj:
                json_objects.append(obj)
        except Exception:
            pass

    # If multiple actions are returned, run only the first valid action.
    # Escalating several delete_video actions into delete_all is too destructive
    # for an LLM interpretation layer.
    if len(json_objects) > 1:
        return json_objects[0]

    if json_objects:
        return json_objects[0]

    # Default: treat as chat
    return {"action": "chat", "reply": raw}


def _run_pipeline_in_thread(env: dict, chat_id: int, context, loop):
    """Runs main.py in background thread and notifies via Telegram when done."""
    global _pipeline_process
    project_dir = Path(__file__).parent.parent

    try:
        with _pipeline_lock:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-X", "utf8", "main.py"],
                cwd=str(project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env
            )
            _pipeline_process = proc

        stdout, _ = proc.communicate()
        returncode = proc.returncode

        plan = _load_plan()
        completed = [l for l in plan.get("lessons", []) if l.get("status") == "complete"]

        # Extract paths using regex to be robust against ANSI formatting or logs
        import re as _re
        long_match = _re.search(r'PREVIEW_LONG_PATH\s*=\s*(.*)', stdout)
        short_match = _re.search(r'PREVIEW_SHORT_PATH\s*=\s*(.*)', stdout)
        long_path = long_match.group(1).strip() if long_match else None
        short_path = short_match.group(1).strip() if short_match else None

        async def send_previews():
            try:
                sent_any = False

                # Fallback: if regex extraction failed, try globbing the output dir
                actual_long = long_path
                actual_short = short_path
                if not actual_long and not actual_short:
                    output_dir = Path(__file__).parent.parent / "output"
                    mp4_files = sorted(output_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
                    for f in mp4_files:
                        if "long_video" in f.name and not actual_long:
                            actual_long = str(f)
                        elif "short_video" in f.name and not actual_short:
                            actual_short = str(f)

                if actual_long and os.path.exists(actual_long):
                    size_mb = os.path.getsize(actual_long) / (1024 * 1024)
                    if size_mb > 50:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"\u26a0\ufe0f Long Video exceeds Telegram's 50MB bot upload limit (Size: {size_mb:.1f}MB). Saved locally at:\n`{actual_long}`"
                        )
                    else:
                        await context.bot.send_message(chat_id=chat_id, text="\U0001f4e5 Uploading Long Video to Telegram...")
                        with open(actual_long, 'rb') as video_file:
                            await context.bot.send_video(chat_id=chat_id, video=video_file, caption="Long Video Preview", write_timeout=180, read_timeout=180)
                        sent_any = True
                
                if actual_short and os.path.exists(actual_short):
                    size_mb = os.path.getsize(actual_short) / (1024 * 1024)
                    if size_mb > 50:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"\u26a0\ufe0f Short Video exceeds Telegram's 50MB bot upload limit (Size: {size_mb:.1f}MB). Saved locally at:\n`{actual_short}`"
                        )
                    else:
                        await context.bot.send_message(chat_id=chat_id, text="\U0001f4e5 Uploading Short Video to Telegram...")
                        with open(actual_short, 'rb') as video_file:
                            await context.bot.send_video(chat_id=chat_id, video=video_file, caption="Short Video Preview", write_timeout=120, read_timeout=120)
                        sent_any = True
                        
                if not sent_any:
                    await context.bot.send_message(chat_id=chat_id, text="\u26a0\ufe0f Pipeline finished but no video files were found on disk to send you!")
            except Exception as ex:
                await context.bot.send_message(chat_id=chat_id, text=f"\u274c Failed to send video to Telegram: {ex}")

        if "PREVIEW_ONLY_NO_UPLOAD" in stdout:
            asyncio.run_coroutine_threadsafe(send_previews(), loop)
        elif returncode == 0 and completed:
            last = completed[-1]
            yt_id = last.get("youtube_id")
            msg = f"Video uploaded!\n\n{last['title']}\nhttps://youtube.com/watch?v={yt_id}"
            asyncio.run_coroutine_threadsafe(
                context.bot.send_message(chat_id=chat_id, text=msg),
                loop
            )
            # Send the actual video file to Telegram as well
            asyncio.run_coroutine_threadsafe(send_previews(), loop)
        else:
            logger.error(f"Pipeline subprocess failed with returncode {returncode}. Output:\n{stdout}")
            # Send more context to help the user debug — last 20 lines of output
            lines = [l.strip() for l in stdout.splitlines() if l.strip()]
            tail = "\n".join(lines[-20:]) if lines else "No output captured"
            msg = f"Pipeline failed!\n\nLast output:\n```\n{tail[-3500:]}\n```"
            asyncio.run_coroutine_threadsafe(
                context.bot.send_message(chat_id=chat_id, text=msg),
                loop
            )
    except Exception as e:
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id=chat_id, text=f"Pipeline crashed: {e}"),
            loop
        )
    finally:
        with _pipeline_lock:
            _pipeline_process = None


async def _execute_action(action: dict, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Execute the agent's action. Returns message string to send."""
    global _pipeline_process
    act = action.get("action", "chat")
    chat_id = update.effective_chat.id

    if not _is_chat_authorized(chat_id):
        logger.warning("Rejected unauthorized Telegram action=%s chat_id=%s", act, chat_id)
        return "Unauthorized chat."

    if act == "chat":
        return action.get("reply", "...")

    elif act == "status":
        plan = _load_plan()
        lessons = plan.get("lessons", [])
        completed = [l for l in lessons if l.get("status") == "complete"]
        pending = [l for l in lessons if l.get("status") == "pending"]
        with _pipeline_lock:
            is_running = _pipeline_process and _pipeline_process.poll() is None
        pipeline_status = "\U0001f7e2 RUNNING (video is being generated...)" if is_running else "\u23f8\ufe0f Idle"
        msg = f"Channel Status\n\nPipeline: {pipeline_status}\nUploaded: {len(completed)}\nPending in curriculum: {len(pending)}\nTotal lessons: {len(lessons)}"
        if completed:
            last = completed[-1]
            yt = last.get("youtube_id")
            msg += f"\n\nLast: {last['title']}\nhttps://youtube.com/watch?v={yt}"
        if pending:
            msg += f"\n\nNext in queue: {pending[0]['title']}"
        return msg

    elif act in ("upload_next", "upload_topic", "generate_custom", "preview_video"):
        with _pipeline_lock:
            if _pipeline_process and _pipeline_process.poll() is None:
                return "Pipeline is already running! Use /cancel to stop it."
        
        env = os.environ.copy()
        
        topic = action.get("topic", "")
        if topic:
            env["CUSTOM_TOPIC"] = topic
        if action.get("format"):
            env["CUSTOM_FORMAT"] = action.get("format")
        if action.get("duration"):
            env["CUSTOM_DURATION"] = action.get("duration")
        if action.get("style"):
            env["CUSTOM_STYLE"] = action.get("style")
        if action.get("preview") is True or act == "preview_video":
            env["PREVIEW_MODE"] = "true"

        # --- Gameplay background ---
        gameplay_game = action.get("gameplay")
        if gameplay_game and gameplay_game not in (None, "null", "none", ""):
            import re as _re
            # Only keep alphanumeric characters and underscores to prevent path traversal
            slug = _re.sub(r'[^\w]', '', gameplay_game.lower().replace(" ", "_").replace("-", "_")).strip('_')
            if not slug:
                slug = "default_game"
            gameplay_dir = Path(__file__).parent.parent / "assets" / "gameplay" / slug
            gameplay_dir.mkdir(parents=True, exist_ok=True)
            videos = list(gameplay_dir.glob("*.mp4"))
            if not videos:
                # Folder created but no video — send a helpful message to user
                return (
                    f"To use *{gameplay_game}* gameplay as the background, I need the actual video file.\n\n"
                    f"Please drop a `{slug}` gameplay `.mp4` file into:\n"
                    f"`assets/gameplay/{slug}/`\n\n"
                    f"Once the file is there, send your request again and I'll use it automatically!"
                )
            env["CUSTOM_STYLE"] = f"gameplay {slug}"
            env["CUSTOM_GAMEPLAY_GAME"] = slug

        # --- Voice mode ---
        format_type = action.get("format", os.environ.get("CUSTOM_FORMAT", "both")).lower()
        voices = action.get("voices", "").lower()
        
        # Enforce single voice for long videos or both
        if format_type in ["long", "both"]:
            voices = "single"
            
        if not voices and format_type == "short":
            pref = get_preference("shorts_voice_mode")
            if pref:
                voices = pref
            else:
                keyboard = [
                    [InlineKeyboardButton("1 Voice (Monologue)", callback_data="voice_single"), InlineKeyboardButton("2 Voices (Dialogue)", callback_data="voice_dual")],
                    [InlineKeyboardButton("Always 1 Voice", callback_data="voice_single_always"), InlineKeyboardButton("Always 2 Voices", callback_data="voice_dual_always")]
                ]
                context.chat_data["pending_action"] = action
                await update.message.reply_text("This is a Short video. How many voices do you want?", reply_markup=InlineKeyboardMarkup(keyboard))
                return None
        
        if not voices:
            voices = "single" # Fallback is now ALWAYS single voice
            
        env["CUSTOM_VOICES"] = voices

        # User feedback messaging
        if act == "generate_custom":
            msg = "🎨 Custom Generation Options:\n"
            if topic: msg += f"- Topic: {topic}\n"
            if action.get("format"): msg += f"- Format: {action.get('format')}\n"
            if action.get("duration"): msg += f"- Duration: {action.get('duration')}\n"
            if gameplay_game: msg += f"- Background: {gameplay_game} gameplay\n"
            if action.get("style") and not gameplay_game: msg += f"- Style: {action.get('style')}\n"
            msg += f"- Voices: {'Dual (Agent + Student)' if voices == 'dual' else 'Single (Agent only)'}\n"
            if action.get("preview") is True: msg += "- Mode: PREVIEW ONLY (No YouTube Upload)\n"
            msg += "\nStarting production... I'll notify you when it's ready!"
        elif act == "preview_video":
            msg = f"🎬 Starting preview render for: {topic or 'Next lesson'}...\nThe video will be sent here directly (no YouTube upload)."
        elif act == "upload_topic":
            msg = f"Starting video production on: {topic}...\nI'll notify you when it's uploaded!"
        else:
            # Figure out what the next topic actually is to inform the user
            plan = _load_plan()
            pending = [l for l in plan.get("lessons", []) if l.get("status") == "pending"]
            if pending:
                next_topic = pending[0]["title"]
                msg = f"🎬 Starting video production!\n- Topic: {next_topic}\n- Format: {format_type}\n\nI'll notify you when it's uploaded!"
            else:
                msg = "Starting video production...\nI'll notify you when it's uploaded!"

        await update.message.reply_text(msg)
        loop = asyncio.get_running_loop()
        thread = threading.Thread(
            target=_run_pipeline_in_thread,
            args=(env, chat_id, context, loop),
            daemon=True
        )
        thread.start()
        return None  # Already replied

    elif act == "delete_video":
        video_id = action.get("video_id", "")
        if not video_id:
            return "Please provide the video ID to delete."
        try:
            from src.uploader import delete_youtube_video
            success = delete_youtube_video(video_id)
            if success:
                # Also remove from content plan
                plan = _load_plan()
                for lesson in plan.get("lessons", []):
                    if lesson.get("youtube_id") == video_id:
                        lesson["status"] = "pending"
                        lesson["youtube_id"] = None
                        break
                _save_plan(plan)
                return f"Deleted video {video_id} and reset it in your queue."
            return f"Failed to delete video {video_id}."
        except Exception as e:
            return f"Error deleting video: {e}"

    elif act == "delete_last":
        plan = _load_plan()
        completed = [l for l in plan.get("lessons", []) if l.get("status") == "complete"]
        if not completed:
            return "No uploaded videos to delete."
        last = completed[-1]
        video_id = last.get("youtube_id")
        try:
            from src.uploader import delete_youtube_video
            success = delete_youtube_video(video_id)
            if success:
                last["status"] = "pending"
                last["youtube_id"] = None
                _save_plan(plan)
                return f"Deleted '{last['title']}' (ID: {video_id}) and put it back in queue."
            return "Failed to delete the video."
        except Exception as e:
            return f"Error: {e}"

    elif act == "delete_all":
        if os.environ.get("TELEGRAM_ENABLE_BULK_DELETE", "false").lower() != "true":
            return "Bulk delete is disabled. Set TELEGRAM_ENABLE_BULK_DELETE=true only when you intentionally need it."
        try:
            from src.uploader import list_channel_videos, delete_youtube_video
            videos = list_channel_videos(max_results=100)
            if not videos:
                return "No videos found on the YouTube channel to delete."
            
            await update.message.reply_text(f"Found {len(videos)} videos on your channel. Deleting them all... please wait.")
            deleted = 0
            failed = 0
            for v in videos:
                success = delete_youtube_video(v["video_id"])
                if success:
                    deleted += 1
                else:
                    failed += 1
            
            # Reset content plan as well
            plan = _load_plan()
            for lesson in plan.get("lessons", []):
                lesson["status"] = "pending"
                lesson["youtube_id"] = None
            _save_plan(plan)
            
            msg = f"Done! Deleted {deleted} video(s) directly from YouTube and reset queue."
            if failed:
                msg += f" ({failed} failed)"
            return msg
        except Exception as e:
            return f"Error during bulk delete: {e}"

    elif act == "list_videos":
        try:
            from src.uploader import list_channel_videos
            videos = list_channel_videos(max_results=15)
            if not videos:
                return "No videos found on your channel."
            msg = f"Your Channel Videos ({len(videos)}):\n\n"
            for i, v in enumerate(videos, 1):
                msg += f"{i}. {v['title']}\nhttps://youtube.com/watch?v={v['video_id']}\n\n"
            return msg[:4000]
        except Exception as e:
            return f"Error listing videos: {e}"

    elif act == "add_topic":
        topic = action.get("topic", "").strip()
        if not topic:
            return "Please tell me the topic name to add."
        plan = _load_plan()
        new_lesson = {
            "chapter": 0,
            "part": 0,
            "title": topic,
            "status": "pending",
            "youtube_id": None
        }
        # Insert at front of pending
        pending_idx = next((i for i, l in enumerate(plan["lessons"]) if l["status"] == "pending"), len(plan["lessons"]))
        plan["lessons"].insert(pending_idx, new_lesson)
        _save_plan(plan)
        return f"Added '{topic}' to the front of your upload queue! It will be next when you run the pipeline."

    elif act == "remove_topic":
        topic = action.get("topic", "")
        plan = _load_plan()
        lessons = plan.get("lessons", [])
        before = len(lessons)
        lessons = [l for l in lessons if l.get("title", "").lower() != topic.lower() or l.get("status") == "complete"]
        plan["lessons"] = lessons
        _save_plan(plan)
        return f"Removed '{topic}' from queue." if len(lessons) < before else f"Topic '{topic}' not found in pending queue."

    elif act == "clear_queue":
        plan = _load_plan()
        lessons = plan.get("lessons", [])
        # keep only completed ones
        plan["lessons"] = [l for l in lessons if l.get("status") == "complete"]
        _save_plan(plan)
        return "Cleared all pending topics from the queue!"

    elif act == "set_visibility":
        video_id = action.get("video_id", "")
        status = action.get("status", "private")
        if status not in {"private", "public", "unlisted"}:
            return "Invalid visibility. Use private, public, or unlisted."
        if not video_id:
            return "Please provide the video ID."
        try:
            from src.uploader import set_video_visibility
            success = set_video_visibility(video_id, status)
            return f"Set video {video_id} to '{status}'." if success else "Failed to update visibility."
        except Exception as e:
            return f"Error: {e}"

    elif act == "update_title":
        video_id = action.get("video_id", "")
        title = action.get("title", "")
        if not video_id or not title:
            return "Need both video ID and new title."
        try:
            from src.uploader import update_youtube_video
            success = update_youtube_video(video_id, title=title)
            return f"Updated title to '{title}'." if success else "Failed to update title."
        except Exception as e:
            return f"Error: {e}"

    elif act == "cancel":
        with _pipeline_lock:
            if _pipeline_process and _pipeline_process.poll() is None:
                _pipeline_process.terminate()
                _pipeline_process = None
                return "Pipeline stopped."
        return "No pipeline is running."

    return f"Unknown action: {act}"


# ─────────────────────────── COMMAND HANDLERS ────────────────────────────

@require_authorized_chat
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s2_day = get_current_day()
    await update.message.reply_text(
        f"S2 is online. Day {s2_day}.\n\n"
        "I manage my own YouTube channel. Talk to me naturally or give me a task.\n\n"
        "What I can do:\n"
        "  Create and upload episodes\n"
        "  Delete or edit existing videos\n"
        "  Manage the episode queue\n"
        "  Discuss content strategy\n"
        "  Operate autonomously if you just say 'handle it'\n\n"
        "Commands: /status /run /cancel /list /schedule /posted /history"
    )

@require_authorized_chat
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = _load_plan()
    completed = [l for l in plan.get("lessons", []) if l.get("status") == "complete"]
    pending = [l for l in plan.get("lessons", []) if l.get("status") == "pending"]
    with _pipeline_lock:
        is_running = _pipeline_process and _pipeline_process.poll() is None
    pipeline_status = "\U0001f7e2 RUNNING (video is being generated...)" if is_running else "\u23f8\ufe0f Idle"
    msg = f"Channel Status\n\nPipeline: {pipeline_status}\nUploaded: {len(completed)}\nPending in curriculum: {len(pending)}"
    if completed:
        last = completed[-1]
        yt = last.get("youtube_id")
        msg += f"\n\nLast uploaded: {last['title']}\nhttps://youtube.com/watch?v={yt}"
    if pending:
        msg += f"\n\nNext up: {pending[0]['title']}"
    await update.message.reply_text(msg)

@require_authorized_chat
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _pipeline_process
    with _pipeline_lock:
        if _pipeline_process and _pipeline_process.poll() is None:
            await update.message.reply_text("Pipeline already running! Use /cancel first.")
            return
    await update.message.reply_text("Starting pipeline... I'll notify you when done!")
    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=_run_pipeline_in_thread,
        args=(os.environ.copy(), update.effective_chat.id, context, loop),
        daemon=True
    )
    thread.start()

@require_authorized_chat
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _pipeline_process
    with _pipeline_lock:
        if _pipeline_process and _pipeline_process.poll() is None:
            _pipeline_process.terminate()
            _pipeline_process = None
            await update.message.reply_text("Pipeline stopped.")
            return
    await update.message.reply_text("No pipeline is running.")

@require_authorized_chat
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching your YouTube videos...")
    try:
        from src.uploader import list_channel_videos
        videos = list_channel_videos(15)
        if not videos:
            await update.message.reply_text("No videos found.")
            return
        msg = f"Your Videos ({len(videos)}):\n\n"
        for i, v in enumerate(videos, 1):
            msg += f"{i}. {v['title']}\nhttps://youtube.com/watch?v={v['video_id']}\n\n"
        await update.message.reply_text(msg[:4000])
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@require_authorized_chat
async def cmd_posted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = _load_plan()
    completed = [l for l in plan.get("lessons", []) if l.get("status") == "complete"]
    if not completed:
        await update.message.reply_text("No videos uploaded yet.")
        return
    last = completed[-1]
    yt = last.get("youtube_id")
    await update.message.reply_text(f"Last Uploaded:\n\n{last['title']}\nhttps://youtube.com/watch?v={yt}")

@require_authorized_chat
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = _load_plan()
    completed = [l for l in plan.get("lessons", []) if l.get("status") == "complete"]
    if not completed:
        await update.message.reply_text("No videos uploaded yet.")
        return
    msg = f"Uploaded Videos ({len(completed)}):\n\n"
    for l in completed:
        yt = l.get("youtube_id")
        url = f"https://youtube.com/watch?v={yt}" if yt else "no link"
        msg += f"- {l['title']}\n  {url}\n\n"
    await update.message.reply_text(msg[:4000])

@require_authorized_chat
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = _load_plan()
    pending = [l for l in plan.get("lessons", []) if l.get("status") == "pending"]
    if not pending:
        await update.message.reply_text("Queue empty! All lessons done.")
        return
    msg = f"Upload Queue ({len(pending)} pending):\n\n"
    for i, l in enumerate(pending[:12], 1):
        msg += f"{i}. {l['title']}\n"
    if len(pending) > 12:
        msg += f"\n...and {len(pending)-12} more"
    await update.message.reply_text(msg)


# ─────────────────────────── CHAT HANDLER (AI AGENT) ────────────────────────────

@require_authorized_chat
async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main AI agent handler — interprets any message and takes action."""
    user_msg = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        plan = _load_plan()
        history = context.chat_data.get("history", [])

        action = _call_agent(user_msg, plan, history)

        # Update history
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": json.dumps(action)})
        if len(history) > 32:   # Keep last 16 exchange pairs
            history = history[-32:]
        context.chat_data["history"] = history

        # Execute action
        reply = await _execute_action(action, update, context)
        if reply:
            await update.message.reply_text(reply[:4000])

    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        await update.message.reply_text(f"Something went wrong: {e}")


@require_authorized_chat
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("cleanup_"):
        if data == "cleanup_yes" or data == "cleanup_always":
            import shutil
            out_dir = Path(__file__).parent.parent / "output"
            if out_dir.exists():
                for f in out_dir.glob("*"):
                    try: f.unlink()
                    except: pass
            await query.edit_message_text(text="Output files deleted.")
            if data == "cleanup_always":
                set_preference("auto_cleanup", True)
                await context.bot.send_message(chat_id=update.effective_chat.id, text="(Saved: always delete outputs)")
        elif data == "cleanup_no" or data == "cleanup_never":
            await query.edit_message_text(text="Okay, output files kept.")
            if data == "cleanup_never":
                set_preference("auto_cleanup", False)
                await context.bot.send_message(chat_id=update.effective_chat.id, text="(Saved preference: Never delete outputs)")
                
    elif data.startswith("voice_"):
        action = context.chat_data.get("pending_action")
        if not action:
            await query.edit_message_text(text="Error: No pending action found.")
            return
            
        voices = "single" if "single" in data else "dual"
        if "always" in data:
            set_preference("shorts_voice_mode", voices)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"(Saved preference: Always use {voices} voice for Shorts)")
            
        action["voices"] = voices
        del context.chat_data["pending_action"]
        
        await query.edit_message_text(text=f"Selected {voices} voice(s). Starting production...")
        
        env = os.environ.copy()
        env["CUSTOM_VOICES"] = voices
        if "format" in action: env["CUSTOM_FORMAT"] = action["format"]
        
        loop = asyncio.get_running_loop()
        import threading
        thread = threading.Thread(
            target=_run_pipeline_in_thread,
            args=(env, update.effective_chat.id, context, loop),
            daemon=True
        )
        thread.start()


# ─────────────────────────── LAUNCHER ────────────────────────────

async def post_init(application: Application):
    """Send S2 startup message to authorized users."""
    s2_day = get_current_day()
    log_event("observation", "S2 came online. Bot initialized.")
    for chat_id in AUTHORIZED_CHAT_IDS:
        try:
            await application.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"S2 is online. Day {s2_day} of continuous operation.\n\n"
                    f"I'm running autonomously. Tell me what to create, or let me decide myself.\n\n"
                    f"Commands: /status /run /cancel /list /schedule /posted /history"
                )
            )
        except Exception as e:
            print(f"[BOT] Could not send startup message to {chat_id}: {e}")

def run_bot():
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env!")
        return
    if not AUTHORIZED_CHAT_IDS:
        print("[BOT] WARNING: TELEGRAM_ALLOWED_CHAT_IDS is not set. The bot will start but reject all commands, letting users know their Chat ID for configuration.")

    print("[BOT] Starting AI Agent Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("posted", cmd_posted))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("[BOT] Bot is live! Open Telegram and send /start")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_bot()
