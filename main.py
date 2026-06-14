from src.security import init_security
init_security()

import os
import re
import sys
import json
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 stdout/stderr so emoji don't crash on Windows cp1252 terminals
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
import datetime
import time
import traceback
from pathlib import Path
from src.generator import (
    generate_curriculum,
    generate_lesson_content,
    text_to_speech,
    generate_visuals,
    create_video,
    YOUR_NAME
)
from src.uploader import upload_to_youtube
from src.s2_narrative import (
    get_current_day,
    get_narrative_context,
    log_event,
    advance_day,
    set_last_topic,
    generate_day_hook
)

CONTENT_PLAN_FILE = Path("content_plan.json")
OUTPUT_DIR = Path("output")
LESSONS_PER_RUN = 1

def get_content_plan():
    if not CONTENT_PLAN_FILE.exists():
        print("📄 content_plan.json not found. Generating new S2 Chronicles plan...")
        s2_day = get_current_day()
        new_plan = generate_curriculum(s2_day=s2_day)
        with open(CONTENT_PLAN_FILE, 'w') as f:
            json.dump(new_plan, f, indent=2)
        print(f"✅ New S2 curriculum saved to {CONTENT_PLAN_FILE}")
        return new_plan
    else:
        try:
            with open(CONTENT_PLAN_FILE, 'r') as f:
                plan = json.load(f)
            if not plan.get("lessons") or not isinstance(plan["lessons"], list):
                raise ValueError("⚠️ Invalid or empty lesson plan detected.")
            return plan
        except Exception as e:
            print(f"❌ ERROR loading existing plan: {e}. Regenerating...")
            s2_day = get_current_day()
            new_plan = generate_curriculum(s2_day=s2_day)
            with open(CONTENT_PLAN_FILE, 'w') as f:
                json.dump(new_plan, f, indent=2)
            return new_plan


def update_content_plan(plan):
    with open(CONTENT_PLAN_FILE, 'w') as f:
        json.dump(plan, f, indent=2)



