import logging
import re
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import FREE_CHECKS_LIMIT, FREE_PARTS_LIMIT, PRO_PRICE, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
from database import db
from keyboards import *

logger = logging.getLogger(__name__)

# DeepSeek через aiohttp
import aiohttp

async def deepseek_request(prompt: str, system_prompt: str = None) -> str:
    """Запрос к DeepSeek API"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    
    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1500
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=data
        ) as response:
            if response.status == 200:
                result = await response.json()
                return result["choices"][0]["message"]["content"]
            else:
                text = await response.text()
                logger.error(f"DeepSeek error {response.status}: {text}")
                return None

# ========== КОМАНДЫ ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await db.get_or_create_user(update.effective_user.id, update.effective_user.username)
    
    welcome_text = (
        "🚗 <b>АвтоЭксперт AI</b>\n\n"
        "Проверь авто по VIN за секунды:\n"
        "• 📋 Технические характеристики\n"
        "• ⚠️ Скрытые проблемы\n"
        "• 💰 Оценка рыночной стоимости\n"
        "• 🔧 Цены на запчасти\n\n"
    )
    
    if user and user.get("subscription_type") == "pro":
        welcome_text += "💎 У вас PRO подписка!\n\n"
    else:
        remaining = max(0, FREE_CHECKS_LIMIT - user.get("checks_used", 0))
        welcome_text += f"🆓 Бесплатных проверок осталось: {remaining}\n\n"
    
    welcome_text += "Выберите действие:"
    
    await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажмите «🔍 Проверить VIN»\n"
        "2️⃣ Введите 17-значный VIN номер\n"
        "3️⃣ Получите полный отчёт от AI\n\n"
        "<b>Что такое VIN?</b>\n"
        "VIN — уникальный номер авто из 17 символов. "
        "Находится в ПТС, СТС, на лобовом стекле.\n\n"
        "<b>Примеры VIN:</b>\n"
        "• JHMCM56533C004353 (Honda)\n"
        "• LVRHDBDL9RN123456 (Mazda китайская)\n\n"
        "💎 <b>PRO ($9.99/мес):</b>\n"
        "• Безлимитные проверки\n"
        "• Полный отчёт о стоимости владения\n"
        "• Сравнение авто"
    )
    await update.message.reply_text(help_text, reply_markup=back_menu_keyboard(), parse_mode="HTML")

# ========== CALLBACK ОБРАБОТЧИКИ ==========

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)

async def check_vin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 <b>Введите VIN номер</b>\n\n"
        "VIN состоит из 17 символов (буквы и цифры).\n"
        "Пример: <code>JHMCM56533C004353</code>\n\n"
        "❌ Не вводите пробелы и дефисы",
        parse_mode="HTML"
    )
    return 1  # WAITING_FOR_VIN

async def process_vin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vin = update.message.text.strip().upper().replace(" ", "").replace("-", "")
    
    if not validate_vin(vin):
        await update.message.reply_text(
            "❌ <b>Некорректный VIN!</b>\n\n"
            "VIN должен содержать ровно 17 символов.\n"
            "Исключены: I, O, Q.\n\n"
            "Попробуйте ещё раз:",
            parse_mode="HTML"
        )
        return 1
    
    await update.message.reply_text(
        f"📝 <b>Проверить VIN:</b>\n\n<code>{vin}</code>\n\nВсё верно?",
        reply_markup=confirm_vin_keyboard(vin),
        parse_mode="HTML"
    )
    return ConversationHandler.END

async def confirm_vin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    vin = query.data.split(":")[1]
    user = await db.get_user(query.from_user.id)
    
    # Проверка лимита
    if user and user.get("subscription_type") == "free":
        if user.get("checks_used", 0) >= FREE_CHECKS_LIMIT:
            await query.edit_message_text(
                "⚡ <b>Лимит исчерпан!</b>\n\n"
                f"Вы использовали {FREE_CHECKS_LIMIT} проверок.\n\n"
                "💎 Перейдите на PRO для безлимита.",
                reply_markup=pro_upgrade_keyboard(),
                parse_mode="HTML"
            )
            return
    
    msg = await query.edit_message_text("🔍 Анализирую VIN... Это займёт 10-20 секунд")
    
    try:
        result = await analyze_vin(vin)
        
        vin_data = {
            "vin": vin,
            "make": result.get("make", "Unknown"),
            "model": result.get("model", "Unknown"),
            "year": result.get("year", "Unknown")
        }
        await db.save_vin_data(query.from_user.id, vin_data)
        await db.increment_checks(query.from_user.id)
        
        response_text = format_vin_result(result, vin)
        await msg.edit_text(response_text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"VIN analysis error: {e}")
        await msg.edit_text(
            "❌ Ошибка при анализе VIN. Попробуйте позже.",
            reply_markup=back_menu_keyboard()
        )

async def my_checks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = await db.get_user(query.from_user.id)
    if not user:
        await query.edit_message_text("❌ Ошибка. Нажмите /start", reply_markup=back_menu_keyboard())
        return
    
    checks_used = user.get("checks_used", 0)
    checks_limit = user.get("checks_limit", FREE_CHECKS_LIMIT)
    subscription = user.get("subscription_type", "free")
    
    text = (
        "📊 <b>Ваша статистика:</b>\n\n"
        f"👤 Подписка: <b>{'💎 PRO' if subscription == 'pro' else '🆓 Free'}</b>\n"
        f"🔍 Проверок: <b>{checks_used}</b>\n"
        f"🔍 Лимит: <b>{'∞' if subscription == 'pro' else checks_limit}</b>\n"
        f"🔧 Запросов запчастей: <b>{user.get('parts_requests', 0)}</b>\n\n"
    )
    
    if subscription == "free":
        remaining = max(0, checks_limit - checks_used)
        text += f"🆓 Осталось: <b>{remaining}</b>"
        if remaining == 0:
            text += "\n\n⚠️ Лимит исчерпан!"
    
    await query.edit_message_text(text, reply_markup=back_menu_keyboard(), parse_mode="HTML")

async def parts_estimate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = await db.get_user(query.from_user.id)
    if not user:
        await query.edit_message_text("❌ Ошибка. Нажмите /start", reply_markup=back_menu_keyboard())
        return
    
    vin_data = user.get("last_vin_data")
    if not vin_data:
        await query.edit_message_text(
            "❌ <b>Сначала проверьте VIN!</b>",
            reply_markup=back_menu_keyboard(),
            parse_mode="HTML"
        )
        return
    
    if user.get("subscription_type") == "free" and user.get("parts_requests", 0) >= FREE_PARTS_LIMIT:
        await query.edit_message_text(
            "⚡ <b>Лимит исчерпан!</b>\n\n"
            "В Free — 1 оценка запчастей в месяц.\n\n"
            "💎 PRO ($9.99/мес) — безлимит!",
            reply_markup=pro_upgrade_keyboard(),
            parse_mode="HTML"
        )
        return
    
    msg = await query.edit_message_text("🔍 Анализирую цены на запчасти...")
    
    try:
        estimate = await get_parts_estimate_from_ai(vin_data)
        await msg.edit_text(estimate, reply_markup=parts_detail_keyboard(), parse_mode="HTML")
        await db.increment_parts(query.from_user.id)
    except Exception as e:
        logger.error(f"Parts error: {e}")
        await msg.edit_text("❌ Ошибка. Попробуйте позже.", reply_markup=back_menu_keyboard())

async def pro_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "💎 <b>АвтоЭксперт PRO</b>\n\n"
        "✅ Безлимитные проверки VIN\n"
        "✅ Полный отчёт о стоимости владения\n"
        "✅ Безлимитные оценки запчастей\n"
        "✅ Сравнение авто\n"
        "✅ Приоритетная поддержка\n\n"
        f"<b>Цена: ${PRO_PRICE}/мес</b>\n\n"
        "Оплата вручную через админа."
    )
    await query.edit_message_text(text, reply_markup=pro_upgrade_keyboard(), parse_mode="HTML")

async def buy_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💳 <b>Оплата PRO</b>\n\n"
        "Напишите @admin для активации.\n\n"
        "После оплаты отправьте скриншот.",
        reply_markup=back_menu_keyboard(),
        parse_mode="HTML"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заглушка для необработанных кнопок"""
    query = update.callback_query
    await query.answer()

