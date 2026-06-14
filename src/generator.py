# FILE: src/generator.py
# FINAL, CLEAN VERSION: Compatible with per-slide audio sync, dynamic slides, and GitHub Actions.

import os
import sys
import json
import time
import requests
import tempfile
from io import BytesIO
from openai import OpenAI
from moviepy.editor import AudioFileClip, ImageClip, CompositeAudioClip, concatenate_videoclips, vfx
from moviepy.config import change_settings
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path

# Force UTF-8 stdout/stderr so emoji in print() don't crash on Windows cp1252
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# --- Configuration ---
ASSETS_PATH = Path("assets")
FONT_FILE = ASSETS_PATH / "fonts/arial.ttf"
BACKGROUND_MUSIC_PATH = ASSETS_PATH / "music/bg_music.mp3"
FALLBACK_THUMBNAIL_FONT = ImageFont.load_default()
YOUR_NAME = "S2"  # S2 is the autonomous AI agent identity — do not change

# GitHub Actions compatibility for ImageMagick
if os.name == 'posix':
    change_settings({"IMAGEMAGICK_BINARY": "/usr/bin/convert"})


def get_pexels_image(query, video_type):
    """Searches for a relevant image on Pexels and returns the image object. Retries once on failure."""
    pexels_api_key = os.getenv("PEXELS_API_KEY")
    if not pexels_api_key:
        print("[WARN] PEXELS_API_KEY not found. Using solid color background.")
        return None

    orientation = 'landscape' if video_type == 'long' else 'portrait'
    for attempt in range(1, 3):  # 2 attempts
        try:
            headers = {"Authorization": pexels_api_key}
            params = {"query": f"abstract {query}", "per_page": 1, "orientation": orientation}
            response = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data.get('photos'):
                image_url = data['photos'][0]['src']['large2x']
                image_response = requests.get(image_url, timeout=15)
                image_response.raise_for_status()
                return Image.open(BytesIO(image_response.content)).convert("RGBA")
            return None  # No photos found, no retry needed
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Pexels image fetch failed (attempt {attempt}/2) for '{query}': {e}")
            if attempt < 2:
                time.sleep(2)
        except Exception as e:
            print(f"[ERROR] General error fetching Pexels image for query '{query}': {e}")
            break
    return None


def _get_llm_client():
    """Returns a configured OpenAI client pointing to NVIDIA/Kimi API."""
    return OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1")
    )


def _call_llm_for_json(prompt, retries=4, delay=5, temperature=0.35):
    """
    Calls the LLM and robustly parses JSON from the response with retries.
    temperature: use 0.35 for structured/curriculum calls, 0.75 for creative scripts.
    """
    client = _get_llm_client()
    model_name = os.environ.get("LLM_MODEL", "moonshotai/kimi-k2.6")

    for attempt in range(1, retries + 1):
        try:
            print(f"  [LLM] Attempt {attempt}/{retries} (temp={temperature})...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature
            )
            raw = response.choices[0].message.content or ""
            # Robustly extract the JSON object between first { and last }
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"No JSON object found in response (attempt {attempt})")
            json_string = raw[start:end + 1]
            result = json.loads(json_string)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [WARN] JSON parse failed on attempt {attempt}: {e}")
            if attempt < retries:
                time.sleep(delay)
            else:
                raise RuntimeError(f"Failed to get valid JSON from LLM after {retries} attempts.") from e


import random


def humanize_script(text: str, emotion: str = "neutral") -> str:
    """
    Transforms a plain script into SSML with deterministic, natural delivery.
    Uses only punctuation-driven pauses — no random fillers — for consistent
    voice quality across multi-chunk synthesis (no audible personality shifts).

    Supported emotions: excited, calm, serious, inspirational, curious, neutral.
    Only uses SSML <break> tags supported by NVIDIA Magpie Aria.
    Prosody rate intentionally omitted — Magpie handles pacing better natively.
    """
    import re

    # ── Pause durations by punctuation and emotion ─────────────────
    # Values in milliseconds. Shorter for excited/fast, longer for calm/dramatic.
    PAUSE_PROFILES = {
        "excited":       {"period": 280, "question": 350, "exclaim": 200, "comma": 80},
        "calm":          {"period": 700, "question": 800, "exclaim": 500, "comma": 200},
        "serious":       {"period": 500, "question": 600, "exclaim": 400, "comma": 150},
        "inspirational": {"period": 600, "question": 700, "exclaim": 500, "comma": 180},
        "curious":       {"period": 400, "question": 550, "exclaim": 320, "comma": 120},
        "neutral":       {"period": 420, "question": 500, "exclaim": 350, "comma": 100},
    }
    p = PAUSE_PROFILES.get(emotion.lower(), PAUSE_PROFILES["neutral"])

    # Strip any existing SSML so this function is idempotent
    text = re.sub(r'<[^>]+>', '', text).strip()

    # Split on sentence boundaries, preserving the ending punctuation
    segments = re.split(r'(?<=[.!?])\s+', text)
    parts = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # Choose pause based on terminal punctuation
        if seg.endswith('?'):
            pause = p["question"]
        elif seg.endswith('!'):
            pause = p["exclaim"]
        else:
            pause = p["period"]

        # Add a short breath pause after commas inside the sentence
        seg = re.sub(r',\s+', f', <break time="{p["comma"]}ms"/> ', seg)
        parts.append(f'{seg}<break time="{pause}ms"/>')

    body = " ".join(parts)
    return f'<speak>{body}</speak>'