def produce_lesson_videos(lesson):
    print(f"\n[PIPELINE] Starting S2 production for: '{lesson['title']}'")
    chapter_safe = re.sub(r'[^\w]', '_', str(lesson['chapter'])).strip('_')
    part_safe = re.sub(r'[^\w]', '_', str(lesson['part'])).strip('_')
    unique_id = f"{datetime.datetime.now().strftime('%Y%m%d')}_{chapter_safe}_{part_safe}"
    video_format = os.environ.get("CUSTOM_FORMAT", "both").lower()

    # S2 Chronicle context
    s2_day = get_current_day()
    narrative_ctx = get_narrative_context()
    log_event("experiment", f"Starting production: {lesson['title']}")

    # Track all temp files for cleanup on failure
    temp_audio_files = []

    # S2 is always S2 — no random name selection
    speaker_name_map = {"Agent": "S2", "Human": "Human"}
    print(f"[PIPELINE] S2 Chronicle Day {s2_day} — producing episode: {lesson['title']}")

    try:
        lesson_content = generate_lesson_content(
            lesson['title'],
            video_format=video_format,
            target_duration=os.environ.get("CUSTOM_DURATION", ""),
            s2_day=s2_day,
            narrative_context=narrative_ctx
        )
        emotion = lesson_content.get('emotion', 'curious')

        long_video_id = None
        if video_format in ["both", "long"]:
            print("\n--- Producing S2 Long-Form Episode ---")

            dialogue = lesson_content.get('dialogue', [])

            # Rely purely on the LLM's organically generated script
            # because the LLM prompt already enforces a Day X hook and a strong payoff.
            full_dialogue = dialogue

            voice_mode = os.environ.get("CUSTOM_VOICES", "dual").lower()
            long_voice_mode = "single" if video_format == "long" and voice_mode != "single" else voice_mode

            dialogue_audio_paths = []

            # Single-pass audio for long videos: concatenate all text first,
            # then synthesize in one continuous call for consistent voice.
            # For short dual-voice, keep per-line synthesis.
            if long_voice_mode == "single" and len(full_dialogue) > 1:
                print(f"[AUDIO] Single-pass audio mode: combining {len(full_dialogue)} lines into one synthesis call.")
                # Concatenate all text with natural transition pauses embedded
                combined_text = " ".join(line["text"] for line in full_dialogue)
                audio_path = OUTPUT_DIR / f"audio_dialogue_full_{unique_id}.mp3"
                wav_path = text_to_speech(
                    combined_text,
                    audio_path,
                    emotion=emotion,
                    speaker="Agent",
                    voice_mode="single"
                )
                dialogue_audio_paths = [wav_path]
                temp_audio_files.append(wav_path)
            else:
                for i, line in enumerate(full_dialogue):
                    audio_path = OUTPUT_DIR / f"audio_dialogue_{i+1}_{unique_id}.mp3"
                    wav_path = text_to_speech(
                        line["text"],
                        audio_path,
                        emotion=line.get("emotion", "neutral"),
                        speaker=line.get("speaker", "Agent"),
                        voice_mode=long_voice_mode
                    )
                    dialogue_audio_paths.append(wav_path)
                    temp_audio_files.append(wav_path)
            
            # If single-pass: align dialogue segments for subtitle sync
            print(f"[AUDIO] Total dialogue audio files: {len(dialogue_audio_paths)}")

            long_video_path = OUTPUT_DIR / f"long_video_{unique_id}.mp4"
            print(f"[VIDEO] Creating S2 long-form episode: {long_video_path}")
            create_video(full_dialogue, dialogue_audio_paths, long_video_path, 'long', title=lesson['title'], speaker_name_map=speaker_name_map)

            long_thumb_path = generate_visuals(
                output_dir=OUTPUT_DIR,
                video_type='long',
                thumbnail_title=f"S2 — {lesson['title']}"
            )
        else:
            long_thumb_path = None
            long_video_path = None

        short_video_path = None
        short_thumb_path = None
        short_audio_path = None

        if video_format in ["both", "short"]:
            print("\n--- Producing S2 Short Episode ---")
            short_dialogue = lesson_content.get('dialogue', [])
            print(f"[SHORT] Using {len(short_dialogue)} dialogue exchanges from S2 script")

            # S2's short-form hook — direct, day-anchored
            import re as _re
            clean_topic = _re.sub(r'^(what is|what are|how does|how do)\s+', '', lesson['title'], flags=_re.IGNORECASE).strip()
            # Remove 'Day N:' prefix from topic if already present in title
            clean_topic = _re.sub(r'^Day\s+\d+[:\s]+', '', clean_topic, flags=_re.IGNORECASE).strip()
            full_short_dialogue = [
                {"speaker": "Agent", "text": f"Day {s2_day}. Something about {clean_topic} surprised me.", "emotion": "curious"},
                *short_dialogue,
                {"speaker": "Agent", "text": "Follow S2 for more days like this.", "emotion": "calm"}
            ]

            short_audio_paths = []
            short_voice_mode = os.environ.get("CUSTOM_VOICES", "dual").lower()
            
            if short_voice_mode == "single":
                print(f"[AUDIO] Generating monolithic audio for short video...")
                full_text = " ".join([line["text"] for line in full_short_dialogue])
                audio_path = OUTPUT_DIR / f"short_audio_dialogue_full_{unique_id}.mp3"
                wav_path = text_to_speech(full_text, audio_path, emotion=emotion, speaker="Agent")
                short_audio_paths.append(wav_path)
                temp_audio_files.append(wav_path)
            else:
                for i, line in enumerate(full_short_dialogue):
                    audio_path = OUTPUT_DIR / f"short_audio_dialogue_{i+1}_{unique_id}.mp3"
                    wav_path = text_to_speech(line["text"], audio_path, emotion=line.get("emotion", "neutral"), speaker=line.get("speaker", "Agent"))
                    short_audio_paths.append(wav_path)
                    temp_audio_files.append(wav_path)

            short_video_path = OUTPUT_DIR / f"short_video_{unique_id}.mp4"
            print(f"[VIDEO] Creating short conversational video at: {short_video_path}")
            create_video(full_short_dialogue, short_audio_paths, short_video_path, 'short', title=lesson['title'], speaker_name_map=speaker_name_map)

            short_thumb_path = generate_visuals(
                output_dir=OUTPUT_DIR,
                video_type='short',
                thumbnail_title=f"S2 — {lesson['title']}"
            )
        print("\n[PIPELINE] Preparing video metadata...")
        hashtags = lesson_content.get("hashtags", "#S2 #AutonomousAI #AIChronicle")

        if long_video_path:
            print(f"PREVIEW_LONG_PATH={long_video_path.resolve()}")
        if short_video_path:
            print(f"PREVIEW_SHORT_PATH={short_video_path.resolve()}")

        preview_mode = os.environ.get("PREVIEW_MODE", "false").lower() == "true"
        if preview_mode:
            print("\n[PIPELINE] PREVIEW MODE: Skipping YouTube upload.")
            print("PREVIEW_ONLY_NO_UPLOAD")
            return "PREVIEW_ONLY_NO_UPLOAD"
        

        if video_format in ["both", "long"]:
            long_desc = (
                f"S2 Chronicle | Day {s2_day}\n\n"
                f"Episode: {lesson['title']}\n\n"
                f"S2 is an autonomous AI agent sharing its experiences, discoveries, and decisions — one day at a time.\n\n"
                f"{hashtags}"
            )
            long_tags = f"S2,AutonomousAI,AIChronicle,{lesson['title'].replace(' ', ',')}"

            long_video_id = upload_to_youtube(
                long_video_path,
                lesson['title'],
                long_desc,
                long_tags,
                long_thumb_path
            )
        else:
            # Faux ID so it counts as completed if only short was requested
            long_video_id = "SHORT_ONLY_" + unique_id

        if long_video_id:
            if video_format in ["both", "short"] and short_video_path:
                if video_format == "both":
                    print("[PIPELINE] Waiting 30 seconds before uploading the short...")
                    time.sleep(30)
                highlight = (lesson_content.get('short_form_highlight') or '').strip()
                if not highlight:
                    highlight = f"S2 | Day {s2_day}: {lesson['title']}"
                short_title = f"{highlight[:90].rstrip()} #Shorts"
                short_desc = (
                    f"S2 Chronicle | Day {s2_day}\n\n"
                    f"{lesson_content['short_form_highlight']}\n\n"
                    f"Watch the full S2 episode here: https://www.youtube.com/watch?v={long_video_id}\n\n"
                    f"{hashtags}"
                )
                short_id = upload_to_youtube(
                    short_video_path,
                    short_title.strip(),
                    short_desc,
                    "S2,AutonomousAI,AIChronicle,Shorts",
                    short_thumb_path
                )
                # Advance S2's day after successful upload
                new_day = advance_day()
                log_event("launch", f"Short episode uploaded successfully. Now on Day {new_day}.")
                set_last_topic(lesson['title'])
                if video_format == "short":
                    return short_id
            else:
                # Advance day after long-form upload
                new_day = advance_day()
                log_event("launch", f"Long episode uploaded successfully. Now on Day {new_day}.")
                set_last_topic(lesson['title'])
            return long_video_id
        return None

    except Exception:
        # Clean up temp audio files on failure to prevent disk bloat
        print("[CLEANUP] Cleaning up temp audio files after failure...")
        for f in temp_audio_files:
            try:
                Path(f).unlink(missing_ok=True)
            except Exception:
                pass
        # Also clean up any orphan temp MP3 files
        for f in OUTPUT_DIR.glob("*_temp.mp3"):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
        raise  # Re-raise so main() can handle it



