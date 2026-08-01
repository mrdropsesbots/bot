import logging
import re
import httpx
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import BOT_TOKEN, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, FREE_CHECKS_LIMIT, FREE_PARTS_LIMIT, ADMIN_ID
from database import db
from keyboards import *

logger = logging.getLogger(__name__)
router = Router()

# DeepSeek клиент
import openai
deepseek_client = openai.AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL
)

# Состояния
class VINStates(StatesGroup):
    waiting_for_vin = State()

# ========== КОМАНДЫ ==========

@router.message(Command("start"))
async def cmd_start(message: Message):
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    
    welcome_text = (
        "🚗 <b>АвтоЭксперт AI</b>\n\n"
        "Проверь авто по VIN за секунды:\n"
        "• 📋 Технические характеристики\n"
        "• ⚠️ Скрытые проблемы\n"
        "• 💰 Оценка рыночной стоимости\n"
        "• 🔧 Цены на запчасти\n\n"
        f"{'💎 У вас PRO подписка!' if user and user.get('subscription_type') == 'pro' else f'🆓 Бесплатных проверок осталось: {max(0, FREE_CHECKS_LIMIT - user.get(\"checks_used\", 0))}'}\n\n"
        "Выберите действие:"
    )
    
    await message.answer(welcome_text, reply_markup=main_menu_keyboard(), parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажмите «🔍 Проверить VIN»\n"
        "2️⃣ Введите 17-значный VIN номер\n"
        "3️⃣ Получите полный отчёт от AI\n\n"
        "<b>Что такое VIN?</b>\n"
        "VIN (Vehicle Identification Number) — уникальный номер авто из 17 символов. "
        "Находится в ПТС, СТС, на лобовом стекле (снизу слева) или на кузове.\n\n"
        "<b>Примеры VIN:</b>\n"
        "• JHMCM56533C004353 (Honda)\n"
        "• LVRHDBDL9RN123456 (Mazda китайская)\n"
        "• 5UXCR6C04L9B12345 (BMW)\n\n"
        "💎 <b>PRO ($9.99/мес):</b>\n"
        "• Безлимитные проверки\n"
        "• Полный отчёт о стоимости владения\n"
        "• Сравнение авто\n"
        "• Приоритетная поддержка"
    )
    await message.answer(help_text, reply_markup=back_menu_keyboard(), parse_mode="HTML")

# ========== CALLBACK ОБРАБОТЧИКИ ==========

@router.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_start(callback.message)

@router.callback_query(F.data == "check_vin")
async def check_vin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(VINStates.waiting_for_vin)
    await callback.message.edit_text(
        "🔍 <b>Введите VIN номер</b>\n\n"
        "VIN состоит из 17 символов (буквы и цифры).\n"
        "Пример: <code>JHMCM56533C004353</code>\n\n"
        "❌ Не вводите пробелы и дефисы",
        parse_mode="HTML"
    )

@router.callback_query(F.data == "my_checks")
async def my_checks(callback: CallbackQuery):
    await callback.answer()
    user = await db.get_user(callback.from_user.id)
    
    if not user:
        await callback.message.edit_text("❌ Ошибка. Нажмите /start", reply_markup=back_menu_keyboard())
        return
    
    checks_used = user.get("checks_used", 0)
    checks_limit = user.get("checks_limit", FREE_CHECKS_LIMIT)
    subscription = user.get("subscription_type", "free")
    
    text = (
        "📊 <b>Ваша статистика:</b>\n\n"
        f"👤 Подписка: <b>{'💎 PRO' if subscription == 'pro' else '🆓 Free'}</b>\n"
        f"🔍 Проверок использовано: <b>{checks_used}</b>\n"
        f"🔍 Лимит проверок: <b>{'∞' if subscription == 'pro' else checks_limit}</b>\n"
        f"🔧 Запросов запчастей: <b>{user.get('parts_requests', 0)}</b>\n\n"
    )
    
    if subscription == "free":
        remaining = max(0, checks_limit - checks_used)
        text += f"🆓 Осталось бесплатных проверок: <b>{remaining}</b>\n\n"
        if remaining == 0:
            text += "⚠️ Лимит исчерпан! Купите PRO для безлимита."
    
    await callback.message.edit_text(text, reply_markup=back_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "parts_estimate")
