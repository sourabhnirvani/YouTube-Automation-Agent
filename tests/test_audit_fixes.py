import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock

from src import telegram_bot
from src.generator import create_subtitle_clip
from src.uploader import upload_to_youtube


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, chat_id):
        self.effective_chat = FakeChat(chat_id)
        self.message = FakeMessage()


class TelegramSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_authorized = telegram_bot.AUTHORIZED_CHAT_IDS
        telegram_bot.AUTHORIZED_CHAT_IDS = {12345}

    def tearDown(self):
        telegram_bot.AUTHORIZED_CHAT_IDS = self.original_authorized

    def test_authorized_chat_parser_ignores_invalid_entries(self):
        parsed = telegram_bot._parse_authorized_chat_ids("123, bad, -456, ,789")
        self.assertEqual(parsed, {123, -456, 789})

    async def test_execute_action_rejects_unauthorized_chat(self):
        update = FakeUpdate(chat_id=999)

        result = await telegram_bot._execute_action({"action": "status"}, update, object())

        self.assertEqual(result, "Unauthorized chat.")

    async def test_bulk_delete_disabled_by_default(self):
        update = FakeUpdate(chat_id=12345)

        with mock.patch.dict(os.environ, {"TELEGRAM_ENABLE_BULK_DELETE": "false"}, clear=False):
            result = await telegram_bot._execute_action({"action": "delete_all"}, update, object())

        self.assertIn("Bulk delete is disabled", result)

    def test_multiple_delete_actions_do_not_escalate_to_delete_all(self):
        fake_response = mock.Mock()
        fake_response.choices = [
            mock.Mock(
                message=mock.Mock(
                    content='{"action": "delete_video", "video_id": "a"} {"action": "delete_video", "video_id": "b"}'
                )
            )
        ]
        fake_client = mock.Mock()
        fake_client.chat.completions.create.return_value = fake_response

        with mock.patch.object(telegram_bot, "_get_kimi", return_value=fake_client), \
             mock.patch.object(telegram_bot, "fetch_trending_topics", return_value=[]):
            action = telegram_bot._call_agent("delete two videos", {"lessons": []}, [])

        self.assertEqual(action, {"action": "delete_video", "video_id": "a"})

    async def test_gameplay_slug_sanitization(self):
        update = FakeUpdate(chat_id=12345)
        action = {
            "action": "generate_custom",
            "gameplay": "../../malicious_slug-name"
        }
        with mock.patch("pathlib.Path.mkdir") as mock_mkdir, \
             mock.patch("pathlib.Path.glob", return_value=[]) as mock_glob, \
             mock.patch("threading.Thread.start") as mock_thread_start:
            
            result = await telegram_bot._execute_action(action, update, mock.Mock())
            # The folder path in instructions should be sanitized to malicious_slug_name
            self.assertIn("malicious_slug_name", result)
            self.assertNotIn("..", result.split("into:\n")[1])



