from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    async def get_or_create_user(self, telegram_id: int, username: str = None):
        """Получить или создать пользователя"""
        try:
            # Проверяем существование
            result = self.client.table("users").select("*").eq("telegram_id", telegram_id).execute()
            
            if result.data:
                return result.data[0]
            
            # Создаем нового
            new_user = {
                "telegram_id": telegram_id,
                "username": username,
                "subscription_type": "free",
                "checks_used": 0,
                "checks_limit": 3,
                "parts_requests": 0,
                "parts_limit": 1,
                "created_at": datetime.utcnow().isoformat(),
                "last_vin_data": None
            }
            
            result = self.client.table("users").insert(new_user).execute()
            return result.data[0]
            
        except Exception as e:
            logger.error(f"DB Error get_or_create_user: {e}")
            return None
    
    async def increment_checks(self, telegram_id: int):
        """Увеличить счетчик проверок"""
        try:
            user = await self.get_or_create_user(telegram_id)
            new_count = user["checks_used"] + 1
            self.client.table("users").update({"checks_used": new_count}).eq("telegram_id", telegram_id).execute()
            return new_count
        except Exception as e:
            logger.error(f"DB Error increment_checks: {e}")
            return 0
    
    async def increment_parts(self, telegram_id: int):
        """Увеличить счетчик запросов запчастей"""
        try:
            user = await self.get_or_create_user(telegram_id)
            new_count = user["parts_requests"] + 1
            self.client.table("users").update({"parts_requests": new_count}).eq("telegram_id", telegram_id).execute()
            return new_count
        except Exception as e:
            logger.error(f"DB Error increment_parts: {e}")
            return 0
    
    async def save_vin_data(self, telegram_id: int, vin_data: dict):
        """Сохранить данные последнего VIN"""
        try:
            self.client.table("users").update({"last_vin_data": vin_data}).eq("telegram_id", telegram_id).execute()
        except Exception as e:
            logger.error(f"DB Error save_vin_data: {e}")
    
    async def get_user(self, telegram_id: int):
        """Получить пользователя"""
        try:
            result = self.client.table("users").select("*").eq("telegram_id", telegram_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"DB Error get_user: {e}")
            return None

db = Database()
