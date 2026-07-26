from supabase import create_async_client, AsyncClient
from config import SUPABASE_URL, SUPABASE_KEY

# Будем хранить клиент в глобальной переменной после инициализации
supabase: AsyncClient = None

async def get_client() -> AsyncClient:
    """Возвращает один и тот же AsyncClient (ленивая инициализация)."""
    global supabase
    if supabase is None:
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase

async def get_places_by_type(place_type: str):
    client = await get_client()
    res = await client.table("places").select("*").eq("type", place_type).order("name").execute()
    return res.data

async def get_place_by_id(place_id: int):
    client = await get_client()
    res = await client.table("places").select("*").eq("id", place_id).single().execute()
    return res.data

async def get_categories(place_id: int):
    client = await get_client()
    res = await client.table("menu_categories").select("*").eq("place_id", place_id).order("sort_order").execute()
    return res.data

async def get_menu_items(category_id: int):
    client = await get_client()
    res = await client.table("menu_items").select("*").eq("category_id", category_id).order("name").execute()
    return res.data

async def upsert_user(user: dict):
    client = await get_client()
    await client.table("users").upsert(user).execute()

async def update_last_active(user_id: int):
    client = await get_client()
    await client.table("users").update({"last_active": "now()"}).eq("user_id", user_id).execute()

async def get_users_count():
    client = await get_client()
    res = await client.table("users").select("*", count="exact").execute()
    return res.count

async def get_new_users_since(days: int):
    client = await get_client()
    res = await client.rpc('get_new_users_since', {'days': days}).execute()
    return res.data[0]['count'] if res.data else 0