import os
import asyncio
from datetime import datetime, time as dtime, timedelta, date
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, TelegramObject
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

import database
from locales import t, get_kb_main, get_kb_reports

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=TOKEN)
dp = Dispatcher()

active_timers: dict[int, asyncio.Task] = {}
last_pinged: dict[int, datetime] = {}

class RegistrationStates(StatesGroup):
    WaitingForLanguage = State()
    WaitingForName = State()
    WaitingForShop = State()
    WaitingForNewShopName = State()

class SettingsStates(StatesGroup):
    WaitingForOpenTime = State()
    WaitingForCloseTime = State()
    WaitingForOfflineName = State()
    WaitingForNewName = State()
    WaitingForNewShop = State()
    WaitingForNewShopNameChange = State()
    WaitingForBarcode = State()

class BreakStates(StatesGroup):
    WaitingForWorkReason = State()
    WaitingForEditIndex = State()
    WaitingForEditTime = State()
    WaitingForEditReason = State()
    WaitingForDeleteIndex = State()
    WaitingForArchiveDate = State()

class ScheduleStates(StatesGroup):
    WaitingForManualWorker = State()

class AdminStates(StatesGroup):
    WaitingForMsgUserId = State()
    WaitingForMsgText = State()
    WaitingForFwdUserId = State()
    WaitingForFwdMessage = State()

# --- MIDDLEWARE ---
class RegistrationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user_id = event.from_user.id
        user = await database.get_user(user_id)
        
        state: FSMContext = data.get("state")
        current_state = await state.get_state() if state else None
        
        is_registration = False
        if isinstance(event, Message) and event.text == "/start":
            is_registration = True
        if current_state and current_state.startswith("RegistrationStates"):
            is_registration = True

        # Superadmin bypasses all checks
        if user_id == ADMIN_ID:
            data['db_user'] = user
            data['lang'] = user['lang'] if user else 'ru'
            return await handler(event, data)

        # Block check
        if user and user.get('is_blocked'):
            if isinstance(event, Message):
                await event.answer("🚫 Ваш аккаунт заблокирован. Обратитесь к администратору.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Аккаунт заблокирован.", show_alert=True)
            return None
            
        if user or is_registration:
            data['db_user'] = user
            data['lang'] = user['lang'] if user else 'ru'
            return await handler(event, data)
            
        if isinstance(event, Message):
            await event.answer("⚠️ Вы не зарегистрированы! Пожалуйста, нажмите /start")
        elif isinstance(event, CallbackQuery):
            await event.answer("Требуется регистрация: /start", show_alert=True)
        return None

dp.message.middleware(RegistrationMiddleware())
dp.callback_query.middleware(RegistrationMiddleware())

# --- REGISTRATION ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, db_user: dict, lang: str):
    if db_user:
        await message.answer(t("welcome_back", lang, name=db_user['full_name']), reply_markup=get_kb_main(lang))
        return
        
    await state.clear()
    await state.set_state(RegistrationStates.WaitingForLanguage)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_ua"),
         InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")]
    ])
    await message.answer(t("choose_lang", "ru"), reply_markup=kb)

@dp.callback_query(RegistrationStates.WaitingForLanguage, F.data.startswith("lang_"))
async def process_reg_lang(call: CallbackQuery, state: FSMContext):
    lang = call.data.split("_")[1]
    await state.update_data(lang=lang)
    await state.set_state(RegistrationStates.WaitingForName)
    await call.message.edit_text(t("reg_name", lang), parse_mode="HTML")

@dp.message(RegistrationStates.WaitingForName)
async def process_reg_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    if len(full_name) < 3:
        await message.answer(t("err_name_len", lang))
        return
        
    await state.update_data(full_name=full_name)
    shops = await database.get_shops()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for s in shops:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"🏪 {s['name']}", callback_data=f"reg_shop_{s['id']}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=t("btn_new_shop", lang), callback_data="reg_new_shop")])
    
    await state.set_state(RegistrationStates.WaitingForShop)
    await message.answer(t("reg_shop", lang, name=full_name), reply_markup=kb)

@dp.callback_query(RegistrationStates.WaitingForShop, F.data.startswith("reg_shop_"))
async def process_reg_shop(call: CallbackQuery, state: FSMContext):
    shop_id = int(call.data.split("_")[2])
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    await database.create_user(call.from_user.id, data["full_name"], shop_id, lang)
    await state.clear()
    await call.message.edit_text(t("reg_done", lang), parse_mode="HTML")
    await call.message.answer(t("main_menu", lang), reply_markup=get_kb_main(lang))

@dp.callback_query(RegistrationStates.WaitingForShop, F.data == "reg_new_shop")
async def process_reg_new_shop(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await state.set_state(RegistrationStates.WaitingForNewShopName)
    await call.message.edit_text(t("reg_new_shop_name", lang), parse_mode="HTML")

@dp.message(RegistrationStates.WaitingForNewShopName)
async def process_reg_new_shop_name(message: Message, state: FSMContext):
    shop_name = message.text.strip()
    shop_id = await database.create_shop(shop_name)
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    await database.create_user(message.from_user.id, data["full_name"], shop_id, lang)
    await state.clear()
    await message.answer(t("shop_created", lang, shop=shop_name), parse_mode="HTML", reply_markup=get_kb_main(lang))

@dp.message(Command("admin"))
async def cmd_admin(message: Message, lang: str):
    if message.from_user.id != ADMIN_ID:
        await message.answer(t("admin_no_access", lang))
        return
        
    grouped = await database.get_all_users_grouped()
    lines = [t("admin_title", lang)]
    for shop, users in grouped.items():
        lines.append(f"\n🏬 <b>{shop}</b>")
        for u in users:
            mark = " (офлайн)" if u["id"] < 0 else ""
            lines.append(f"▫️ {u['name']}{mark}")
            
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Графики магазинов", callback_data="adm_shops"),
         InlineKeyboardButton(text="👥 Управление юзерами", callback_data="adm_users")],
        [InlineKeyboardButton(text="✉️ Написать юзеру", callback_data="adm_msg"),
         InlineKeyboardButton(text="📨 Переслать от себя", callback_data="adm_fwd")]
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# =============================================
# SUPERADMIN: просмотр графиков любого магазина
# =============================================

@dp.callback_query(F.data == "adm_shops")
async def adm_shops_list(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    shops = await database.get_shops()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🏬 {s['name']}", callback_data=f"adm_sch_{s['id']}_{date.today().isoformat()}")]
        for s in shops
    ])
    await call.message.edit_text("🗓 <b>Выберите магазин для просмотра графика:</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("adm_sch_"))
async def adm_view_schedule(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split("_")
    shop_id = int(parts[2])
    target_date = date.fromisoformat(parts[3])
    shop = await database.get_shop(shop_id)
    mon, sun = get_week_range(target_date)
    sched = await database.get_weekly_schedule(shop_id, mon, sun)
    
    lines = [f"🏬 <b>{shop['name']}</b>\n🗓 Неделя: <b>{mon.strftime('%d.%m')} — {sun.strftime('%d.%m')}</b>\n"]
    curr = mon
    for i in range(7):
        w_text = sched.get(curr, "<i>Не указано</i>")
        lines.append(f"▫️ {WEEKDAYS_RU[i]} ({curr.strftime('%d.%m')}): {w_text}")
        curr += timedelta(days=1)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_sch_{shop_id}_{(target_date - timedelta(days=7)).isoformat()}"),
         InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"adm_sch_{shop_id}_{(target_date + timedelta(days=7)).isoformat()}")],
        [InlineKeyboardButton(text="🔙 К списку магазинов", callback_data="adm_shops")]
    ])
    await call.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")