class MediaCleanupTests(unittest.TestCase):
    def test_create_subtitle_clip_uses_trackable_temp_file(self):
        clip = create_subtitle_clip("Agent", "Short subtitle", 640, 24, "yellow", 0.1)
        temp_path = Path(clip.temp_image_path)
        try:
            self.assertTrue(temp_path.exists())
        finally:
            clip.close()
            temp_path.unlink(missing_ok=True)

    def test_make_kenburns_clip_lazy_evaluation(self):
        from src.generator import _make_kenburns_clip
        from PIL import Image
        import numpy as np

        pil_image = Image.new('RGB', (100, 100), color='blue')
        clip = _make_kenburns_clip(pil_image, duration=2.0, width=100, height=100)
        try:
            self.assertEqual(clip.duration, 2.0)
            frame = clip.get_frame(1.0)
            self.assertIsInstance(frame, np.ndarray)
            self.assertEqual(frame.shape, (100, 100, 3))
        finally:
            clip.close()

    def test_generate_lesson_content_monologue_prompt(self):
        from src.generator import generate_lesson_content
        with mock.patch("src.generator._call_llm_for_json") as mock_llm_call:
            mock_llm_call.return_value = {
                "short_form_highlight": "Test hook",
                "dialogue": [{"speaker": "Agent", "text": "Test speech", "emotion": "neutral"}],
                "hashtags": "#test"
            }
            with mock.patch.dict(os.environ, {"CUSTOM_VOICES": "single"}):
                res = generate_lesson_content("Test Title", video_format="short")
                
            args, kwargs = mock_llm_call.call_args
            prompt_content = args[0]
            self.assertIn("monologue", prompt_content.lower())
            self.assertIn("where ALL speakers are \"Agent\"", prompt_content)

    def test_create_video_uses_speaker_name_mapping(self):
        from src.generator import create_video
        from unittest.mock import MagicMock
        
        dialogue = [
            {"speaker": "Agent", "text": "Hello, I am Sophia."},
            {"speaker": "Student", "text": "Hi, I am Ethan."}
        ]
        
        speaker_name_map = {"Agent": "Sophia", "Student": "Ethan"}
        
        with mock.patch("src.generator.AudioFileClip") as mock_audio, \
             mock.patch("src.generator.concatenate_audioclips") as mock_concat_audio, \
             mock.patch("src.generator.create_subtitle_clip") as mock_sub, \
             mock.patch("src.generator._make_kenburns_clip") as mock_kb, \
             mock.patch("src.generator._resize_clip_to_fill") as mock_resize:
            
            mock_audio_inst = MagicMock()
            mock_audio_inst.duration = 1.0
            mock_audio_inst.end = 1.0
            mock_audio_inst.nchannels = 2
            mock_audio_inst.volumex.return_value = mock_audio_inst
            mock_audio_inst.fx.return_value = mock_audio_inst
            mock_audio.return_value = mock_audio_inst
            
            mock_concat_inst = MagicMock()
            mock_concat_inst.duration = 2.0
            mock_concat_inst.end = 2.0
            mock_concat_inst.nchannels = 2
            mock_concat_inst.volumex.return_value = mock_concat_inst
            mock_concat_audio.return_value = mock_concat_inst
            
            mock_kb_clip = MagicMock()
            mock_kb_clip.duration = 1.0
            mock_kb_clip.fps = 24
            mock_kb_clip.size = (100, 100)
            mock_kb_clip.end = 1.0
            mock_kb_clip.audio = None
            mock_kb_clip.mask = None
            mock_kb.return_value = mock_kb_clip
            mock_resize.return_value = mock_kb_clip
            
            mock_sub_clip = MagicMock()
            mock_sub_clip.duration = 1.0
            mock_sub_clip.fps = 24
            mock_sub_clip.size = (100, 100)
            mock_sub_clip.end = 1.0
            mock_sub_clip.audio = None
            mock_sub_clip.mask = None
            
            mock_positioned_sub = MagicMock()
            mock_positioned_sub.duration = 1.0
            mock_positioned_sub.fps = 24
            mock_positioned_sub.size = (100, 100)
            mock_positioned_sub.end = 1.0
            mock_positioned_sub.audio = None
            mock_positioned_sub.mask = None
            
            mock_mask = MagicMock()
            mock_mask.fps = 24
            mock_mask.size = (100, 100)
            mock_mask.end = 1.0
            mock_mask.audio = None
            mock_mask.mask = None
            mock_mask.set_position.return_value = mock_mask
            mock_mask.set_end.return_value = mock_mask
            mock_mask.set_start.return_value = mock_mask
            
            mock_kb_clip.add_mask.return_value.mask = mock_mask
            mock_positioned_sub.add_mask.return_value.mask = mock_mask
            
            mock_sub_clip.set_position.return_value = mock_positioned_sub
            mock_sub.return_value = mock_sub_clip
            
            with mock.patch("moviepy.video.VideoClip.VideoClip.write_videofile") as mock_write, \
                 mock.patch("moviepy.editor.concatenate_videoclips") as mock_concat_video:
                 
                try:
                    create_video(
                        dialogue=dialogue,
                        audio_paths=["path1.wav", "path2.wav"],
                        output_path="out.mp4",
                        video_type="short",
                        title="test",
                        speaker_name_map=speaker_name_map
                    )
                except Exception:
                    pass
                
                mock_sub.assert_any_call("Sophia", "Hello, I am Sophia.", mock.ANY, mock.ANY, mock.ANY, mock.ANY)
                mock_sub.assert_any_call("Ethan", "Hi, I am Ethan.", mock.ANY, mock.ANY, mock.ANY, mock.ANY)


class UploaderValidationTests(unittest.TestCase):
    def test_upload_rejects_missing_video_before_authentication(self):
        with mock.patch("src.uploader.get_authenticated_service") as auth:
            with self.assertRaises(FileNotFoundError):
                upload_to_youtube("missing-video-file.mp4", "title", "description", "tag")
            auth.assert_not_called()


if __name__ == "__main__":
    unittest.main()
