import os
import json
import logging
from datetime import datetime

import aiohttp

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.base_url = SUPABASE_URL.rstrip('/')
        self.headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
    
    async def _request(self, method: str, endpoint: str, data=None, params=None):
        """Универсальный HTTP-запрос к Supabase"""
        url = f"{self.base_url}/rest/v1/{endpoint}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=data,
                    params=params
                ) as response:
                    if response.status in (200, 201):
                        return await response.json()
                    elif response.status == 409:  # Conflict - already exists
                        return None
                    else:
                        text = await response.text()
                        logger.error(f"Supabase error {response.status}: {text}")
                        return None
            except Exception as e:
                logger.error(f"Request error: {e}")
                return None
    
    async def get_or_create_user(self, telegram_id: int, username: str = None):
        """Получить или создать пользователя"""
        # Сначала ищем
        result = await self._request(
            "GET", 
            "users",
            params={"telegram_id": f"eq.{telegram_id}", "select": "*"}
        )
        
        if result and len(result) > 0:
            return result[0]
        
        # Создаём нового
        new_user = {
            "telegram_id": telegram_id,
            "username": username,
            "subscription_type": "free",
            "checks_used": 0,
            "checks_limit": 3,
            "parts_requests": 0,
            "parts_limit": 1,
            "last_vin_data": None,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        result = await self._request("POST", "users", data=new_user)
        if result and len(result) > 0:
            return result[0]
        
        # Если conflict, снова ищем
        result = await self._request(
            "GET", 
            "users",
            params={"telegram_id": f"eq.{telegram_id}", "select": "*"}
        )
        return result[0] if result and len(result) > 0 else None
    
    async def increment_checks(self, telegram_id: int):
        """Увеличить счетчик проверок"""
        user = await self.get_or_create_user(telegram_id)
        if not user:
            return 0
        
        new_count = user.get("checks_used", 0) + 1
        await self._request(
            "PATCH",
            f"users?telegram_id=eq.{telegram_id}",
            data={"checks_used": new_count}
        )
        return new_count
    
    async def increment_parts(self, telegram_id: int):
        """Увеличить счетчик запросов запчастей"""
        user = await self.get_or_create_user(telegram_id)
        if not user:
            return 0
        
        new_count = user.get("parts_requests", 0) + 1
        await self._request(
            "PATCH",
            f"users?telegram_id=eq.{telegram_id}",
            data={"parts_requests": new_count}
        )
        return new_count
    
    async def save_vin_data(self, telegram_id: int, vin_data: dict):
        """Сохранить данные последнего VIN"""
        await self._request(
            "PATCH",
            f"users?telegram_id=eq.{telegram_id}",
            data={"last_vin_data": vin_data}
        )
    
    async def get_user(self, telegram_id: int):
        """Получить пользователя"""
        result = await self._request(
            "GET",
            "users",
            params={"telegram_id": f"eq.{telegram_id}", "select": "*"}
        )
        return result[0] if result and len(result) > 0 else None

db = Database()