# =============================================
# SUPERADMIN: блокировка / разблокировка юзеров
# =============================================

@dp.callback_query(F.data == "adm_users")
async def adm_users_list(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await database.get_all_users_for_admin()
    if not users:
        await call.answer("Нет пользователей", show_alert=True)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for u in users:
        status = "🚫" if u["is_blocked"] else "✅"
        shop = u["shop_name"] or "Без магазина"
        btn_text = f"{status} {u['full_name']} [{shop}]"
        action = "unblock" if u["is_blocked"] else "block"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"adm_{action}_{u['user_id']}")
        ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")])
    await call.message.edit_text(
        "👥 <b>Управление пользователями</b>\n\n✅ — активен | 🚫 — заблокирован\nНажми на пользователя для переключения:",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("adm_block_"))
async def adm_block_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    uid = int(call.data.split("_")[2])
    user = await database.get_user(uid)
    await database.block_user(uid)
    name = user['full_name'] if user else str(uid)
    await call.answer(f"🚫 {name} заблокирован", show_alert=True)
    # Refresh the list
    await adm_users_list(call)

@dp.callback_query(F.data.startswith("adm_unblock_"))
async def adm_unblock_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    uid = int(call.data.split("_")[2])
    user = await database.get_user(uid)
    await database.unblock_user(uid)
    name = user['full_name'] if user else str(uid)
    await call.answer(f"✅ {name} разблокирован", show_alert=True)
    # Refresh the list
    await adm_users_list(call)

@dp.callback_query(F.data == "adm_back")
async def adm_back(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    grouped = await database.get_all_users_grouped()
    lines = ["👑 <b>Список пользователей:</b>"]
    for shop, users in grouped.items():
        lines.append(f"\n🏬 <b>{shop}</b>")
        for u in users:
            mark = " (офлайн)" if u["id"] < 0 else ""
            lines.append(f"▫️ {u['name']}{mark}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Графики магазинов", callback_data="adm_shops"),
         InlineKeyboardButton(text="👥 Управление юзерами", callback_data="adm_users")],
        [InlineKeyboardButton(text="✉️ Написать юзеру", callback_data="adm_msg"),
         InlineKeyboardButton(text="📨 Переслать от себя", callback_data="adm_fwd")]
    ])
    await call.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")


# =============================================
# SUPERADMIN: написать пользователю
# =============================================

@dp.callback_query(F.data == "adm_msg")
async def adm_msg_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await database.get_all_users_for_admin()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"👤 {u['full_name']} [{u['shop_name'] or 'Без магазина'}]",
            callback_data=f"adm_msg_to_{u['user_id']}"
        )]
        for u in users
    ] + [[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]])
    await call.message.edit_text(
        "✉️ <b>Кому написать от имени бота?</b>\nВыбери пользователя:",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("adm_msg_to_"))
async def adm_msg_pick_user(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    uid = int(call.data.split("_")[3])
    user = await database.get_user(uid)
    name = user['full_name'] if user else str(uid)
    await state.update_data(msg_target_uid=uid, msg_target_name=name)
    await state.set_state(AdminStates.WaitingForMsgText)
    await call.message.edit_text(
        f"✉️ Пишем сообщение для <b>{name}</b> (от имени бота).\n\nВведи текст сообщения:",
        parse_mode="HTML"
    )

@dp.message(AdminStates.WaitingForMsgText)
async def adm_msg_send(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    uid = data["msg_target_uid"]
    name = data["msg_target_name"]
    try:
        await bot.send_message(uid, f"📢 <b>Сообщение от администратора:</b>\n\n{message.text}", parse_mode="HTML")
        await message.answer(f"✅ Сообщение отправлено пользователю <b>{name}</b>.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить: {e}")
    await state.clear()


# =============================================
# SUPERADMIN: переслать своё сообщение юзеру
# =============================================

@dp.callback_query(F.data == "adm_fwd")
async def adm_fwd_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await database.get_all_users_for_admin()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"👤 {u['full_name']} [{u['shop_name'] or 'Без магазина'}]",
            callback_data=f"adm_fwd_to_{u['user_id']}"
        )]
        for u in users
    ] + [[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]])
    await call.message.edit_text(
        "📨 <b>Кому переслать сообщение от себя?</b>\nВыбери пользователя:",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("adm_fwd_to_"))
async def adm_fwd_pick_user(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    uid = int(call.data.split("_")[3])
    user = await database.get_user(uid)
    name = user['full_name'] if user else str(uid)
    await state.update_data(fwd_target_uid=uid, fwd_target_name=name)
    await state.set_state(AdminStates.WaitingForFwdMessage)
    await call.message.edit_text(
        f"📨 Теперь напиши (или перешли сюда) сообщение — оно будет переслано пользователю <b>{name}</b> от тебя лично:",
        parse_mode="HTML"
    )

@dp.message(AdminStates.WaitingForFwdMessage)
async def adm_fwd_send(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    uid = data["fwd_target_uid"]
    name = data["fwd_target_name"]
    try:
        await bot.forward_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
        await message.answer(f"✅ Сообщение переслано пользователю <b>{name}</b>.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось переслать: {e}")
    await state.clear()

# --- NOTIFICATIONS ---
async def notify_colleagues(shop_id: int, editor_id: int, text_template: str, worker: str, date_str: str):
    users = await database.get_shop_users(shop_id)
    for u in users:
        if u["user_id"] != editor_id:
            u_lang = u.get("lang", "ru")
            # We construct the message in the recipient's language
            editor_name = ""
            for x in users:
                if x["user_id"] == editor_id:
                    editor_name = x["full_name"]
            
            body = t(text_template, u_lang, name=editor_name, date=date_str, worker=worker)
            msg = t("notify_update", u_lang, text=body)
            try:
                await bot.send_message(u["user_id"], msg, parse_mode="HTML")
            except Exception:
                pass

# --- SCHEDULE ---
def get_week_range(d: date) -> tuple[date, date]:
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)
    return mon, sun

WEEKDAYS_RU = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
WEEKDAYS_UA = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "НД"]

async def render_schedule(shop_id: int, target_date: date, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    mon, sun = get_week_range(target_date)
    sched = await database.get_weekly_schedule(shop_id, mon, sun)
    
    lines = [t("sch_title", lang), t("sch_week", lang, mon=mon.strftime('%d.%m'), sun=sun.strftime('%d.%m'))]
    
    curr = mon
    wdays = WEEKDAYS_UA if lang == "ua" else WEEKDAYS_RU
    for i in range(7):
        w_text = sched.get(curr, t("not_specified", lang))
        lines.append(f"▫️ {wdays[i]} ({curr.strftime('%d.%m')}): {w_text}")
        curr += timedelta(days=1)
        
    text = "\n".join(lines)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_prev", lang), callback_data=f"sch_nav_{-1}_{target_date.isoformat()}"),
         InlineKeyboardButton(text=t("btn_next", lang), callback_data=f"sch_nav_{1}_{target_date.isoformat()}")],
        [InlineKeyboardButton(text=t("btn_edit_sch", lang), callback_data=f"sch_edit_{target_date.isoformat()}")]
    ])
    return text, kb