async def parts_estimate(callback: CallbackQuery):
    await callback.answer()
    user = await db.get_user(callback.from_user.id)
    
    if not user:
        await callback.message.edit_text("❌ Ошибка. Нажмите /start", reply_markup=back_menu_keyboard())
        return
    
    vin_data = user.get("last_vin_data")
    
    if not vin_data:
        await callback.message.edit_text(
            "❌ <b>Сначала проверьте VIN авто!</b>\n\n"
            "Нажмите «🔍 Проверить VIN» и введите номер.",
            reply_markup=back_menu_keyboard(),
            parse_mode="HTML"
        )
        return
    
    # Проверка лимита для Free
    if user.get("subscription_type") == "free" and user.get("parts_requests", 0) >= FREE_PARTS_LIMIT:
        await callback.message.edit_text(
            "⚡ <b>Лимит исчерпан!</b>\n\n"
            "В Free-версии — 1 оценка запчастей в месяц.\n\n"
            "💎 <b>PRO ($9.99/мес):</b>\n"
            "• Безлимитные оценки запчастей\n"
            "• Полный отчёт о стоимости владения\n"
            "• Сравнение с аналогами",
            reply_markup=pro_upgrade_keyboard(),
            parse_mode="HTML"
        )
        return
    
    # Показываем оценку
    msg = await callback.message.edit_text("🔍 Анализирую рыночные цены на запчасти...")
    
    try:
        estimate = await get_parts_estimate_from_ai(vin_data)
        await msg.edit_text(estimate, reply_markup=parts_detail_keyboard(), parse_mode="HTML")
        
        # Увеличиваем счетчик
        await db.increment_parts(callback.from_user.id)
        
    except Exception as e:
        logger.error(f"Parts estimate error: {e}")
        await msg.edit_text(
            "❌ Ошибка при анализе цен. Попробуйте позже.",
            reply_markup=back_menu_keyboard()
        )

@router.callback_query(F.data == "pro_info")
async def pro_info(callback: CallbackQuery):
    await callback.answer()
    text = (
        "💎 <b>АвтоЭксперт PRO</b>\n\n"
        "<b>Что включено:</b>\n"
        "✅ Безлимитные проверки VIN\n"
        "✅ Полный отчёт о стоимости владения\n"
        "✅ Безлимитные оценки запчастей\n"
        "✅ Сравнение 2-3 авто\n"
        "✅ История всех проверок\n"
        "✅ Приоритетная поддержка\n\n"
        f"<b>Цена: ${PRO_PRICE}/мес</b>\n\n"
        "⚡ Оплата через Telegram Stars или крипто-кошелёк\n\n"
        "Нажмите кнопку ниже для оплаты:"
    )
    await callback.message.edit_text(text, reply_markup=pro_upgrade_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "buy_pro")
async def buy_pro(callback: CallbackQuery):
    await callback.answer()
    # Заглушка для оплаты
    await callback.message.edit_text(
        "💳 <b>Оплата PRO</b>\n\n"
        "Пока оплата вручную. Напишите @admin для активации.\n\n"
        "Или переведите $9.99 на:\n"
        "• USDT TRC20: <code>YOUR_WALLET</code>\n"
        "• Карта РБ: <code>0000 0000 0000 0000</code>\n\n"
        "После оплаты отправьте скриншот админу.",
        reply_markup=back_menu_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery):
    await callback.answer()
    await cmd_help(callback.message)