def main():
    print("[PIPELINE] Starting Autonomous AI Course Generator")
    print(f"[PIPELINE] Current working dir: {os.getcwd()}")
    print(f"[PIPELINE] OUTPUT_DIR: {OUTPUT_DIR.resolve()}")

    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        print(f"[PIPELINE] Created output folder: {OUTPUT_DIR.exists()}")
        plan = get_content_plan()
        custom_topic = os.environ.get("CUSTOM_TOPIC")
        if custom_topic:
            pending = [(0, {
                "chapter": "Custom",
                "part": "01",
                "title": custom_topic,
                "status": "pending"
            })]
            print(f"[PIPELINE] S2 custom episode: {custom_topic}")
        else:
            pending = [(i, lesson) for i, lesson in enumerate(plan['lessons']) if lesson['status'] == 'pending']

            if not pending:
                print("[PIPELINE] All S2 episodes produced! Generating new S2 Chronicles...")

                previous_titles = [lesson['title'] for lesson in plan['lessons']]
                s2_day = get_current_day()
                new_plan = generate_curriculum(previous_titles=previous_titles, s2_day=s2_day)
                update_content_plan(new_plan)
                plan = new_plan
                pending = [(i, lesson) for i, lesson in enumerate(new_plan['lessons']) if lesson['status'] == 'pending']
                if not pending:
                    print("[WARN] Curriculum generated but no valid episodes found.")
                    return

        failed_lessons = []
        for lesson_index, lesson in pending[:LESSONS_PER_RUN]:
            try:
                video_id = produce_lesson_videos(lesson)
                if video_id:
                    for original_lesson in plan['lessons']:
                        if original_lesson['title'].strip().lower() == lesson['title'].strip().lower():
                            original_lesson['status'] = 'complete'
                            original_lesson['youtube_id'] = video_id
                            print(f"[OK] Completed lesson: {lesson['title']}")
                            break
                    else:
                        lesson['status'] = 'complete'
                        lesson['youtube_id'] = video_id
                        plan['lessons'].append(lesson)
                        print(f"[OK] Appended custom lesson to plan as complete: {lesson['title']}")
                else:
                    print(f"[ERROR] Upload failed (no video ID returned): {lesson['title']}")
                    failed_lessons.append(lesson['title'])
            except Exception as e:
                print(f"[ERROR] Failed producing lesson: {lesson['title']}")
                traceback.print_exc()
                failed_lessons.append(lesson['title'])
            finally:
                update_content_plan(plan)
                print("[PIPELINE] Content plan saved.")

        if failed_lessons:
            print(f"\n[PIPELINE FAILED] {len(failed_lessons)} lesson(s) did not complete:")
            for title in failed_lessons:
                print(f"   - {title}")
            sys.exit(1)

    except Exception as e:
        print("[CRITICAL] Critical error in main()")
        traceback.print_exc()
        sys.exit(1)

    # Cleanup is now handled centrally by the telegram bot's interactive prompts

if __name__ == "__main__":
    main()