def is_btn(text: str, key: str) -> bool:
    return text in (t(key, "ru"), t(key, "ua"))

@dp.message(lambda msg: is_btn(msg.text, "btn_schedule"))
async def handle_schedule(message: Message, state: FSMContext, db_user: dict, lang: str):
    await state.clear()
    if not db_user.get("shop_id"):
        await message.answer(t("no_shop", lang))
        return
        
    text, kb = await render_schedule(db_user["shop_id"], date.today(), lang)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("sch_nav_"))
async def cb_sch_nav(call: CallbackQuery, db_user: dict, lang: str):
    parts = call.data.split("_")
    shift_weeks = int(parts[2])
    base_date = date.fromisoformat(parts[3])
    
    new_date = base_date + timedelta(days=7 * shift_weeks)
    text, kb = await render_schedule(db_user["shop_id"], new_date, lang)
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        await call.answer()

@dp.callback_query(F.data.startswith("sch_edit_"))
async def cb_sch_edit(call: CallbackQuery, lang: str):
    base_date = date.fromisoformat(call.data.split("_")[2])
    mon, sun = get_week_range(base_date)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    row = []
    curr = mon
    wdays = WEEKDAYS_UA if lang == "ua" else WEEKDAYS_RU
    for i in range(7):
        row.append(InlineKeyboardButton(text=wdays[i], callback_data=f"sch_day_{curr.isoformat()}"))
        curr += timedelta(days=1)
        if len(row) == 4:
            kb.inline_keyboard.append(row)
            row = []
    if row:
        kb.inline_keyboard.append(row)
        
    kb.inline_keyboard.append([InlineKeyboardButton(text=t("btn_back_schedule", lang), callback_data=f"sch_nav_0_{base_date.isoformat()}")])
    
    await call.message.edit_text(t("sch_pick_day", lang, mon=mon.strftime('%d.%m'), sun=sun.strftime('%d.%m')), reply_markup=kb)

@dp.callback_query(F.data.startswith("sch_day_"))
async def cb_sch_day(call: CallbackQuery, db_user: dict, state: FSMContext, lang: str):
    target_date = date.fromisoformat(call.data.split("_")[2])
    shop_users = await database.get_shop_users(db_user["shop_id"])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    kb.inline_keyboard.append([InlineKeyboardButton(text=t("btn_me", lang, name=db_user['full_name']), callback_data=f"sch_set_{target_date.isoformat()}_{db_user['user_id']}")])
    
    for u in shop_users:
        if u["user_id"] != db_user["user_id"]:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"👤 {u['full_name']}", callback_data=f"sch_set_{target_date.isoformat()}_{u['user_id']}")])
            
    kb.inline_keyboard.append([InlineKeyboardButton(text=t("btn_day_off", lang), callback_data=f"sch_set_{target_date.isoformat()}_OFF")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=t("btn_manual", lang), callback_data=f"sch_manual_{target_date.isoformat()}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data=f"sch_nav_0_{target_date.isoformat()}")])
    
    await call.message.edit_text(t("sch_who_works", lang, date=target_date.strftime('%d.%m.%Y')), reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("sch_set_"))
async def cb_sch_set(call: CallbackQuery, db_user: dict, lang: str):
    parts = call.data.split("_")
    target_date = date.fromisoformat(parts[2])
    target_val = parts[3]
    
    worker_text = t("btn_day_off", lang)
    if target_val != "OFF":
        target_uid = int(target_val)
        user_info = await database.get_user(target_uid)
        if user_info:
            worker_text = user_info["full_name"]
            
    await database.set_schedule(db_user["shop_id"], target_date, worker_text)
    await notify_colleagues(db_user["shop_id"], db_user["user_id"], "sch_updated_notify", worker_text, target_date.strftime('%d.%m'))
    
    text, kb = await render_schedule(db_user["shop_id"], target_date, lang)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("sch_manual_"))
async def cb_sch_manual(call: CallbackQuery, state: FSMContext, lang: str):
    target_date = date.fromisoformat(call.data.split("_")[2])
    await state.update_data(sch_date=target_date.isoformat(), sch_msg_id=call.message.message_id)
    await state.set_state(ScheduleStates.WaitingForManualWorker)
    await call.message.edit_text(t("sch_manual_text", lang), parse_mode="HTML")

@dp.message(ScheduleStates.WaitingForManualWorker)
async def process_sch_manual(message: Message, state: FSMContext, db_user: dict, lang: str):
    data = await state.get_data()
    target_date = date.fromisoformat(data["sch_date"])
    worker_text = message.text.strip()
    
    await database.set_schedule(db_user["shop_id"], target_date, worker_text)
    await notify_colleagues(db_user["shop_id"], db_user["user_id"], "sch_updated_notify", worker_text, target_date.strftime('%d.%m'))
    
    await message.answer(t("sch_updated", lang, date=target_date.strftime('%d.%m')))
    
    text, kb = await render_schedule(db_user["shop_id"], target_date, lang)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.clear()

# --- SETTINGS ---
@dp.message(lambda msg: is_btn(msg.text, "btn_settings"))
async def handle_settings(message: Message, state: FSMContext, db_user: dict, lang: str):
    await state.clear()
    shop = await database.get_shop(db_user["shop_id"])
    open_t = shop["open_time"].strftime("%H:%M")
    close_t = shop["close_time"].strftime("%H:%M")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_change_time", lang), callback_data="set_shop_time")],
        [InlineKeyboardButton(text=t("btn_change_lang", lang), callback_data="set_user_lang")],
        [InlineKeyboardButton(text=t("btn_change_name", lang), callback_data="set_user_name")],
        [InlineKeyboardButton(text=t("btn_change_shop", lang), callback_data="set_user_shop")],
        [InlineKeyboardButton(text=t("btn_add_offline", lang), callback_data="add_offline")],
        [InlineKeyboardButton(text=t("btn_del_offline", lang), callback_data="del_offline")],
        [InlineKeyboardButton(
            text=t("btn_dice_on" if db_user.get("dice_enabled", True) else "btn_dice_off", lang),
            callback_data="toggle_dice"
        )],
        [InlineKeyboardButton(text=t("btn_change_tz", lang), callback_data="set_shop_tz")]
    ])
    await message.answer(t("settings_title", lang, shop=shop['name'], open=open_t, close=close_t), reply_markup=kb, parse_mode="HTML")

@dp.message(lambda msg: is_btn(msg.text, "btn_barcode"))
async def handle_barcode(message: Message, state: FSMContext, db_user: dict, lang: str):
    await state.clear()
    file_id = await database.get_barcode(db_user["user_id"])
    if file_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📷 Обновить / Оновити", callback_data="upload_barcode")]
        ])
        await message.answer_photo(file_id, caption=t("barcode_caption", lang), reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Загрузить / Завантажити", callback_data="upload_barcode")]
        ])
        await message.answer(t("barcode_empty", lang), reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "upload_barcode")
