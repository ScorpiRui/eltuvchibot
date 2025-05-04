# city_taxi_project/urls.py
import logging
from django.contrib import admin
from django.urls    import path
from django.http    import JsonResponse, HttpResponse
from asgiref.sync   import sync_to_async
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from taxiapp.models   import Driver
from taxiapp.botpool  import get_dispatcher

log = logging.getLogger(__name__)

async def tg_webhook(request, token: str):
    if request.method != "POST":
        return HttpResponse(status=405)

    exists = await sync_to_async(
        lambda: Driver.objects.filter(bot_token=token, active=True).exists(),
        thread_sensitive=True,
    )()
    if not exists:
        return HttpResponse(status=404)

    try:
        update = types.Update.model_validate_json(request.body.decode())
    except Exception:
        return HttpResponse(status=400)

    dp  = get_dispatcher(token)
    bot = dp["bot"]

    try:
        await dp.feed_update(bot, update)
    except TelegramBadRequest as e:
        log.warning("Telegram API rejected the handler call: %s", e)
    except Exception:
        log.exception("Unexpected error in handler")

    return JsonResponse({"ok": True})

tg_webhook.csrf_exempt = True

urlpatterns = [
    path("admin/", admin.site.urls),
    path("webhook/<str:token>/", tg_webhook, name="tg_webhook"),
]