# ========== ВСПОМОГАТЕЛЬНЫЕ ==========

def validate_vin(vin: str) -> bool:
    if len(vin) != 17:
        return False
    allowed = set("0123456789ABCDEFGHJKLMNPRSTUVWXYZ")
    return all(c in allowed for c in vin)

async def analyze_vin(vin: str) -> dict:
    wmi = vin[:3]
    region = get_region_by_wmi(wmi)
    
    system_prompt = "Ты — эксперт по автомобилям. Анализируй VIN. Отвечай на русском."
    
    user_prompt = f"""Проанализируй VIN: {vin}
WMI: {wmi}, Регион: {region}

Определи: производитель, модель, год, кузов, двигатель, страна сборки, проблемы модели (2-3), рыночная цена в РБ (BYN).

Ответь ТОЛЬКО JSON:
{{"make":"...","model":"...","year":"...","body_type":"...","engine":"...","assembly_country":"...","issues":["..."],"market_price_byn":"...","confidence":"high/medium/low"}}"""

    content = await deepseek_request(user_prompt, system_prompt)
    
    if not content:
        return {"vin": vin, "make": "Unknown", "model": "Unknown", "year": "Unknown"}
    
    try:
        content = content.replace("```json", "").replace("```", "").strip()
        result = json.loads(content)
        result["vin"] = vin
        result["wmi"] = wmi
        result["region"] = region
        return result
    except:
        return {"vin": vin, "make": "Unknown", "model": "Unknown", "year": "Unknown", "raw": content}