async def cb_upload_barcode(call: CallbackQuery, state: FSMContext, lang: str):
    await state.set_state(SettingsStates.WaitingForBarcode)
    await call.message.answer(t("barcode_ask_upload", lang), parse_mode="HTML")
    await call.answer()

@dp.message(SettingsStates.WaitingForBarcode)
async def process_barcode_upload(message: Message, state: FSMContext, db_user: dict, lang: str):
    if not message.photo:
        await message.answer(t("barcode_err_photo", lang), parse_mode="HTML")
        return
    # Take the highest-resolution version of the photo
    file_id = message.photo[-1].file_id
    await database.set_barcode(db_user["user_id"], file_id)
    await state.clear()
    await message.answer(t("barcode_saved", lang))

@dp.callback_query(F.data == "add_offline")
async def cb_add_offline(call: CallbackQuery, state: FSMContext, lang: str):
    await state.set_state(SettingsStates.WaitingForOfflineName)
    await call.message.edit_text(t("ask_offline_name", lang), parse_mode="HTML")

@dp.message(SettingsStates.WaitingForOfflineName)
async def process_add_offline(message: Message, state: FSMContext, db_user: dict, lang: str):
    name = message.text.strip()
    await database.create_offline_user(name, db_user["shop_id"])
    await state.clear()
    await message.answer(t("offline_added", lang, name=name), parse_mode="HTML")

@dp.callback_query(F.data == "del_offline")
async def cb_del_offline(call: CallbackQuery, db_user: dict, lang: str):
    off_users = await database.get_offline_users(db_user["shop_id"])
    if not off_users:
        await call.answer(t("err_no_offline", lang), show_alert=True)
        return
        
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for u in off_users:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ {u['full_name']}", callback_data=f"del_off_{u['user_id']}")])
        
    await call.message.edit_text(t("offline_del_list", lang), reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("del_off_"))
async def cb_del_off_exec(call: CallbackQuery, db_user: dict, lang: str):
    uid = int(call.data.split("_")[2])
    await database.delete_offline_user(uid, db_user["shop_id"])
    await call.message.edit_text(t("offline_deleted", lang))

@dp.callback_query(F.data == "set_user_lang")
async def cb_set_user_lang(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="change_lang_ua"),
         InlineKeyboardButton(text="🇷🇺 Русский", callback_data="change_lang_ru")]
    ])
    await call.message.edit_text("🇺🇦 Оберіть мову / 🇷🇺 Выберите язык:", reply_markup=kb)

@dp.callback_query(F.data.startswith("change_lang_"))
async def cb_change_lang(call: CallbackQuery, db_user: dict):
    lang = call.data.split("_")[2]
    await database.update_user_lang(db_user["user_id"], lang)
    await call.message.edit_text(t("lang_updated", lang))
    await call.message.answer(t("main_menu", lang), reply_markup=get_kb_main(lang))

@dp.callback_query(F.data == "toggle_dice")
async def cb_toggle_dice(call: CallbackQuery, db_user: dict, lang: str):
    new_val = await database.toggle_dice(db_user["user_id"])
    msg_key = "dice_toggled_on" if new_val else "dice_toggled_off"
    await call.answer(t(msg_key, lang), show_alert=True)


@dp.callback_query(F.data == "set_shop_tz")
async def cb_set_shop_tz(call: CallbackQuery, db_user: dict, lang: str):
    shop = await database.get_shop(db_user["shop_id"])
    current_offset = shop.get("tz_offset", 2)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕑 GMT+2 (Киев / Kyiv)", callback_data="change_tz_2")],
        [InlineKeyboardButton(text="🕒 GMT+3 (Москва / Moscow)", callback_data="change_tz_3")],
    ])
    await call.message.edit_text(
        t("tz_current", lang, offset=current_offset),
        reply_markup=kb,
        parse_mode="HTML"
    )
    await call.answer()

@dp.callback_query(F.data.startswith("change_tz_"))
async def cb_change_tz(call: CallbackQuery, db_user: dict, lang: str):
    offset = int(call.data.split("_")[2])
    await database.update_shop_tz(db_user["shop_id"], offset)
    await call.message.edit_text(t("tz_updated", lang, offset=offset), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "set_user_name")
async def cb_set_user_name(call: CallbackQuery, state: FSMContext, lang: str):
    await state.set_state(SettingsStates.WaitingForNewName)
    await call.message.edit_text(t("ask_new_name", lang), parse_mode="HTML")

@dp.message(SettingsStates.WaitingForNewName)
async def process_new_name(message: Message, state: FSMContext, db_user: dict, lang: str):
    new_name = message.text.strip()
    if len(new_name.split()) < 2:
        await message.answer(t("err_name_len", lang))
        return
    await database.update_user_name(db_user["user_id"], new_name)
    await state.clear()
    await message.answer(t("name_updated", lang, name=new_name), parse_mode="HTML", reply_markup=get_kb_main(lang))