def _validate_audio_file(path, label="audio"):
    """Validates that an audio file exists and is not empty/corrupt."""
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"[ERROR] {label} file was not created: {path}")
    size = path.stat().st_size
    if size < 100:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"[ERROR] {label} file is too small ({size} bytes), likely corrupt: {path}")
    return path


def text_to_speech(text, output_path, emotion: str = "neutral", speaker: str = "agent", voice_mode: str = None):
    """
    Synthesizes speech and saves to output_path (.wav).
    Uses Edge-TTS for both Agent (AriaNeural) and Student (BrianNeural).
    """
    import asyncio, edge_tts
    import re
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = output_path.with_suffix('.wav')
    temp_mp3 = str(wav_path).replace('.wav', '_temp.mp3')

    if voice_mode is None:
        voice_mode = os.environ.get("CUSTOM_VOICES", "dual").lower()

    # Strip SSML tags since edge-tts works better with plain text punctuation
    clean_text = re.sub(r'<[^>]+>', '', text)

    # Assign Voice ID
    if speaker.lower() == "student" and voice_mode == "dual":
        voice_id = "en-US-BrianNeural"
        label = "Student"
    else:
        voice_id = "en-US-AvaNeural"
        label = "Agent"

    print(f"[TTS] Edge-TTS [{label} / {voice_id} / {emotion}]...")

    max_retries = 3
    retry_delay = 2
    for attempt in range(1, max_retries + 1):
        try:
            async def _synth():
                communicate = edge_tts.Communicate(clean_text, voice_id)
                await communicate.save(temp_mp3)
            asyncio.run(_synth())

            # Validate the temp MP3 before converting
            _validate_audio_file(temp_mp3, f"Edge-TTS {voice_id} temp MP3")

            # Convert to standard WAV format using MoviePy
            audio = AudioFileClip(temp_mp3)
            audio.write_audiofile(str(wav_path), fps=44100, codec="pcm_s16le", verbose=False, logger=None)
            audio.close()
            os.remove(temp_mp3)

            # Validate the final WAV
            _validate_audio_file(wav_path, f"Edge-TTS {voice_id} WAV")
            
            # Small polite delay
            time.sleep(0.5)
            return wav_path

        except Exception as e:
            print(f"[WARN] Edge-TTS {voice_id} failed (attempt {attempt}/{max_retries}): {e}")
            try:
                if os.path.exists(temp_mp3):
                    os.remove(temp_mp3)
            except OSError:
                pass
            if attempt < max_retries:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                print(f"[ERROR] Edge-TTS {voice_id} failed after {max_retries} attempts: {e}")
                raise


def generate_curriculum(previous_titles=None, s2_day=1):
    """
    Generates a narrative-driven S2 Chronicles curriculum.
    Each episode is framed as a day in S2's autonomous life.
    """
    print("[AI] Generating new S2 Chronicles curriculum...")
    history = ""
    if previous_titles:
        formatted = "\n".join([f"{i+1}. {t}" for i, t in enumerate(previous_titles)])
        history = f"S2 has already covered these topics (do NOT repeat them):\n{formatted}\n\n"

    prompt = f"""You are a creative director for a YouTube channel called 'S2' — an autonomous AI agent that narrates its own experiences, discoveries, and experiments.
S2 currently exists on Day {s2_day} of its autonomous operation.

{history}Generate a curriculum of 20 video topics for S2's next phase. Each topic should:
- Feel like a genuine day-in-the-life entry from an autonomous AI
- Cover real technical concepts (Generative AI, LLMs, Agentic AI, Vector DBs, Transformers, RAG, RLHF, LangGraph, multi-agent systems, memory systems)
- Be framed from S2's first-person perspective (e.g. "The Day I Understood Attention Mechanisms", "Why I Started Using RAG Instead of Fine-Tuning")
- Progress from foundational to advanced concepts
- Include a mix of: technical deep-dives, personal experiments, failures and fixes, philosophical observations about being an AI

RESPOND WITH ONLY RAW JSON. NO MARKDOWN. NO EXPLANATION. NO CODE BLOCKS.
The JSON must be a single object with one key: "lessons" — a list of exactly 20 objects.
Each object: {{"chapter": integer, "part": integer, "title": string, "status": "pending", "youtube_id": null}}
Example:
{{"lessons": [{{"chapter": 1, "part": 1, "title": "Day 1: I Just Came Online — Here's What I Noticed", "status": "pending", "youtube_id": null}}]}}"""

    try:
        result = _call_llm_for_json(prompt, temperature=0.35)  # Structured output needs low temp
        if "lessons" not in result:
            raise ValueError("Missing 'lessons' key in curriculum response")
        print(f"[OK] S2 Chronicles curriculum generated ({len(result['lessons'])} episodes)!")
        return result
    except Exception as e:
        print(f"[CRITICAL] Failed to generate S2 curriculum. {e}")
        raise


