    async def get_or_create_user(self, telegram_id: int, username: str = None):
        """Получить или создать пользователя"""
        try:
            # Проверяем существование
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
            
            # Если conflict или другая ошибка — ищем снова
            result = await self._request(
                "GET", 
                "users",
                params={"telegram_id": f"eq.{telegram_id}", "select": "*"}
            )
            if result and len(result) > 0:
                return result[0]
            
            # Если всё равно не нашли — возвращаем заглушку
            return {
                "telegram_id": telegram_id,
                "username": username,
                "subscription_type": "free",
                "checks_used": 0,
                "checks_limit": 3,
                "parts_requests": 0,
                "parts_limit": 1
            }
            
        except Exception as e:
            logger.error(f"DB Error get_or_create_user: {e}")
            # Возвращаем заглушку чтобы бот не падал
            return {
                "telegram_id": telegram_id,
                "username": username,
                "subscription_type": "free",
                "checks_used": 0,
                "checks_limit": 3,
                "parts_requests": 0,
                "parts_limit": 1
            }