@dp.callback_query(F.data == "set_user_shop")
async def cb_set_user_shop(call: CallbackQuery, state: FSMContext, lang: str):
    shops = await database.get_shops()
    rows = [
        [InlineKeyboardButton(text=s["name"], callback_data=f"chshop_{s['id']}")]
        for s in shops
    ]
    rows.append([InlineKeyboardButton(text=t("btn_new_shop", lang), callback_data="chshop_new")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(t("ask_new_shop", lang), reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "chshop_new")
async def cb_chshop_new(call: CallbackQuery, state: FSMContext, lang: str):
    await state.set_state(SettingsStates.WaitingForNewShopNameChange)
    await call.message.edit_text(t("reg_new_shop_name", lang), parse_mode="HTML")

@dp.message(SettingsStates.WaitingForNewShopNameChange)
async def process_new_shop_name_change(message: Message, state: FSMContext, db_user: dict, lang: str):
    shop_name = message.text.strip()
    shop_id = await database.create_shop(shop_name)
    await database.update_user_shop(db_user["user_id"], shop_id)
    await state.clear()
    await message.answer(t("shop_updated", lang, shop=shop_name), parse_mode="HTML", reply_markup=get_kb_main(lang))

@dp.callback_query(F.data.startswith("chshop_"))
async def cb_change_shop(call: CallbackQuery, db_user: dict, lang: str):
    shop_id = int(call.data.split("_")[1])
    await database.update_user_shop(db_user["user_id"], shop_id)
    shop = await database.get_shop(shop_id)
    await call.message.edit_text(t("shop_updated", lang, shop=shop["name"]), parse_mode="HTML")
    await call.message.answer(t("main_menu", lang), reply_markup=get_kb_main(lang))

@dp.callback_query(F.data == "set_shop_time")
async def cb_set_shop_time(call: CallbackQuery, state: FSMContext, lang: str):
    await state.set_state(SettingsStates.WaitingForOpenTime)
    await call.message.edit_text(t("set_open_time", lang), parse_mode="HTML")

@dp.message(SettingsStates.WaitingForOpenTime)
async def process_set_open_time(message: Message, state: FSMContext, lang: str):
    try:
        h, m = map(int, message.text.split(":"))
        t_time = dtime(h, m)
    except Exception:
        await message.answer(t("err_time_format", lang))
        return
    await state.update_data(new_open_time=t_time.isoformat())
    await state.set_state(SettingsStates.WaitingForCloseTime)
    await message.answer(t("set_close_time", lang), parse_mode="HTML")

@dp.message(SettingsStates.WaitingForCloseTime)
async def process_set_close_time(message: Message, state: FSMContext, db_user: dict, lang: str):
    try:
        h, m = map(int, message.text.split(":"))
        t_time = dtime(h, m)
    except Exception:
        await message.answer(t("err_time_format", lang))
        return
    data = await state.get_data()
    open_t = dtime.fromisoformat(data["new_open_time"])
    
    await database.update_shop_times(db_user["shop_id"], open_t, t_time)
    await state.clear()
    await message.answer(t("time_updated", lang, open=open_t.strftime('%H:%M'), close=t_time.strftime('%H:%M')), parse_mode="HTML")


# --- TIMERS AND BREAKS LOGIC ---
async def live_timer_task(user_id: int, message: Message, start_time: datetime, lang: str):
    reminders_sent = set()
    try:
        while True:
            await asyncio.sleep(10)
            now = database.now_tz()
            
            st_floor = start_time.replace(second=0, microsecond=0)
            now_floor = now.replace(second=0, microsecond=0)
            duration_minutes = int((now_floor - st_floor).total_seconds() // 60)
            
            total_seconds = int((now - start_time).total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            
            start_str = start_time.strftime("%H:%M")
            new_text = t("break_started", lang, time=start_str, dur=time_str)
            
            try:
                await message.edit_text(new_text, parse_mode="HTML")
            except TelegramBadRequest:
                pass 
                
            if duration_minutes >= 9 and 9 not in reminders_sent:
                reminders_sent.add(9)
                await bot.send_message(user_id, t("warn_10m", lang), parse_mode="HTML")
            elif duration_minutes >= 14 and 14 not in reminders_sent:
                reminders_sent.add(14)
                await bot.send_message(user_id, t("warn_14m", lang), parse_mode="HTML")
            elif duration_minutes >= 19 and 19 not in reminders_sent:
                reminders_sent.add(19)
                await bot.send_message(user_id, t("warn_19m", lang), parse_mode="HTML")
                
            if total_seconds > 7200: 
                break
                
    except asyncio.CancelledError:
        pass

@dp.message(lambda msg: is_btn(msg.text, "btn_back"))
async def handle_back(message: Message, state: FSMContext, lang: str):
    await state.clear()
    await message.answer(t("menu_back", lang), reply_markup=get_kb_main(lang))

@dp.message(lambda msg: is_btn(msg.text, "btn_menu"))
async def handle_reports_menu(message: Message, state: FSMContext, lang: str):
    await state.clear()
    await message.answer(t("menu_reports", lang), reply_markup=get_kb_reports(lang))

@dp.message(lambda msg: is_btn(msg.text, "btn_out"))
async def handle_out(message: Message, state: FSMContext, lang: str):
    await state.clear()
    success, start_dt = await database.start_break(message.from_user.id)
    if success:
        start_str = start_dt.strftime("%H:%M")
        msg = await message.answer(t("break_started", lang, time=start_str, dur="00:00:00"), parse_mode="HTML")
        
        today_breaks = await database.get_today_breaks(message.from_user.id)
        current_hour = database.now_tz().hour
        count_this_hour = sum(1 for b in today_breaks if b["start_time"].astimezone(database.TZ).hour == current_hour)
        if count_this_hour > 1:
            await message.answer(t("warn_same_hour", lang), parse_mode="HTML")
            
        if message.from_user.id in active_timers:
            active_timers[message.from_user.id].cancel()
        active_timers[message.from_user.id] = asyncio.create_task(live_timer_task(message.from_user.id, msg, start_dt, lang))
    else:
        await message.answer(t("err_unclosed", lang))

@dp.message(lambda msg: is_btn(msg.text, "btn_in"))
async def handle_in(message: Message, state: FSMContext, lang: str):
    await state.clear()
    
    if message.from_user.id in active_timers:
        active_timers[message.from_user.id].cancel()
        del active_timers[message.from_user.id]
        
    success, break_id, base_tag, duration = await database.end_break(message.from_user.id)
    if success:
        # Translate base tag
        translated_tag = ""
        if base_tag == "(Задержка)": translated_tag = t("tag_delay", lang)
        elif base_tag == "(Обед)": translated_tag = t("tag_lunch", lang)
        elif base_tag == "(Обед плюс задержка)": translated_tag = t("tag_lunch_delay", lang)
        
        text = t("break_ended", lang, dur=duration)
        if translated_tag:
            text += f" {translated_tag}"
            
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_work", lang), callback_data=f"br_work_{break_id}"),
             InlineKeyboardButton(text=t("btn_transfer", lang), callback_data=f"br_transf_{break_id}")],
            [InlineKeyboardButton(text=t("btn_delivery", lang), callback_data=f"br_deliv_{break_id}")],
            [InlineKeyboardButton(text=t("btn_edit", lang), callback_data="br_edit")],
            [InlineKeyboardButton(text=t("btn_close", lang), callback_data="br_close")]
        ])
        await message.answer(f"{text}\n\n{t('break_what', lang)}", reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(t("err_not_out", lang))

@dp.callback_query(F.data.startswith("br_transf_"))
async def cb_transfer(call: CallbackQuery, lang: str):
    break_id = int(call.data.split("_")[2])
    await database.set_transfer_by_id(break_id)
    await call.message.edit_text(call.message.text + "\n\n" + t("saved_transfer", lang), parse_mode="HTML")

@dp.callback_query(F.data == "br_close")
async def cb_close(call: CallbackQuery, lang: str):
    await call.message.edit_text(call.message.text + "\n\n" + t("saved_normal", lang), parse_mode="HTML")

@dp.callback_query(F.data.startswith("br_deliv_"))
async def cb_delivery(call: CallbackQuery, lang: str):
    break_id = int(call.data.split("_")[2])
    success, msg = await database.add_delivery_by_id(call.from_user.id, break_id)
    if success:
        # success message from db is in RU, we can hardcode translate or just use our own
        await call.message.edit_text(call.message.text + f"\n\n<i>🚚 Довоз / Довіз</i>", parse_mode="HTML")
    else:
        await call.answer(msg, show_alert=True)

@dp.callback_query(F.data.startswith("br_work_"))
async def cb_work(call: CallbackQuery, state: FSMContext, lang: str):
    break_id = int(call.data.split("_")[2])
    await state.update_data(break_id=break_id)
    await state.set_state(BreakStates.WaitingForWorkReason)
    await call.message.edit_text(call.message.text + "\n\n" + t("saved_work", lang), parse_mode="HTML")

@dp.message(BreakStates.WaitingForWorkReason)
async def process_work_reason(message: Message, state: FSMContext, lang: str):
    data = await state.get_data()
    break_id = data.get("break_id")
    text = f"(Робочий/Рабочий: {message.text})"
    await database.set_custom_text(break_id, text)
    await message.answer(t("desc_saved", lang))
    await state.clear()

@dp.callback_query(F.data == "br_edit")
async def cb_edit(call: CallbackQuery, state: FSMContext, lang: str):
    breaks = await database.get_today_breaks(call.from_user.id)
    if not breaks:
        await call.answer(t("no_breaks_today", lang), show_alert=True)
        return
        
    lines = [t("edit_which", lang)]
    valid_indexes = []
    
    for i, b in enumerate(breaks, start=1):
        start_str = b["start_time"].astimezone(database.TZ).strftime("%H:%M")
        end_str = b["end_time"].astimezone(database.TZ).strftime("%H:%M") if b["end_time"] else "..."
        lines.append(f"{i}. {start_str}-{end_str}")
        valid_indexes.append(i)
        
    serialized = []
    for b in breaks:
        d = dict(b)
        d["start_time"] = d["start_time"].isoformat()
        if d["end_time"]:
            d["end_time"] = d["end_time"].isoformat()
        serialized.append(d)
        
    await state.update_data(breaks=serialized)
    await state.set_state(BreakStates.WaitingForEditIndex)
    
    await call.message.edit_text(call.message.text + "\n\n" + t("edit_editing", lang), parse_mode="HTML")
    await call.message.answer("\n".join(lines) + "\n\n" + t("edit_ask_index", lang), parse_mode="HTML")

@dp.message(BreakStates.WaitingForEditIndex)
async def process_edit_index(message: Message, state: FSMContext, lang: str):
    try:
        idx = int(message.text)
    except ValueError:
        await message.answer(t("err_number", lang))
        return
        
    data = await state.get_data()
    breaks = data.get("breaks", [])
    if not (1 <= idx <= len(breaks)):
        await message.answer(t("err_not_found", lang, idx=idx, max=len(breaks)))
        return
        
    target_break = breaks[idx - 1]
    await state.update_data(edit_break_id=target_break["id"], edit_date=target_break["start_time"])
    await state.set_state(BreakStates.WaitingForEditTime)
    await message.answer(t("edit_ask_time", lang), parse_mode="HTML")

@dp.message(BreakStates.WaitingForEditTime)
async def process_edit_time(message: Message, state: FSMContext, lang: str):
    data = await state.get_data()
    break_id = data["edit_break_id"]
    
    text = message.text.lower().replace(" ", "")
    skip_word = t("word_skip", lang).lower()
    
    if text not in (skip_word, "пропустить", "пропустити"):
        if "-" not in text:
            await message.answer(t("err_format_time", lang))
            return
            
        parts = text.split("-")
        if len(parts) != 2:
            await message.answer(t("err_format_time", lang))
            return
            
        try:
            h1, m1 = map(int, parts[0].split(":"))
            h2, m2 = map(int, parts[1].split(":"))
            t1 = dtime(h1, m1)
            t2 = dtime(h2, m2)
        except Exception:
            await message.answer("❌ Ошибка в числах.")
            return
            
        original_start_str = data["edit_date"]
        original_start = datetime.fromisoformat(original_start_str).astimezone(database.TZ)
        
        new_start = original_start.replace(hour=t1.hour, minute=t1.minute, second=0)
        new_end = original_start.replace(hour=t2.hour, minute=t2.minute, second=0)
        
        if new_end < new_start:
            await message.answer(t("err_time_logic", lang))
            return
            
        await database.update_break_time(break_id, new_start, new_end)
        
    await state.set_state(BreakStates.WaitingForEditReason)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_mark_lunch", lang), callback_data="mark_as_lunch")],
        [InlineKeyboardButton(text=t("btn_skip", lang), callback_data="skip_edit_reason")]
    ])
    await message.answer(t("edit_ask_reason", lang), reply_markup=kb)

