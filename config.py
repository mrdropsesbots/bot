import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY не задан!")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

FREE_CHECKS_LIMIT = 3
FREE_PARTS_LIMIT = 1
PRO_PRICE = 9.99

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
