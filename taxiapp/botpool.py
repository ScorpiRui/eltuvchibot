# taxiapp/botpool.py
import logging
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import CommandStart, Command

log = logging.getLogger(__name__)
bots: dict[str, Dispatcher] = {}          # token → Dispatcher (cache)

router = Router()

@router.message(CommandStart())
async def cmd_start(msg: types.Message):
    await msg.answer("👋 I’m your city‑to‑city taxi helper bot!")

@router.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.answer("Use /start to begin.\nSoon: /status, /pause …")

def get_dispatcher(token: str) -> Dispatcher:
    if token in bots:
        return bots[token]

    bot = Bot(token, parse_mode="HTML")
    dp  = Dispatcher()
    dp.include_router(router)

    dp["bot"] = bot          # ← ADD THIS LINE (store bot reference)

    bots[token] = dp
    log.info("Dispatcher created for %s", token[:10])
    return dp
