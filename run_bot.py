"""
run_bot.py — Start the Telegram Bot
Run this in a separate terminal window while main.py handles video production.

Usage:
  python run_bot.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from src.security import init_security
init_security()

from dotenv import load_dotenv
load_dotenv()

from src.telegram_bot import run_bot

if __name__ == "__main__":
    run_bot()