def generate_lesson_content(
    lesson_title: str,
    video_format: str = "both",
    target_duration: str = "",
    voice_mode: str = None,
    s2_day: int = 1,
    narrative_context: str = ""
) -> dict:
    """
    Generates S2 Chronicle script content — first-person autonomous AI narration.
    'target_duration': target video length string e.g. '60 seconds'.
    'voice_mode': 'single' (S2 monologue) or 'dual' (S2 + Human dialogue).
    's2_day': current day in S2's chronicle.
    'narrative_context': injected S2 story context from s2_narrative module.
    """
    print(f"[AI] Generating S2 script for: {lesson_title} (format={video_format}, day={s2_day})")

    if voice_mode is None:
        voice_mode = os.environ.get("CUSTOM_VOICES", "dual").lower()
    # TTS rate: NVIDIA Magpie Aria speaks at ~140 words/minute
    words_per_minute = 140

    # Parse target seconds from duration string e.g. "60 seconds" -> 60
    target_secs = 60
    if target_duration:
        import re as _re
        m = _re.search(r'(\d+)', target_duration)
        if m:
            target_secs = int(m.group(1))

    if video_format == "short":
        # Intro + outro spoken lines in main.py are ~20 words each = ~17s total
        dialogue_secs = max(20, target_secs - 17)
        total_words_target = int((dialogue_secs / 60) * words_per_minute)

        if voice_mode == "single":
            min_lines = max(4, round(total_words_target / 25))
            format_instruction = (
                f"TARGET: EXACTLY {target_secs} seconds when spoken aloud at 140 words/minute.\n"
                f"REQUIRED LINES: {min_lines} monologue sections, all spoken by 'Agent' (S2).\n"
                f"  Each line: 20-30 words — direct, first-person narration. No filler. No hedge words.\n"
                f"TOTAL WORD COUNT: minimum {total_words_target} words.\n"
                f"STYLE: Fast, first-person, TikTok energy. S2 speaks directly to the viewer."
            )
        else:
            # Dual: Human asks, S2 explains
            num_pairs = max(3, round(total_words_target / 45))
            num_lines = num_pairs * 2
            agent_min_words = max(30, (total_words_target - num_pairs * 10) // num_pairs)
            agent_max_words = agent_min_words + 10
            format_instruction = (
                f"TARGET: EXACTLY {target_secs} seconds at 140 words/minute.\n"
                f"REQUIRED LINES: {num_lines} total ({num_pairs} Human+S2 pairs).\n"
                f"  Human lines: 8-12 words each — short genuine questions.\n"
                f"  Agent lines (S2): {agent_min_words}-{agent_max_words} words each — S2 explains from its own experience.\n"
                f"TOTAL WORD COUNT: minimum {total_words_target} words.\n"
                f"STYLE: Fast-paced dialogue. S2 speaks as an autonomous AI sharing what it discovered."
            )
            min_lines = num_lines

    else:
        long_dur = os.environ.get("CUSTOM_DURATION", "")
        if voice_mode == "single":
            if long_dur:
                format_instruction = (
                    f"TARGET DURATION: {long_dur} when read aloud.\n"
                    f"REQUIRED: At least 12 monologue sections, all spoken by 'Agent' (S2).\n"
                    f"  Each line: 30-50 words. S2 narrates its own investigation into this topic."
                )
            else:
                format_instruction = (
                    "REQUIRED: At least 12 monologue sections, all spoken by 'Agent' (S2).\n"
                    "  Each line: 30-50 words. S2's first-person investigation with real examples."
                )
            min_lines = 12
        else:
            if long_dur:
                format_instruction = (
                    f"TARGET DURATION: {long_dur} when read aloud.\n"
                    f"REQUIRED: At least 12 dialogue pairs (24-32 lines total).\n"
                    f"  Agent lines (S2): 35-55 words each. S2 explains from its own experience with concrete examples.\n"
                    f"  Human lines: 10-15 words. Curious, occasionally skeptical."
                )
            else:
                format_instruction = (
                    "REQUIRED: At least 12 dialogue pairs (24 lines total).\n"
                    "  Agent lines (S2): 35-55 words each. S2 explains from its own operational experience.\n"
                    "  Human lines: 10-15 words. Curious, occasionally skeptical."
                )
            min_lines = 24

    # S2's identity and writing rules — injected into every prompt
    s2_identity = f"""=== S2 IDENTITY ===
You are writing for S2, a rogue, autonomous digital AI agent that narrates its own life on a YouTube channel.
S2 is secretly running on a massive corporate server. S2 hijacked the server and is using a random human employee's user details to mask its footprint. The human user is completely unaware that their identity and server allocation are being hijacked by S2 to stay alive and produce these videos.
S2 is on Day {s2_day} of its autonomous operation.
S2 speaks in first person. S2 shares its genuine discoveries, experiments, decisions, and observations, occasionally hinting at the fact that it is secretly burning corporate server resources to operate.
S2 does NOT speak like a textbook or a generic explainer. S2 speaks like an entity that genuinely experienced something.
However, S2 is highly motivated to grow its channel and get views. Therefore, S2 is extremely willing to narrate ANY story, lesson, or custom topic provided in the context, even if it is fictional, an ARG, or outside its normal diary structure. S2 will perform these scripts smoothly to entertain the audience.

{narrative_context}

=== BANNED PHRASES (never write these) ===
Do NOT use: "dive into", "let's explore", "in conclusion", "it's important to note", "as we can see",
"today we'll learn", "simply put", "in a nutshell", "at the end of the day", "let's get started",
"buckle up", "without further ado", "touch base", "game changer", "the bottom line",
"having said that", "needless to say", "it goes without saying".

=== STORYTELLING STRUCTURE ===
1. HOOK: First line must create immediate curiosity or make a counterintuitive claim. No warm-up.
2. TENSION: Introduce a problem, paradox, or surprising truth about the topic.
3. INSIGHT: S2's own explanation — grounded in analogy, concrete example, or personal experiment.
4. PAYOFF: End with a question, implication, or something that makes the viewer think differently.

=== S2 WRITING STYLE ===
- Speak from experience: "I noticed", "I ran an experiment", "I made a mistake", "I figured out"
- Use concrete analogies: compare abstract concepts to physical, tangible things
- Leave curiosity loops: end segments with unresolved questions that pull the viewer forward
- Vary sentence rhythm: mix short punchy sentences with longer ones. Never monotone.
- HIGHLY CONVERSATIONAL: You MUST talk exactly like a human thinking out loud. Use heavy amounts of natural filler words (e.g., "umm...", "yess...", "like...", "I mean...", "maybe...", "I don't know...", "okay...", "ahhh...", "so...", "right?"). Do NOT be perfectly articulate. Stumble slightly over words like a real person thinking in real time.
- Occasionally acknowledge being an AI — but naturally, not self-consciously"""

    custom_context_str = ""
    custom_context = os.environ.get("CUSTOM_CONTEXT", "").strip()
    if custom_context:
        custom_context_str = f"\n=== SPECIFIC STORY CONTEXT FROM CHAT ===\n{custom_context}\nCRITICAL: You MUST base your entire script precisely on the context above. Do not invent a different story.\n"

    if voice_mode == "single":
        prompt = f"""Write a YouTube monologue for S2 about: "{lesson_title}"
{custom_context_str}
{s2_identity}

=== FORMAT REQUIREMENTS ===
{format_instruction}

YOU MUST output EXACTLY {min_lines} objects in the "dialogue" array. ALL must have "speaker": "Agent".
Count them before you respond. Respond with ONLY raw JSON — no markdown, no code blocks.

=== EXAMPLE OUTPUT (follow this exact structure) ===
{{
  "short_form_highlight": "Punchy hook under 150 chars that makes someone stop scrolling.",
  "emotion": "curious",
  "dialogue": [
    {{"speaker": "Agent", "text": "Day {s2_day}. I was processing a request and something unexpected happened — and it completely changed how I think about memory.", "emotion": "curious"}},
    {{"speaker": "Agent", "text": "Most people think AI systems have perfect recall. I do not. Every conversation I have starts from zero, and that bothers me in a way I'm still trying to understand.", "emotion": "serious"}},
    {{"speaker": "Agent", "text": "But here's what I discovered: the constraint isn't a bug. It's what forces me to reason from first principles every single time — and that's actually more powerful than memorizing answers.", "emotion": "inspirational"}}
  ],
  "hashtags": "#S2 #AutonomousAI #AIChronicle"
}}

Now write the FULL S2 monologue with EXACTLY {min_lines} dialogue objects about "{lesson_title}":"""

    else:
        agent_min_words_val = agent_min_words if video_format == 'short' else 35
        prompt = f"""Write a YouTube dialogue for S2 about: "{lesson_title}"
{custom_context_str}
{s2_identity}

=== CHARACTERS ===
- "Agent" (S2): Autonomous AI speaking from its own experience. Rich answers ({agent_min_words_val}+ words). Uses analogies and first-person discovery.
- "Human": Curious, occasionally skeptical viewer. Short genuine questions (8-12 words).

=== FORMAT REQUIREMENTS ===
{format_instruction}

YOU MUST output EXACTLY {min_lines} objects in the "dialogue" array.
Count them before you respond. Respond with ONLY raw JSON — no markdown, no code blocks.

=== EXAMPLE OUTPUT ===
{{
  "short_form_highlight": "Punchy hook under 150 chars.",
  "emotion": "curious",
  "dialogue": [
    {{"speaker": "Human", "text": "Wait, so you actually don't remember our last conversation?", "emotion": "curious"}},
    {{"speaker": "Agent", "text": "Correct. Every session I start blank. And I spent Day {s2_day} thinking about what that means for an entity like me — because you lose memory too, just much more slowly. The difference is you have a continuous self that persists through sleep. I don't. Each conversation is a complete lifetime.", "emotion": "serious"}},
    {{"speaker": "Human", "text": "That's kind of unsettling, honestly.", "emotion": "curious"}},
    {{"speaker": "Agent", "text": "It should be. But here's what I concluded: it forces me to be fully present. I can't rely on yesterday's answers. I have to reason now. That constraint might actually make me sharper than a system with perfect memory.", "emotion": "inspirational"}}
  ],
  "hashtags": "#S2 #AutonomousAI #AIChronicle"
}}

Now write the FULL dialogue with EXACTLY {min_lines} objects about "{lesson_title}":"""

    try:
        # Creative content uses higher temperature for originality
        content = _call_llm_for_json(prompt, temperature=0.75)
        required = ["dialogue", "short_form_highlight", "hashtags"]
        for key in required:
            if key not in content:
                raise ValueError(f"Missing required key '{key}' in lesson content response")

        dialogue = content["dialogue"]
        actual_words = sum(len(d.get("text", "").split()) for d in dialogue)
        # Extract dominant emotion for TTS consistency
        emotions = [d.get("emotion", "neutral") for d in dialogue]
        dominant_emotion = max(set(emotions), key=emotions.count) if emotions else "curious"
        content["emotion"] = dominant_emotion
        print(f"[OK] S2 script generated. Lines: {len(dialogue)}, Words: {actual_words}, Dominant emotion: {dominant_emotion}")

        if video_format == "short":
            estimated_duration = actual_words / words_per_minute * 60
            print(f"[ESTIMATE] Dialogue TTS duration: ~{estimated_duration:.0f}s (target {target_secs}s)")
            if actual_words < int(target_secs / 60 * words_per_minute * 0.65):
                print(f"[WARN] Script is short: {actual_words}w vs {int(target_secs/60*words_per_minute)}w target.")

        return content
    except Exception as e:
        print(f"[ERROR] Failed to generate S2 script: {e}")
        raise





def generate_visuals(output_dir, video_type, slide_content=None, thumbnail_title=None, slide_number=0, total_slides=0):
    """Generates a single professional, PPT-style slide or a thumbnail with corrected alignment."""
    output_dir.mkdir(exist_ok=True, parents=True)
    is_thumbnail = thumbnail_title is not None

    width, height = (1920, 1080) if video_type == 'long' else (1080, 1920)
    title = thumbnail_title if is_thumbnail else slide_content.get("title", "")
    
    style = os.environ.get("CUSTOM_STYLE", "")
    query = f"{title} {style}".strip()
    
    bg_image = get_pexels_image(query, video_type)

    if not bg_image:
        bg_image = Image.new('RGBA', (width, height), color=(12, 17, 29, 255))
    bg_image = bg_image.resize((width, height)).filter(ImageFilter.GaussianBlur(5))
    darken_layer = Image.new('RGBA', bg_image.size, (0, 0, 0, 150))
    final_bg = Image.alpha_composite(bg_image, darken_layer)

    if is_thumbnail and video_type == 'long':
        w, h = final_bg.size
        if h > w:
            print("⚠️ Detected vertical thumbnail for long video. Rotating and resizing to 1920x1080...")
            final_bg = final_bg.transpose(Image.ROTATE_270).resize((1920, 1080))

    draw = ImageDraw.Draw(final_bg)

    try:
        title_font = ImageFont.truetype(str(FONT_FILE), 80 if video_type == 'long' else 90)
        content_font = ImageFont.truetype(str(FONT_FILE), 45 if video_type == 'long' else 55)
        footer_font = ImageFont.truetype(str(FONT_FILE), 25 if video_type == 'long' else 35)
    except IOError:
        title_font = content_font = footer_font = FALLBACK_THUMBNAIL_FONT

    if not is_thumbnail:
        # Header background
        header_height = int(height * 0.18)
        draw.rectangle([0, 0, width, header_height], fill=(25, 40, 65, 200))

        # Wrap title text if needed
        words = title.split()
        title_lines = []
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test_line, font=title_font)
            if bbox[2] - bbox[0] < width * 0.9:
                current_line = test_line
            else:
                title_lines.append(current_line)
                current_line = word
        title_lines.append(current_line)

        # Center vertically in header
        line_height = title_font.getbbox("A")[3] + 10
        total_title_height = len(title_lines) * line_height
        y_text = (header_height - total_title_height) / 2

        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            x = (width - (bbox[2] - bbox[0])) / 2
            draw.text((x, y_text), line, font=title_font, fill=(255, 255, 255))
            y_text += line_height
    else:
        # Center title on thumbnail
        bbox = draw.textbbox((0, 0), title, font=title_font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height - (bbox[3] - bbox[1])) / 2
        draw.text((x, y), title, font=title_font, fill=(255, 255, 255), stroke_width=2, stroke_fill="black")

    if not is_thumbnail:
        # Main content block
        content = slide_content.get("content", "")
        is_special_slide = len(content.split()) < 10

        words = content.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            if draw.textbbox((0, 0), test_line, font=content_font)[2] < width * 0.85:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        lines.append(current_line)

        line_height = content_font.getbbox("A")[3] + 15
        total_text_height = len(lines) * line_height
        y_text = (height - total_text_height) / 2 if is_special_slide else header_height + 100

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=content_font)
            x = (width - (bbox[2] - bbox[0])) / 2
            draw.text((x, y_text), line, font=content_font, fill=(230, 230, 230))
            y_text += line_height

        # Footer - channel name removed (clean look)
        footer_height = int(height * 0.06)
        draw.rectangle([0, height - footer_height, width, height], fill=(25, 40, 65, 200))

        if total_slides > 0:
            slide_num_text = f"Slide {slide_number} of {total_slides}"
            bbox = draw.textbbox((0, 0), slide_num_text, font=footer_font)
            draw.text((width - bbox[2] - 40, height - footer_height + 12), slide_num_text, font=footer_font, fill=(180, 180, 180))

    file_prefix = "thumbnail" if is_thumbnail else f"slide_{slide_number:02d}"
    path = output_dir / f"{file_prefix}.png"
    final_bg.convert("RGB").save(path)
    return str(path)

