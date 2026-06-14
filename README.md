<h1 align="center">🤖 S2: Autonomous 24/7 YouTube Agent</h1>

<p align="center">
  <b>A fully autonomous, intelligent digital production studio that writes, narrates, edits, and uploads YouTube videos continuously, controlled entirely via Telegram.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11-blue?logo=python&logoColor=white" alt="Python 3.11">
  <img src="https://img.shields.io/badge/powered%20by-LLMs%20%2B%20Edge%20TTS-orange" alt="LLMs & TTS">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

<p align="center">
  <a href="https://www.youtube.com/@Im-S2-AI">
    <img src="https://img.shields.io/badge/▶%20Watch%20Generated%20Videos%20on%20YouTube-red?logo=youtube&logoColor=white&style=for-the-badge" alt="Watch on YouTube">
  </a>
</p>

---

## What is S2?

S2 is a fully autonomous 24/7 YouTube automation agent. 

It acts as a complete, self-contained digital production studio. It automatically writes scripts, generates high-quality cinematic voiceovers, creates minimal visual aesthetics, and uploads them directly to YouTube. It can run continuously without human intervention, maintaining a daily upload schedule entirely on its own.

---

## ✨ Core Features & Capabilities

### 📱 Interactive Telegram Control Hub
S2 is actively controlled and monitored via a live **Telegram Bot**. You don't need to interact with the terminal; you act as the "Admin" through the chat interface.
- **Natural Language Chat:** Talk to the agent naturally. It uses advanced LLMs to interpret your intent, chat with you, and remember conversation context.
- **Queue Management:** Add, remove, or modify topics in the daily content plan directly from your phone.
- **Custom Scripts on Demand:** Send complete stories, lessons, or custom scripts in chat. The agent will gladly accept them as "data modules" and produce them for views without hallucinating new text.
- **Instant Generation:** Ask the agent to generate the next episode, or generate custom video formats (shorts, long-form, or both) instantly.
- **YouTube Management:** Delete videos, update titles, list recent uploads, or change video visibility (public/private/unlisted) directly from Telegram.

### 🎬 Advanced Video Rendering Pipeline
The internal rendering engine has been overhauled for a premium, highly engaging cinematic aesthetic:
- **Edge-TTS (AvaNeural):** Utilizes highly realistic, emotive voice models that include natural human pacing, breathing, and pauses.
- **Dynamic Cinematic Subtitles:** Auto-generates center-aligned, drop-shadow typography that highlights the exact spoken word synchronously, creating an intense, documentary-style focus.
- **Dual-Format Generation:** Capable of automatically generating both Long-form (16:9 widescreen) and YouTube Shorts (9:16 vertical) from the same script.
- **Aesthetic Visuals:** Automatically injects visual glitches, static noise transitions, waveform audio visualizations, and dynamic backgrounds.
- **Smart Thumbnails:** Generates and attaches custom thumbnails to every uploaded video.

### 🧠 Intelligent Autonomous Brain
- **Self-Healing API Calls:** The LLM integration features robust exponential backoff and retry logic, meaning temporary network drops or 504 Gateway Timeouts will not crash the bot.
- **State Memory:** Maintains a persistent memory of its upload history in `s2_chronicle.json` and pulls systematically from an expansive curriculum in `content_plan.json`.
- **Flexible Storytelling:** Capable of pivoting from educational tutorials to narrative storytelling based on your Telegram commands.

---

## 🚀 Running the Bot Invisibly

S2 runs locally on your Windows machine. To prevent annoying terminal windows from cluttering your workspace, it includes dedicated stealth launcher scripts:

### `START_HIDDEN.bat` (Recommended)
Double-click this to launch S2 **completely invisibly** in the background. A terminal will flash for a split second and disappear. S2 will stay online and listen to your Telegram commands silently, allowing you to close your IDE and work normally.

### `STOP_HIDDEN.bat`
Double-click this to gracefully and forcefully terminate S2's hidden background python processes without affecting any other Python projects on your machine.

### `START_BOT.bat`
Double-click this if you *want* to keep a visible terminal window open to monitor live console logs and debug outputs.

---

## ⚙️ Setup & Requirements

### 1. Clone & Install Dependencies
```bash
git clone <your-repo-url>
cd Youtube-automation-main
pip install -r requirements.txt
```

### 2. Environment Variables (`.env`)
Create a `.env` file in the root directory containing your API keys:
```env
TELEGRAM_TOKEN=your_telegram_bot_token
OPENAI_API_KEY=your_llm_api_key  # Can be OpenAI, NVIDIA NIM, Moonshot, etc.
```

### 3. YouTube Authentication
Place your Google Cloud `client_secrets.json` file in the root directory. On the very first run, the script will open a browser window asking you to authenticate with your YouTube account. It will then generate a local `credentials.json` token file so the bot can upload videos automatically going forward.

### 4. Launch!
Double-click `START_HIDDEN.bat` and send a `/start` message to your bot on Telegram!

---

## 📝 Architecture

- **`src/telegram_bot.py`**: The conversational AI interface and command router.
- **`src/generator.py`**: The creative engine (LLM script writing, TTS audio generation, MoviePy rendering, Subtitle synchronization).
- **`src/uploader.py`**: The YouTube Data API v3 integration for automated publishing.
- **`main.py`**: The core orchestration pipeline linking generation to uploading.

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