@router.callback_query(F.data.startswith("confirm_vin:"))
async def confirm_vin(callback: CallbackQuery):
    await callback.answer()
    vin = callback.data.split(":")[1]
    
    user = await db.get_user(callback.from_user.id)
    
    # Проверка лимита
    if user and user.get("subscription_type") == "free":
        if user.get("checks_used", 0) >= FREE_CHECKS_LIMIT:
            await callback.message.edit_text(
                "⚡ <b>Лимит бесплатных проверок исчерпан!</b>\n\n"
                f"Вы использовали {FREE_CHECKS_LIMIT} из {FREE_CHECKS_LIMIT} проверок.\n\n"
                "💎 Перейдите на PRO для безлимита.",
                reply_markup=pro_upgrade_keyboard(),
                parse_mode="HTML"
            )
            return
    
    # Запускаем проверку
    msg = await callback.message.edit_text("🔍 Анализирую VIN... Это займет 10-20 секунд")
    
    try:
        result = await analyze_vin(vin)
        
        # Сохраняем данные VIN
        vin_data = {
            "vin": vin,
            "make": result.get("make", "Unknown"),
            "model": result.get("model", "Unknown"),
            "year": result.get("year", "Unknown")
        }
        await db.save_vin_data(callback.from_user.id, vin_data)
        
        # Увеличиваем счетчик
        await db.increment_checks(callback.from_user.id)
        
        # Формируем ответ
        response_text = format_vin_result(result, vin)
        
        await msg.edit_text(response_text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"VIN analysis error: {e}")
        await msg.edit_text(
            "❌ Ошибка при анализе VIN. Попробуйте позже или проверьте корректность номера.",
            reply_markup=back_menu_keyboard()
        )

# ========== ОБРАБОТКА ТЕКСТА ==========

@router.message(VINStates.waiting_for_vin)
async def process_vin(message: Message, state: FSMContext):
    vin = message.text.strip().upper().replace(" ", "").replace("-", "")
    
    # Валидация VIN
    if not validate_vin(vin):
        await message.answer(
            "❌ <b>Некорректный VIN!</b>\n\n"
            "VIN должен содержать ровно 17 символов (буквы A-Z и цифры 0-9).\n"
            "Исключены: I, O, Q (чтобы не путать с цифрами).\n\n"
            "Попробуйте ещё раз:",
            parse_mode="HTML"
        )
        return
    
    # Показываем подтверждение
    await message.answer(
        f"📝 <b>Проверить VIN:</b>\n\n"
        f"<code>{vin}</code>\n\n"
        f"Всё верно?",
        reply_markup=confirm_vin_keyboard(vin),
        parse_mode="HTML"
    )
    
    await state.clear()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def validate_vin(vin: str) -> bool:
    """Проверка корректности VIN"""
    if len(vin) != 17:
        return False
    # Разрешённые символы (без I, O, Q)
    allowed = set("0123456789ABCDEFGHJKLMNPRSTUVWXYZ")
    return all(c in allowed for c in vin)

async def analyze_vin(vin: str) -> dict:
    """Анализ VIN через DeepSeek"""
    
    # Определяем регион по WMI
    wmi = vin[:3]
    region = get_region_by_wmi(wmi)
    
    system_prompt = """Ты — эксперт по автомобилям. Анализируй VIN и давай структурированный ответ.
Всегда отвечай на русском языке. Будь точным и кратким."""

    user_prompt = f"""Проанализируй VIN номер: {vin}

WMI (первые 3 символа): {wmi}
Регион производства: {region}

Определи:
1. Производитель (марка)
2. Модель
3. Год выпуска
4. Тип кузова
5. Тип двигателя
6. Страна сборки
7. Возможные проблемы этой модели (2-3 пункта)
8. Рыночная стоимость в Беларуси (диапазон в BYN)

Ответь в формате JSON:
{{
    "make": "string",
    "model": "string", 
    "year": "string",
    "body_type": "string",
    "engine": "string",
    "assembly_country": "string",
    "issues": ["string", "string"],
    "market_price_byn": "string",
    "confidence": "high/medium/low"
}}"""

    response = await deepseek_client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.3,
        max_tokens=1000
    )
    
    content = response.choices[0].message.content
    
    # Парсим JSON из ответа
    import json
    try:
        # Убираем markdown код-блоки если есть
        content = content.replace("```json", "").replace("```", "").strip()
        result = json.loads(content)
        result["vin"] = vin
        result["wmi"] = wmi
        result["region"] = region
        return result
    except json.JSONDecodeError:
        # Если не JSON, возвращаем текст
        return {
            "vin": vin,
            "wmi": wmi,
            "region": region,
            "raw_response": content,
            "make": "Unknown",
            "model": "Unknown",
            "year": "Unknown"
        }