@dp.callback_query(F.data == "mark_as_lunch")
async def cb_mark_as_lunch(call: CallbackQuery, state: FSMContext, lang: str):
    data = await state.get_data()
    break_id = data.get("edit_break_id")
    if break_id:
        await database.update_break_tag(break_id, "(Обед)")
    await state.clear()
    await call.message.edit_text(call.message.text + "\n\n" + t("lunch_marked", lang), parse_mode="HTML")

@dp.callback_query(F.data == "skip_edit_reason")
async def skip_edit_reason(call: CallbackQuery, state: FSMContext, lang: str):
    await state.clear()
    await call.message.edit_text(call.message.text + "\n\n" + t("edit_skipped", lang), parse_mode="HTML")
    await call.message.answer(t("edit_done", lang))

@dp.message(BreakStates.WaitingForEditReason)
async def process_edit_reason(message: Message, state: FSMContext, lang: str):
    data = await state.get_data()
    break_id = data["edit_break_id"]
    await database.set_custom_text(break_id, f"({message.text})")
    await state.clear()
    await message.answer(t("reason_updated", lang))

@dp.message(lambda message: message.text and message.text.lower().strip() in ("довоз", "довіз"))
async def handle_delivery_text(message: Message, state: FSMContext, lang: str):
    await state.clear()
    breaks = await database.get_today_breaks(message.from_user.id)
    if not breaks:
        await message.answer(t("no_breaks_today", lang))
        return
    last_break = None
    for b in reversed(breaks):
        if b["end_time"]:
            last_break = b
            break
    if not last_break:
        await message.answer(t("no_breaks_today", lang))
        return
        
    success, msg = await database.add_delivery_by_id(message.from_user.id, last_break["id"])
    if success:
        await message.answer(f"🚚 ✅ Довоз/Довіз")
    else:
        await message.answer(f"❌ {msg}")

@dp.message(lambda msg: is_btn(msg.text, "btn_journal"))
async def handle_journal(message: Message, state: FSMContext, db_user: dict, lang: str):
    await state.clear()
    breaks = await database.get_today_breaks(message.from_user.id)
    if not breaks:
        await message.answer(t("journal_empty", lang))
        return
        
    now = database.now_tz()
    date_str = now.strftime("%d.%m.%Y")
    
    lines = [t("journal_title", lang, date=date_str, name=db_user['full_name'])]
    
    for b in breaks:
        start_dt = b["start_time"].astimezone(database.TZ)
        start_str = start_dt.strftime("%H:%M")
        
        if b["end_time"]:
            end_dt = b["end_time"].astimezone(database.TZ)
            end_str = end_dt.strftime("%H:%M")
        else:
            end_str = "..."
            
        line = f"▫️ <b>{start_str} - {end_str}</b>"
        
        tags = []
        if b["is_delivery"]: tags.append(t("tag_deliv", lang))
        if b.get("is_transfer"): tags.append(t("tag_trans", lang))
        
        if b["base_tag"] == "(Задержка)": tags.append(t("tag_delay", lang))
        elif b["base_tag"] == "(Обед)": tags.append(t("tag_lunch", lang))
        elif b["base_tag"] == "(Обед плюс задержка)": tags.append(t("tag_lunch_delay", lang))
        
        if b["custom_text"]:
            tags.append(b["custom_text"])
            
        if tags:
            line += " " + " ".join(tags)
            
        lines.append(line)
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_edit", lang), callback_data="br_edit"),
         InlineKeyboardButton(text=t("btn_delete", lang), callback_data="br_del_menu")]
    ])
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "br_del_menu")
async def cb_del_menu(call: CallbackQuery, state: FSMContext, lang: str):
    breaks = await database.get_today_breaks(call.from_user.id)
    if not breaks:
        await call.answer(t("no_breaks_today", lang), show_alert=True)
        return
        
    lines = [t("delete_which", lang)]
    for i, b in enumerate(breaks, start=1):
        start_str = b["start_time"].astimezone(database.TZ).strftime("%H:%M")
        end_str = b["end_time"].astimezone(database.TZ).strftime("%H:%M") if b["end_time"] else "..."
        lines.append(f"{i}. {start_str}-{end_str}")
        
    serialized = []
    for b in breaks:
        d = dict(b)
        d["start_time"] = d["start_time"].isoformat()
        if d["end_time"]:
            d["end_time"] = d["end_time"].isoformat()
        serialized.append(d)
        
    await state.update_data(breaks=serialized)
    await state.set_state(BreakStates.WaitingForDeleteIndex)
    
    await call.message.edit_text(call.message.text + "\n\n" + t("delete_editing", lang), parse_mode="HTML")
    await call.message.answer("\n".join(lines) + "\n\n" + t("delete_ask_index", lang), parse_mode="HTML")

