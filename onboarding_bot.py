#!/usr/bin/env python
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "city_taxi_project.settings")
django.setup()

import asyncio
import logging
import datetime
from asgiref.sync import sync_to_async

from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from asgiref.sync import sync_to_async
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from django.utils import timezone

import requests

from django.conf import settings
from taxiapp.models import Driver, Announcement, ActiveUser

# --- Logging setup ---
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# Directory for Telethon sessions
SESSION_DIR = os.path.join(settings.BASE_DIR, "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

# In-memory store for TelethonClient during login
_clients: dict[int, TelegramClient] = {}

# --- FSM States ---

class AdminStates(StatesGroup):
    search_id = State()


class OnboardStates(StatesGroup):
    api_id    = State()
    api_hash  = State()

class LoginStates(StatesGroup):
    phone    = State()
    code     = State()
    password = State()

class SetupStates(StatesGroup):
    groups   = State()
    text     = State()
    interval = State()

class AdminAddStates(StatesGroup):
    name      = State()
    phone     = State()
    tg_id     = State()
    duration  = State()

# --- Bot & Dispatcher ---
bot = Bot(token=settings.ONBOARDING_BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Keyboards
def main_menu(active: bool) -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text="🔒 Login"),
            KeyboardButton(text="⚙️ Setup"),
        ],
        [
            KeyboardButton(text="⏹ Stop"),
            KeyboardButton(text="🗑 Delete"),
        ] if active else [
            KeyboardButton(text="▶️ Start"),
            KeyboardButton(text="🗑 Delete"),
            KeyboardButton(text="📝 Sign Up"),
        ],
    ]

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="➕ Add User"),
            KeyboardButton(text="📋 List Users"),
        ],
        [
            KeyboardButton(text="🔍 Check Driver"),
        ],
    ],
    resize_keyboard=True,
)
sign_up_kb = ReplyKeyboardMarkup(
    keyboard=[
        [ KeyboardButton(text="📝 Sign Up") ]
    ],
    resize_keyboard=True,
)

# Helper to check active user
async def is_active_user(user_id: int) -> bool:
    return await sync_to_async(
        lambda: ActiveUser.objects.filter(tg_id=user_id, active=True).exists(),
        thread_sensitive=True
    )()

# --- Handlers ---
@router.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    user_id = msg.from_user.id

    if user_id in settings.ADMIN_IDS:
        await msg.answer("Admin menu:", reply_markup=admin_menu)
        return

    if not await is_active_user(user_id):
        return await msg.answer("❌ Access denied. Please ask an admin to activate your account.")

    await msg.answer("Welcome! Use the button below.", reply_markup=sign_up_kb)