def get_region_by_wmi(wmi: str) -> str:
    """Определение региона по WMI"""
    first_char = wmi[0]
    
    regions = {
        "1": "США", "4": "США", "5": "США",
        "2": "Канада", "3": "Мексика",
        "J": "Япония", "K": "Корея", "L": "Китай",
        "M": "Индия/Таиланд", "N": "Турция",
        "S": "Великобритания", "T": "Швейцария/Венгрия/Польша",
        "V": "Франция/Испания/Югославия", "W": "Германия",
        "X": "Россия/Нидерланды/Люксембург", "Y": "Швеция/Бельгия/Финляндия",
        "Z": "Италия/Словения/Хорватия",
        "6": "Австралия", "7": "Новая Зеландия", "8": "Аргентина/Чили",
        "9": "Бразилия", "A": "Великобритания/Германия/ЮАР",
    }
    
    return regions.get(first_char, "Неизвестный регион")

def format_vin_result(result: dict, vin: str) -> str:
    """Форматирование результата проверки VIN"""
    
    confidence_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
    conf = confidence_emoji.get(result.get("confidence", "medium"), "🟡")
    
    text = (
        f"{conf} <b>Результат проверки VIN</b>\n\n"
        f"🚗 <b>{result.get('make', 'Unknown')} {result.get('model', 'Unknown')}</b>\n"
        f"📅 Год: {result.get('year', 'Unknown')}\n"
        f"🏭 Страна сборки: {result.get('assembly_country', 'Unknown')}\n"
        f"🚙 Кузов: {result.get('body_type', 'Unknown')}\n"
        f"⚙️ Двигатель: {result.get('engine', 'Unknown')}\n\n"
        f"💰 <b>Рыночная цена в РБ:</b> {result.get('market_price_byn', 'Нет данных')} BYN\n\n"
    )
    
    issues = result.get("issues", [])
    if issues:
        text += "⚠️ <b>Возможные проблемы модели:</b>\n"
        for i, issue in enumerate(issues, 1):
            text += f"{i}. {issue}\n"
        text += "\n"
    
    text += (
        f"📝 VIN: <code>{vin}</code>\n"
        f"🌍 Регион WMI: {result.get('region', 'Unknown')}\n\n"
        f"⚡ <b>Хотите узнать цены на запчасти?</b>\n"
        f"Нажмите «🔧 Запчасти и цены» в меню."
    )
    
    return text

async def get_parts_estimate_from_ai(vin_data: dict) -> str:
    """Оценка цен на запчасти через DeepSeek"""
    
    brand = vin_data.get("make", "Unknown")
    model = vin_data.get("model", "Unknown")
    year = vin_data.get("year", "Unknown")
    
    system_prompt = """Ты — эксперт по автозапчастям в Беларуси (август 2026).
Давай только факты. Цены в белорусских рублях (BYN).
Учитывай курс и реалии рынка."""

    user_prompt = f"""Дай ориентировочные цены на запчасти для {brand} {model} {year} года в Беларуси.

Формат: Markdown таблица
| Запчасть | Мин (BYN) | Макс (BYN) | Примечание |

Включи эти позиции:
- Масляный фильтр
- Воздушный фильтр
- Тормозные колодки передние
- Тормозные колодки задние
- Свечи зажигания (комплект)
- Ремень ГРМ (если применимо)
- Амортизатор передний
- Аккумулятор 60-70Ah

В примечании укажи: оригинал/аналог/китайский аналог.

В конце добавь:
⚠️ Цены ориентировочные (±20%). Актуальные цены уточняйте у поставщиков."""

    response = await deepseek_client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.3,
        max_tokens=1500
    )
    
    content = response.choices[0].message.content
    
    header = f"🔧 <b>Цены на запчасти: {brand} {model}</b>\n\n"
    footer = "\n\n💎 <b>Полный отчёт о стоимости владения — в PRO версии!</b>"
    
    return header + content + footer