@dp.message(BreakStates.WaitingForDeleteIndex)
async def process_delete_index(message: Message, state: FSMContext, lang: str):
    text = message.text.lower().replace(" ", "")
    skip_word = t("word_skip", lang).lower()
    
    if text in (skip_word, "пропустить", "пропустити"):
        await state.clear()
        await message.answer(t("edit_skipped", lang), parse_mode="HTML")
        return
        
    try:
        idx = int(message.text)
    except ValueError:
        await message.answer(t("err_number", lang))
        return
        
    data = await state.get_data()
    breaks = data.get("breaks", [])
    if not (1 <= idx <= len(breaks)):
        await message.answer(t("err_not_found", lang, idx=idx, max=len(breaks)))
        return
        
    target_break = breaks[idx - 1]
    await database.delete_break(target_break["id"])
    await state.clear()
    await message.answer(t("delete_done", lang))

@dp.message(lambda msg: is_btn(msg.text, "btn_archive"))
async def handle_archive_request(message: Message, state: FSMContext, lang: str):
    await state.clear()
    await state.set_state(BreakStates.WaitingForArchiveDate)
    await message.answer(t("archive_ask_date", lang), parse_mode="HTML")

@dp.message(BreakStates.WaitingForArchiveDate)
async def process_archive_date(message: Message, state: FSMContext, db_user: dict, lang: str):
    text = message.text.strip()
    try:
        dt = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(t("err_date_format", lang))
        return
        
    breaks = await database.get_breaks_by_date(message.from_user.id, dt)
    if not breaks:
        await message.answer(t("archive_empty", lang, date=text), parse_mode="HTML")
        await state.clear()
        return
        
    lines = [t("archive_title", lang, date=text, name=db_user['full_name'])]
    for b in breaks:
        start_str = b["start_time"].astimezone(database.TZ).strftime("%H:%M")
        if b["end_time"]:
            end_str = b["end_time"].astimezone(database.TZ).strftime("%H:%M")
        else:
            end_str = "..."
            
        line = f"▫️ <b>{start_str} - {end_str}</b>"
        tags = []
        if b["is_delivery"]: tags.append(t("tag_deliv", lang))
        if b.get("is_transfer"): tags.append(t("tag_trans", lang))
        
        if b["base_tag"] == "(Задержка)": tags.append(t("tag_delay", lang))
        elif b["base_tag"] == "(Обед)": tags.append(t("tag_lunch", lang))
        elif b["base_tag"] == "(Обед плюс задержка)": tags.append(t("tag_lunch_delay", lang))
        
        if b["custom_text"]:
            tags.append(b["custom_text"])
            
        if tags:
            line += " " + " ".join(tags)
        lines.append(line)
        
    await message.answer("\n".join(lines), parse_mode="HTML")
    await state.clear()

@dp.message(lambda msg: is_btn(msg.text, "btn_stats"))
async def handle_stats(message: Message, state: FSMContext, db_user: dict, lang: str):
    await state.clear()
    stats = await database.get_weekly_stats(message.from_user.id)
    
    if stats["total_breaks"] == 0:
        await message.answer(t("stats_empty", lang))
        return
        
    text = t("stats_title", lang, name=db_user['full_name'], total=stats['total_breaks'], dur=stats['total_duration'], delays=stats['delays'], lunches=stats['lunches'], deliv=stats['deliveries'], trans=stats['transfers'])
    await message.answer(text, parse_mode="HTML")

# --- Мини-игра (Кубик от скуки) и Напоминания ---
last_reminded_date = {}
morning_reminders_sent_date = None

async def daily_morning_reminders_loop():
    global morning_reminders_sent_date
    while True:
        await asyncio.sleep(60)
        now = database.now_tz()
        if now.hour == 8 and now.minute == 0:
            if morning_reminders_sent_date == now.date():
                continue
            morning_reminders_sent_date = now.date()
            
            weekday = now.weekday()
            day = now.day
            shops = await database.get_shops()
            
            for s in shops:
                users = await database.get_shop_users(s["id"])
                
                if day in (1, 15):
                    for u in users:
                        lang = u.get("lang", "ru")
                        try:
                            await bot.send_message(u["user_id"], t("rem_cleaning_1_15", lang), parse_mode="HTML")
                        except Exception: pass
                        
                if weekday == 4:
                    for u in users:
                        lang = u.get("lang", "ru")
                        try:
                            await bot.send_message(u["user_id"], t("rem_schedule_friday", lang), parse_mode="HTML")
                        except Exception: pass
                        
                if weekday in (0, 1):
                    target_date = now.date() + timedelta(days=2 if weekday == 0 else 4)
                    sched = await database.get_weekly_schedule(s["id"], target_date, target_date)
                    worker = sched.get(target_date, "")
                    
                    for u in users:
                        lang = u.get("lang", "ru")
                        worker_print = worker if worker else t("not_specified", lang)
                        msg_key = "rem_delivery_wed" if weekday == 0 else "rem_delivery_sat"
                        try:
                            await bot.send_message(u["user_id"], t(msg_key, lang, worker=worker_print), parse_mode="HTML")
                        except Exception: pass