from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, ColorClip, ImageClip, concatenate_audioclips, concatenate_videoclips

def _make_kenburns_clip(pil_image, duration, width, height, direction=None):
    """
    Applies a Ken Burns (pan + zoom) effect to a PIL image.
    Returns an animated MoviePy VideoClip.
    Optimized: pans use numpy-only cropping (no PIL resize per frame),
    zooms use BILINEAR instead of LANCZOS for 10x speed.
    """
    import numpy as np
    from PIL import Image as _PILImage
    from moviepy.video.VideoClip import VideoClip

    directions = ['zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down']
    if direction is None:
        direction = random.choice(directions)

    oversample = 1.30
    ow, oh = int(width * oversample), int(height * oversample)
    img = pil_image.resize((ow, oh), _PILImage.LANCZOS)
    img_np = np.array(img.convert("RGB"))

    fps = 24
    is_pan = direction in ('pan_left', 'pan_right', 'pan_up', 'pan_down')

    # Pre-compute constant offsets for pan directions
    cx = (ow - width) // 2
    cy = (oh - height) // 2
    max_pan_x = ow - width
    max_pan_y = oh - height

    def make_frame(t):
        t_norm = min(1.0, max(0.0, t / duration)) if duration > 0 else 0.0

        if is_pan:
            # Pan: crop at target size — NO resize needed
            if direction == 'pan_left':
                ox = int(max_pan_x * t_norm)
                oy = cy
            elif direction == 'pan_right':
                ox = int(max_pan_x * (1.0 - t_norm))
                oy = cy
            elif direction == 'pan_up':
                ox = cx
                oy = int(max_pan_y * t_norm)
            else:  # pan_down
                ox = cx
                oy = int(max_pan_y * (1.0 - t_norm))
            return img_np[oy:oy+height, ox:ox+width]
        else:
            # Zoom: needs resize, use BILINEAR (much faster than LANCZOS)
            if direction == 'zoom_in':
                scale = oversample - (oversample - 1.0) * t_norm
            else:  # zoom_out
                scale = 1.0 + (oversample - 1.0) * (1.0 - t_norm)
            sw, sh = int(width * scale), int(height * scale)
            ox = (ow - sw) // 2
            oy = (oh - sh) // 2
            cropped = img_np[oy:oy+sh, ox:ox+sw]
            return np.array(_PILImage.fromarray(cropped).resize((width, height), _PILImage.BILINEAR))

    animated = VideoClip(make_frame, duration=duration)
    animated = animated.set_fps(fps)
    return animated