def get_region_by_wmi(wmi: str) -> str:
    first = wmi[0]
    regions = {
        "1": "США", "4": "США", "5": "США",
        "2": "Канада", "3": "Мексика",
        "J": "Япония", "K": "Корея", "L": "Китай",
        "M": "Индия", "N": "Турция",
        "S": "Великобритания", "W": "Германия",
        "X": "Россия", "Y": "Швеция", "Z": "Италия",
    }
    return regions.get(first, "Неизвестно")

def format_vin_result(result: dict, vin: str) -> str:
    conf = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(result.get("confidence", "medium"), "🟡")
    
    text = (
        f"{conf} <b>Результат проверки VIN</b>\n\n"
        f"🚗 <b>{result.get('make', 'Unknown')} {result.get('model', 'Unknown')}</b>\n"
        f"📅 Год: {result.get('year', 'Unknown')}\n"
        f"🏭 Страна: {result.get('assembly_country', 'Unknown')}\n"
        f"🚙 Кузов: {result.get('body_type', 'Unknown')}\n"
        f"⚙️ Двигатель: {result.get('engine', 'Unknown')}\n\n"
        f"💰 <b>Цена в РБ:</b> {result.get('market_price_byn', 'Нет данных')} BYN\n\n"
    )
    
    issues = result.get("issues", [])
    if issues:
        text += "⚠️ <b>Проблемы модели:</b>\n"
        for i, issue in enumerate(issues, 1):
            text += f"{i}. {issue}\n"
        text += "\n"
    
    text += (
        f"📝 VIN: <code>{vin}</code>\n"
        f"🌍 Регион: {result.get('region', 'Unknown')}\n\n"
        f"⚡ <b>Цены на запчасти?</b> Нажмите «🔧 Запчасти» в меню."
    )
    return text

async def get_parts_estimate_from_ai(vin_data: dict) -> str:
    brand = vin_data.get("make", "Unknown")
    model = vin_data.get("model", "Unknown")
    year = vin_data.get("year", "Unknown")
    
    system_prompt = "Ты — эксперт по запчастям в Беларуси. Цены в BYN. Август 2026."
    
    user_prompt = f"""Цены на запчасти {brand} {model} {year} в Беларуси.

Таблица:
| Запчасть | Мин (BYN) | Макс (BYN) | Примечание |

Включи: масляный фильтр, воздушный фильтр, тормозные колодки (перед+зад), свечи, ремень ГРМ, амортизатор, аккумулятор.

В примечании: оригинал/аналог.

В конце:
⚠️ Цены ориентировочные. Уточняйте у поставщиков."""

    content = await deepseek_request(user_prompt, system_prompt)
    
    if not content:
        return "❌ Ошибка при получении цен. Попробуйте позже."
    
    header = f"🔧 <b>Цены на запчасти: {brand} {model}</b>\n\n"
    footer = "\n\n💎 <b>Полный отчёт — в PRO версии!</b>"
    
    return header + content + footer
