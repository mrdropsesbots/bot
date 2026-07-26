import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Безопасное получение списка админов
admin_ids_str = os.getenv("ADMIN_IDS", "")
if admin_ids_str.strip():
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
else:
    ADMIN_IDS = []