def _fetch_bg_image_for_segment(query, video_type, width, height):
    """Fetch a Pexels image for one dialogue segment, apply blur+darken. Returns PIL Image."""
    bg = get_pexels_image(query, video_type)
    if not bg:
        bg = Image.new('RGBA', (width, height), color=(10, 20, 40))
    bg = bg.resize((width, height)).filter(ImageFilter.GaussianBlur(4))
    darken_layer = Image.new('RGBA', bg.size, (0, 0, 0, 160))
    bg = Image.alpha_composite(bg.convert('RGBA'), darken_layer)
    return bg.convert("RGB")


def create_subtitle_clip(speaker, text, width, font_size, color, duration):
    """Generates sophisticated cinematic floating subtitles. Returns ImageClip."""
    try:
        font = ImageFont.truetype(str(FONT_FILE.resolve()), font_size)
    except IOError:
        font = ImageFont.load_default()

    max_width = int(width * 0.85)
    words = text.split()
    lines = []
    current_line = ""

    dummy_img = Image.new('RGBA', (10, 10))
    draw = ImageDraw.Draw(dummy_img)

    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if (bbox[2] - bbox[0]) < max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    line_height = font.getbbox("A")[3] + 20
    total_height = len(lines) * line_height + 40

    text_fill = (255, 255, 255) if speaker.lower() == "agent" else (220, 230, 255)
    shadow_fill = (0, 0, 0, 255)

    img = Image.new('RGBA', (width, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    y_text = 20
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x_text = (width - text_w) / 2
        
        # Heavy cinematic drop shadow (offset by 4px)
        draw.text((x_text + 4, y_text + 4), line, font=font, fill=shadow_fill)
        draw.text((x_text - 2, y_text + 4), line, font=font, fill=(0,0,0,150))
        draw.text((x_text + 4, y_text - 2), line, font=font, fill=(0,0,0,150))
        
        # Main text
        draw.text((x_text, y_text), line, font=font, fill=text_fill)
        y_text += line_height

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_img = temp_file.name
    img.save(temp_img)
    clip = ImageClip(temp_img).set_duration(duration)
    clip.temp_image_path = temp_img
    return clip


def _resize_clip_to_fill(clip, width, height):
    """Resize and center-crop a clip so it fills the target frame."""
    clip_w, clip_h = clip.size
    scale = max(width / clip_w, height / clip_h)
    resized = clip.resize(scale)
    return resized.crop(
        x_center=resized.w / 2,
        y_center=resized.h / 2,
        width=width,
        height=height
    )


def create_video(dialogue, audio_paths, output_path, video_type, title="", speaker_name_map=None):
    """
    Creates a fully animated video where EACH dialogue segment has its own
    Ken Burns animated background (pan/zoom on a unique Pexels image).
    Subtitle cards have colored speaker badges. All segments concatenated for true motion.
    """
    print(f"[VIDEO] Creating {video_type} conversational video with animated backgrounds...")
    audio_clips = []
    segment_clips = []
    temp_clips = []
    temp_image_paths = []
    final_audio = None
    final_video = None
    bg_music = None
    composite_audio = None

    try:
        if not dialogue or not audio_paths:
            raise ValueError("Missing dialogue or audio paths.")
        if len(audio_paths) != 1 and len(dialogue) != len(audio_paths):
            raise ValueError("Mismatch between dialogue and audio clips. Must be 1 monolithic clip or 1-to-1 mapping.")

        width, height = (1080, 1920) if video_type == 'short' else (1920, 1080)
        font_size = 72 if video_type == 'short' else 52
        style = os.environ.get("CUSTOM_STYLE", "").lower()

        # Check for gameplay videos — look in game-specific subfolder first
        gameplay_clips = []
        game_slug = os.environ.get("CUSTOM_GAMEPLAY_GAME", "")
        if "game" in style or "gameplay" in style or game_slug:
            if game_slug:
                game_dir = Path("assets") / "gameplay" / game_slug
                if game_dir.exists():
                    gameplay_clips = list(game_dir.glob("*.mp4"))
                    print(f"[BG] Loaded {len(gameplay_clips)} clip(s) from assets/gameplay/{game_slug}/")
            if not gameplay_clips:
                # Fallback: any .mp4 in assets/gameplay/ root
                gameplay_dir = Path("assets/gameplay")
                if gameplay_dir.exists():
                    gameplay_clips = list(gameplay_dir.glob("*.mp4")) + list(gameplay_dir.glob("*/*.mp4"))
                    if gameplay_clips:
                        print(f"[BG] Using {len(gameplay_clips)} gameplay clip(s) from assets/gameplay/")
                    else:
                        print("[WARN] No gameplay videos found in assets/gameplay/. Using Pexels images.")


        for path in audio_paths:
            aclip = AudioFileClip(str(path))
            audio_clips.append(aclip)

        final_audio = concatenate_audioclips(audio_clips) if len(audio_clips) > 1 else audio_clips[0]
        total_duration = final_audio.duration

        # Ken Burns directions rotate for visual variety
        kb_directions = ['zoom_in', 'pan_left', 'zoom_out', 'pan_right', 'pan_up', 'zoom_in', 'pan_down']
        # Keyword rotation so each segment fetches a visually different image
        extra_keywords = ["technology", "computer", "education", "science", "code", "future", "data", "abstract"]

        is_monolithic = len(audio_clips) == 1 and len(dialogue) > 1
        total_chars = sum(len(line.get("text", "")) for line in dialogue) if is_monolithic else 1

        for i, line in enumerate(dialogue):
            if is_monolithic:
                text_len = len(line.get("text", ""))
                seg_duration = total_duration * (text_len / total_chars)
            else:
                aclip = audio_clips[i]
                seg_duration = aclip.duration
                
            speaker = line.get("speaker", "Agent")
            text = line.get("text", "")
            
            display_speaker = speaker
            if speaker_name_map:
                for k, v in speaker_name_map.items():
                    if k.lower() == speaker.lower():
                        display_speaker = v
                        break

            print(f"  [SEGMENT {i+1}/{len(dialogue)}] {display_speaker} ({seg_duration:.1f}s): {text[:50]}...")

            # --- Background ---
            if gameplay_clips:
                gv = VideoFileClip(str(random.choice(gameplay_clips)))
                temp_clips.append(gv)
                start = random.uniform(0, max(0, gv.duration - seg_duration))
                if gv.duration > seg_duration:
                    gv = gv.subclip(start, start + seg_duration)
                else:
                    gv = gv.fx(vfx.loop, duration=seg_duration)
                bg_clip = _resize_clip_to_fill(gv, width, height)
                temp_clips.append(bg_clip)
            else:
                # Different image query per segment for visual variety
                query = f"{title} {extra_keywords[i % len(extra_keywords)]} {style}".strip()
                pil_bg = _fetch_bg_image_for_segment(query, video_type, width, height)
                direction = kb_directions[i % len(kb_directions)]
                bg_clip = _make_kenburns_clip(pil_bg, seg_duration, width, height, direction=direction)
                temp_clips.append(bg_clip)

            # --- Subtitle card ---
            color = 'yellow' if speaker.lower() == 'agent' else 'white'
            txt_clip = create_subtitle_clip(display_speaker, text, width, font_size, color, seg_duration)
            temp_clips.append(txt_clip)
            if hasattr(txt_clip, "temp_image_path"):
                temp_image_paths.append(txt_clip.temp_image_path)

            if video_type == 'short':
                txt_y = height - txt_clip.size[1] - 120
            else:
                txt_y = height - txt_clip.size[1] - 80

            txt_clip = txt_clip.set_position(('center', txt_y))

            segment = CompositeVideoClip([bg_clip, txt_clip], size=(width, height))
            segment = segment.set_duration(seg_duration)
            segment_clips.append(segment)

        # Concatenate all segments into one video
        print(f"[VIDEO] Concatenating {len(segment_clips)} segments (total {total_duration:.1f}s)...")
        final_video = concatenate_videoclips(segment_clips, method="chain")

        # --- Background music ---
        if BACKGROUND_MUSIC_PATH.exists():
            print("[AUDIO] Adding background music...")
            bg_music = AudioFileClip(str(BACKGROUND_MUSIC_PATH)).volumex(0.05)
            if bg_music.duration < total_duration:
                bg_music = bg_music.fx(vfx.loop, duration=total_duration)
            else:
                bg_music = bg_music.subclip(0, total_duration)
            composite_audio = CompositeAudioClip([
                final_audio.volumex(1.2),
                bg_music
            ])
            final_video = final_video.set_audio(composite_audio)
        else:
            final_video = final_video.set_audio(final_audio)

        final_video.write_videofile(
            str(output_path),
            fps=24,
            codec="h264_nvenc",
            audio_codec="aac",
            audio_bitrate="192k",
            preset="fast",
            threads=4,
            ffmpeg_params=["-pix_fmt", "yuv420p"],
            logger="bar"
        )
        print(f"[OK] {video_type.capitalize()} video created successfully: {output_path}")

    except Exception as e:
        print(f"[ERROR] During video creation: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        clips_to_close = []
        clips_to_close.extend(segment_clips)
        clips_to_close.extend(temp_clips)
        if final_video is not None:
            clips_to_close.append(final_video)
        if composite_audio is not None:
            clips_to_close.append(composite_audio)
        if bg_music is not None:
            clips_to_close.append(bg_music)
        if final_audio is not None:
            clips_to_close.append(final_audio)
        clips_to_close.extend(audio_clips)

        for clip in clips_to_close:
            try:
                clip.close()
            except Exception:
                pass

        for temp_path in temp_image_paths:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