async def daily_shop_reminders_loop():
    while True:
        await asyncio.sleep(60)
        now = database.now_tz()
        current_time = now.time()
        weekday = now.weekday()  # 0=Пн, 1=Вт
        day = now.day

        shops = await database.get_shops()
        for s in shops:
            close = s["close_time"]
            users = await database.get_shop_users(s["id"])
            real_users = [u for u in users if u["user_id"] > 0]

            # --- Конец смены (точное время закрытия) ---
            if close.hour == current_time.hour and close.minute == current_time.minute:
                if last_reminded_date.get(s["id"]) == now.date():
                    continue
                last_reminded_date[s["id"]] = now.date()

                tomorrow = now.date() + timedelta(days=1)
                sched = await database.get_weekly_schedule(s["id"], now.date(), tomorrow)
                today_text = sched.get(now.date(), "")
                tomorrow_text = sched.get(tomorrow, "")

                for u in real_users:
                    lang = u.get("lang", "ru")
                    tt_print = tomorrow_text if tomorrow_text else t("not_specified", lang)

                    works_today = u["full_name"] in today_text or "оба" in today_text.lower() or "обидва" in today_text.lower()
                    works_tomorrow = u["full_name"] in tomorrow_text or "оба" in tomorrow_text.lower() or "обидва" in tomorrow_text.lower()

                    if works_today:
                        msg = t("rem_shift_ended", lang, worker=tt_print)
                        try:
                            await bot.send_message(u["user_id"], msg, parse_mode="HTML")
                        except Exception: pass
                    elif works_tomorrow:
                        msg = t("rem_tomorrow", lang)
                        try:
                            await bot.send_message(u["user_id"], msg, parse_mode="HTML")
                        except Exception: pass

            # --- Инкассация: Вторник, за 10 минут до закрытия ---
            if weekday == 1:
                close_dt = now.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
                delta = int((close_dt - now).total_seconds() / 60)
                if 9 <= delta <= 10:
                    key = f"encash_{s['id']}_{now.date()}"
                    if not last_reminded_date.get(key):
                        last_reminded_date[key] = True
                        for u in real_users:
                            lang = u.get("lang", "ru")
                            try:
                                await bot.send_message(u["user_id"], t("rem_encashment", lang), parse_mode="HTML")
                            except Exception: pass

            # --- Отчёт об уборке: 5-е и 19-е, за 2 часа до закрытия ---
            if day in (5, 19):
                close_dt = now.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
                delta = int((close_dt - now).total_seconds() / 60)
                if 119 <= delta <= 120:
                    key = f"cleanrep_{s['id']}_{now.date()}"
                    if not last_reminded_date.get(key):
                        last_reminded_date[key] = True
                        for u in real_users:
                            lang = u.get("lang", "ru")
                            try:
                                await bot.send_message(u["user_id"], t("rem_cleaning_report", lang), parse_mode="HTML")
                            except Exception: pass


async def idle_game_loop():
    while True:
        await asyncio.sleep(15 * 60)
        now = database.now_tz()
        idle_users = await database.get_idle_users()
        for uid in idle_users:
            user = await database.get_user(uid)
            if not user or not user["shop_id"]: continue
            if not user.get("dice_enabled", True): continue  # пользователь отключил кубик
            lang = user.get("lang", "ru")

            
            shop = await database.get_shop(user["shop_id"])
            if not shop: continue
            
            sched = await database.get_weekly_schedule(user["shop_id"], now.date(), now.date())
            today_worker = sched.get(now.date(), "")
            if user["full_name"] not in today_worker and "оба" not in today_worker.lower() and "обидва" not in today_worker.lower():
                continue 
                
            current_time = now.time()
            open_t = shop["open_time"]
            close_t = shop["close_time"]
            
            is_open = False
            if open_t < close_t:
                is_open = open_t <= current_time < close_t
            else:
                is_open = current_time >= open_t or current_time < close_t
                
            if is_open:
                last_ping = last_pinged.get(uid)
                if not last_ping or (now - last_ping).total_seconds() > 3 * 3600:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=t("btn_dice_yes", lang), callback_data="mg_start"),
                         InlineKeyboardButton(text=t("btn_dice_no", lang), callback_data="mg_decline")]
                    ])
                    try:
                        await bot.send_message(uid, t("dice_ask", lang), reply_markup=kb)
                        last_pinged[uid] = now
                    except Exception:
                        pass 

@dp.callback_query(F.data == "mg_start")
async def cb_mg_start(call: CallbackQuery, lang: str):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("dice_even", lang), callback_data="mg_bet_even"),
         InlineKeyboardButton(text=t("dice_odd", lang), callback_data="mg_bet_odd")]
    ])
    await call.message.edit_text(t("dice_bet", lang), reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "mg_decline")
async def cb_mg_decline(call: CallbackQuery, lang: str):
    await call.message.edit_text(t("dice_declined", lang))

@dp.callback_query(F.data.startswith("mg_bet_"))
async def cb_mg_bet(call: CallbackQuery, lang: str):
    bet = call.data.split("_")[2]
    msg = await call.message.edit_text(t("dice_rolling", lang))
    dice_msg = await bot.send_dice(call.message.chat.id)
    await asyncio.sleep(4)
    val = dice_msg.dice.value
    is_even = (val % 2 == 0)
    won = (bet == "even" and is_even) or (bet == "odd" and not is_even)
    
    if won:
        res = t("dice_won", lang, val=val)
    else:
        res = t("dice_lost", lang, val=val)
    await msg.edit_text(res, parse_mode="HTML")

last_morning_date: dict[int, date] = {}

async def daily_morning_reminders_loop():
    while True:
        await asyncio.sleep(60)
        now = database.now_tz()

        # Срабатывает ровно в 08:00
        if now.hour != 8 or now.minute != 0:
            continue

        weekday = now.weekday()  # 0=Пн, 1=Вт, 4=Пт
        day = now.day
        shops = await database.get_shops()

        for s in shops:
            # Дедупликация: один раз в день на магазин
            if last_morning_date.get(s["id"]) == now.date():
                continue
            last_morning_date[s["id"]] = now.date()

            users = await database.get_shop_users(s["id"])
            real_users = [u for u in users if u["user_id"] > 0]

            # --- Генеральная уборка: 1-е и 15-е число ---
            if day in (1, 15):
                for u in real_users:
                    lang = u.get("lang", "ru")
                    try:
                        await bot.send_message(u["user_id"], t("rem_cleaning_1_15", lang), parse_mode="HTML")
                    except Exception:
                        pass

            # --- Понедельник: отписать за Среду (today+2) ---
            elif weekday == 0:
                target = now.date() + timedelta(days=2)
                sched = await database.get_weekly_schedule(s["id"], target, target)
                worker = sched.get(target, "")
                if not worker:
                    continue
                for u in real_users:
                    lang = u.get("lang", "ru")
                    try:
                        await bot.send_message(u["user_id"], t("rem_delivery_wed", lang, worker=worker), parse_mode="HTML")
                    except Exception:
                        pass

            # --- Вторник: отписать за Субботу (today+4) ---
            elif weekday == 1:
                target = now.date() + timedelta(days=4)
                sched = await database.get_weekly_schedule(s["id"], target, target)
                worker = sched.get(target, "")
                if not worker:
                    continue
                for u in real_users:
                    lang = u.get("lang", "ru")
                    try:
                        await bot.send_message(u["user_id"], t("rem_delivery_sat", lang, worker=worker), parse_mode="HTML")
                    except Exception:
                        pass

            # --- Пятница: напомнить составить график ---
            elif weekday == 4:
                for u in real_users:
                    lang = u.get("lang", "ru")
                    try:
                        await bot.send_message(u["user_id"], t("rem_schedule_friday", lang), parse_mode="HTML")
                    except Exception:
                        pass

async def main():
    print("Инициализация базы данных...")
    await database.init_db()
    print("Бот запущен.")
    
    asyncio.create_task(idle_game_loop())
    asyncio.create_task(daily_shop_reminders_loop())
    asyncio.create_task(daily_morning_reminders_loop())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    if not TOKEN:
        print("Ошибка: BOT_TOKEN не установлен!")
        exit(1)
    if not os.getenv("DATABASE_URL"):
        print("Ошибка: DATABASE_URL не установлен!")
        exit(1)
    asyncio.run(main())