@router.message(lambda m: m.text == "📝 Sign Up")
async def signup_button(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Great — first I need your *API ID* (numeric).")
    await state.set_state(OnboardStates.api_id)

@router.message(OnboardStates.api_id)
async def process_api_id(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer("❌ API ID must be a number. Please try again.")
    await state.update_data(api_id=int(msg.text.strip()))
    await msg.answer("✅ Got it! Now send your *API hash* (the secret string).")
    await state.set_state(OnboardStates.api_hash)

@router.message(OnboardStates.api_hash)
async def process_api_hash(msg: types.Message, state: FSMContext):
    api_hash = msg.text.strip()
    data = await state.get_data()
    api_id = data["api_id"]
    tg_id  = msg.from_user.id

    # try to create or update the Driver row
    def _upsert_driver():
        Driver.objects.update_or_create(
            tg_id=tg_id,
            defaults={
                "api_id":   api_id,
                "api_hash": api_hash,
                "session":  "-",    # placeholder until /login
                "active":   True,
            },
        )
    try:
        await sync_to_async(_upsert_driver, thread_sensitive=True)()
    except Exception as e:
        # If something went wrong, offer the user a chance to delete & retry
        await msg.answer(
            "⚠️ Something went wrong saving your credentials.\n"
            "Please tap 🗑 Delete to remove your data and then try again.",
            reply_markup=main_menu(False)
        )
        await state.clear()
        return

    # success!
    await msg.answer(
        "🎉 All set! Your API credentials are stored.",
        reply_markup=main_menu(True)
    )
    await state.clear()


# Onboarding for Driver (API credentials)
@router.message(lambda m: m.text == "🔒 Login")
async def cmd_login(msg: types.Message, state: FSMContext):
    if not await is_active_user(msg.from_user.id):
        return await msg.answer("❌ Not active.")
    await state.clear()
    await msg.answer("📱 Send your phone number (with country code):")
    await state.set_state(LoginStates.phone)

@router.message(LoginStates.phone)
async def process_phone(msg: types.Message, state: FSMContext):
    phone = msg.text.strip()
    driver = await sync_to_async(Driver.objects.get)(tg_id=msg.from_user.id)
    session_file = os.path.join(SESSION_DIR, f"{msg.from_user.id}.session")
    client = TelegramClient(session_file, driver.api_id, driver.api_hash)
    await client.connect()
    await client.send_code_request(phone)
    _clients[msg.from_user.id] = client
    await state.update_data(phone=phone)
    await msg.answer("✉ Code sent. Enter it:")
    await state.set_state(LoginStates.code)

@router.message(LoginStates.code)
async def process_code(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    client = _clients[msg.from_user.id]
    try:
        await client.sign_in(data["phone"], msg.text.strip())
    except SessionPasswordNeededError:
        await msg.answer("🔒 2FA enabled. Send your password:")
        return await state.set_state(LoginStates.password)
    await client.disconnect()
    await msg.answer("✅ Logged in. Session saved.", reply_markup=main_menu(True))
    await state.clear()

@router.message(LoginStates.password)
async def process_password(msg: types.Message, state: FSMContext):
    client = _clients[msg.from_user.id]
    await client.sign_in(password=msg.text.strip())
    await client.disconnect()
    await msg.answer("✅ 2FA passed. You are fully logged in.", reply_markup=main_menu(True))
    await state.clear()

@router.message(lambda msg: msg.text == "⚙️ Setup")
async def cmd_setup(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("➡️ Send group usernames, comma‑separated:")
    await state.set_state(SetupStates.groups)

@router.message(SetupStates.groups)
async def process_groups(msg: types.Message, state: FSMContext):
    groups = [g.strip() for g in msg.text.split(",") if g.strip()]
    await state.update_data(groups=groups)
    await msg.answer("✏️ Now send the broadcast text:")
    await state.set_state(SetupStates.text)

@router.message(SetupStates.text)
async def process_text(msg: types.Message, state: FSMContext):
    await state.update_data(text=msg.text)
    await msg.answer("⏱ Finally, interval in minutes:")
    await state.set_state(SetupStates.interval)

@router.message(SetupStates.interval)
async def process_interval(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("Interval must be numeric.")
    data = await state.get_data()
    interval = int(msg.text)
    groups, text = data['groups'], data['text']
    tg_id = msg.from_user.id
    def _create_ann():
        Announcement.objects.filter(driver__tg_id=tg_id, active=True).update(active=False)
        return Announcement.objects.create(
            driver=Driver.objects.get(tg_id=tg_id),
            groups=groups,
            text=text,
            interval_minutes=interval,
            active=True
        )
    ann = await sync_to_async(_create_ann, thread_sensitive=True)()
    await msg.answer(f"✅ Will post every {interval} min to {len(groups)} groups.", reply_markup=main_menu(True))
    asyncio.create_task(post_loop(ann.id))
    await state.clear()

@router.message(lambda msg: msg.text and msg.text.lower() == "⏹ stop")
async def cmd_stop(msg: types.Message):
    tg_id = msg.from_user.id
    def _stop():
        return Announcement.objects.filter(driver__tg_id=tg_id, active=True).update(active=False)
    cnt = await sync_to_async(_stop, thread_sensitive=True)()

    await msg.answer(
        "🔴 Posting stopped." if cnt else "ℹ️ Nothing active to stop.",
        reply_markup=main_menu(is_active_user(tg_id))
    )

@router.message(lambda msg: msg.text and msg.text.lower() == "▶️ start")
async def cmd_start_announce(msg: types.Message):
    tg_id = msg.from_user.id

    # Reactivate the existing announcement
    def _reactivate():
        # find the most recent inactive announcement
        return Announcement.objects.filter(
            driver__tg_id=tg_id, active=False
        ).update(active=True)

    updated = await sync_to_async(_reactivate, thread_sensitive=True)()

    if updated:
        # grab its ID so we can restart the loop
        def _get_ann_id():
            return Announcement.objects.get(driver__tg_id=tg_id, active=True).id
        ann_id = await sync_to_async(_get_ann_id, thread_sensitive=True)()

        # restart the posting loop
        asyncio.create_task(post_loop(ann_id))

        await msg.answer("▶️ Posting restarted.", reply_markup=main_menu(True))
    else:
        await msg.answer("ℹ️ No stopped announcement to start.", reply_markup=main_menu(False))

@router.message(lambda msg: msg.text == "🗑 Delete")
async def cmd_delete(msg: types.Message):
    tg_id = msg.from_user.id
    def _del(): return Driver.objects.filter(tg_id=tg_id).delete()
    deleted, _ = await sync_to_async(_del, thread_sensitive=True)()
    await msg.answer("🗑 Driver deleted." if deleted else "ℹ️ No driver." , reply_markup=sign_up_kb)

# --- Admin Handlers ---
@router.message(lambda msg: msg.text == "➕ Add User")
async def admin_add_user(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in settings.ADMIN_IDS:
        return
    await state.clear()
    await msg.answer("👤 Enter new user’s name:")
    await state.set_state(AdminAddStates.name)

@router.message(AdminAddStates.name)
async def admin_add_name(msg: types.Message, state: FSMContext):
    await state.update_data(name=msg.text.strip())
    await msg.answer("📱 Enter phone (e.g. +123456789):")
    await state.set_state(AdminAddStates.phone)

@router.message(AdminAddStates.phone)
async def admin_add_phone(msg: types.Message, state: FSMContext):
    await state.update_data(phone=msg.text.strip())
    await msg.answer("🔢 Enter Telegram ID (numeric):")
    await state.set_state(AdminAddStates.tg_id)

@router.message(AdminAddStates.tg_id)
async def admin_add_tg(msg: types.Message, state: FSMContext):
    await state.update_data(tg_id=int(msg.text.strip()))
    await msg.answer("⏳ Enter duration in days:")
    await state.set_state(AdminAddStates.duration)

@router.message(AdminAddStates.duration)
async def admin_add_duration(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    days = int(msg.text.strip())
    now = timezone.now()
    expires = now + datetime.timedelta(days=days)
    def _create():
        ActiveUser.objects.update_or_create(
            tg_id=data['tg_id'],
            defaults={
                'name': data['name'],
                'phone': data['phone'],
                'activated_at': now,
                'expires_at': expires,
                'active': True
            }
        )
    await sync_to_async(_create, thread_sensitive=True)()
    await msg.answer(f"✅ User {data['name']} activated until {expires.date()}", reply_markup=admin_menu)
    await state.clear()

@router.message(lambda msg: msg.text == "📋 List Users")
async def admin_list(msg: types.Message):
    if msg.from_user.id not in settings.ADMIN_IDS:
        return

    # wrap the ORM call in sync_to_async
    @sync_to_async(thread_sensitive=True)
    def _fetch_users():
        return list(ActiveUser.objects.all())

    users = await _fetch_users()
    lines = [
        f"{u.name} ({u.tg_id}): {'Active' if u.active else 'Inactive'} until {u.expires_at.date()}"
        for u in users
    ]
    await msg.answer("📋 Users:\n" + "\n".join(lines), reply_markup=admin_menu)

@router.message(lambda m: m.text == "🔍 Check Driver")
async def ask_for_driver_id(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in settings.ADMIN_IDS:
        return await msg.answer("❌ Forbidden.")
    await state.set_state(AdminStates.search_id)
    await msg.answer("🔎 Please send the *Telegram ID* of the driver you want to inspect.", parse_mode="Markdown")

@router.message(AdminStates.search_id)
async def process_search_id(msg: types.Message, state: FSMContext):
    text = msg.text.strip()
    if not text.isdigit():
        return await msg.answer("❌ ID must be a number. Try again.")

    tg_id = int(text)
    # fetch driver in thread
    @sync_to_async(thread_sensitive=True)
    def _get_driver():
        return ActiveUser.objects.get(tg_id=tg_id)

    try:
        d = await _get_driver()
    except ActiveUser.DoesNotExist:
        await msg.answer("⚠️ No such driver.", reply_markup=admin_menu())
        await state.clear()
        return

    detail = (
        f"👤 Driver `{d.name}`\n"
        f"• Phone: `{d.phone}`\n"
        f"• Active: {'✅' if d.active else '❌'}\n"
        f"• Expires: `{d.expires_at.date() if d.expires_at else '—'}`"
    )
    buttons = []
    if not d.active:
        pass
    buttons.append([InlineKeyboardButton(text="⏩ Extend +30 days", callback_data=f"extend:{d.tg_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons, row_width=2)

    await msg.answer(detail, parse_mode="Markdown", reply_markup=kb)
    await state.clear()
# Expiration check task
def schedule_expiry_notifications():
    scheduler = AsyncIOScheduler()
    async def check_and_notify():
        now = timezone.now()
        expired = ActiveUser.objects.filter(active=True, expires_at__lte=now)
        for u in expired:
            kb = InlineKeyboardMarkup().row(
                InlineKeyboardButton(text="Extend", callback_data=f"extend:{u.id}"),
                InlineKeyboardButton(text="Deactivate", callback_data=f"deact:{u.id}")
            )
            text = f"⚠️ User {u.name} (ID {u.tg_id}) expired on {u.expires_at.date()}"
            for admin in settings.ADMIN_IDS:
                await bot.send_message(admin, text, reply_markup=kb)
    scheduler.add_job(check_and_notify, 'cron', day=1, hour=0, minute=0)
    scheduler.start()

@router.callback_query(lambda c: c.data and c.data.startswith(('extend:','deact:')))
async def cb_manage_user(cb: types.CallbackQuery):
    if cb.from_user.id not in settings.ADMIN_IDS:
        return await cb.answer("No access.", show_alert=True)
    action, uid = cb.data.split(':')
    u = await sync_to_async(ActiveUser.objects.get)(id=int(uid))
    if action == 'extend':
        u.expires_at += datetime.timedelta(days=30)
        u.save()
        await cb.message.edit_text(f"✅ Extended {u.name} until {u.expires_at.date()}")
    else:
        u.active = False
        u.save()
        await cb.message.edit_text(f"❌ Deactivated {u.name}")



def _get_data(ann_id: int):
    ann = (Announcement.objects
           .select_related("driver")
           .get(id=ann_id))
    driver = ann.driver
    session_path = os.path.join(SESSION_DIR, f"{driver.tg_id}.session")
    return {
        "session_path": session_path,
        "api_id":       driver.api_id,
        "api_hash":     driver.api_hash,
        "groups":       ann.groups,
        "text":         ann.text,
        "interval":     ann.interval_minutes,
        "active":       ann.active,
    }

# Posting loop (unchanged)--> omitted for brevity
async def post_loop(ann_id: int):
    data = await sync_to_async(_get_data, thread_sensitive=True)(ann_id)
    if not data["active"]:
        return

    os.makedirs(os.path.dirname(data["session_path"]), exist_ok=True)
    client = TelegramClient(
        data["session_path"],
        data["api_id"],
        data["api_hash"],
    )
    await client.start()

    for grp in data["groups"]:
        try:
            await client.send_message(grp, data["text"])
        except Exception as e:
            log.error("Telethon post to %s failed: %s", grp, e)

    await client.disconnect()
    await asyncio.sleep(data["interval"] * 60)
    # schedule next iteration:
    if data["active"]:
        asyncio.create_task(post_loop(ann_id))

# --- Main ---
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    schedule_expiry_notifications()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
