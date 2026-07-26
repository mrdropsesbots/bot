from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def get_places_by_type(place_type: str):
    res = await supabase.table("places").select("*").eq("type", place_type).order("name").execute()
    return res.data

async def get_place_by_id(place_id: int):
    res = await supabase.table("places").select("*").eq("id", place_id).single().execute()
    return res.data

async def get_categories(place_id: int):
    res = await supabase.table("menu_categories").select("*").eq("place_id", place_id).order("sort_order").execute()
    return res.data

async def get_menu_items(category_id: int):
    res = await supabase.table("menu_items").select("*").eq("category_id", category_id).order("name").execute()
    return res.data

async def upsert_user(user: dict):
    await supabase.table("users").upsert(user).execute()

async def update_last_active(user_id: int):
    await supabase.table("users").update({"last_active": "now()"}).eq("user_id", user_id).execute()

async def get_users_count():
    res = await supabase.table("users").select("*", count="exact").execute()
    return res.count

async def get_new_users_since(days: int):
    res = await supabase.rpc('get_new_users_since', {'days': days}).execute()
    return res.data[0]['count'] if res.data else 0