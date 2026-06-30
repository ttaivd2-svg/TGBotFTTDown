import telegram_handlers.message_handler as mh
import utils.health_server as hs
import config.settings as s
import drive.upload as up
import drive.client as cl
import asyncio

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if not s.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to the .env file.")
    if not cl.DRIVE_SERVICE:
        raise RuntimeError(
            "Google Drive is not configured. Add GOOGLE_DRIVE_FOLDER_ID, "
            "GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and "
            "GOOGLE_OAUTH_REFRESH_TOKEN to .env."
        )

    s.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    up.sync_state_from_drive()
    # hs.start_health_server()

    application = Application.builder().token(s.BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.ALL, mh.handle_message))

    hs.logger.info("Bot started. Temporary download directory: %s", s.DOWNLOAD_DIR)
    hs.logger.info("Google Drive upload: enabled")
    # application.run_polling(allowed_updates=Update.ALL_TYPES)
    application.run_webhook(
        listen="0.0.0.0", 
        port=s.PORT,
        url_path=s.BOT_TOKEN,
        webhook_url=f"{s.WEBHOOK_URL}/{s.BOT_TOKEN}",
        allowed_updates=Update.ALL_TYPES,
        )

if __name__ == "__main__":
    main()

    # URL твого Render сервісу
# application.run_webhook(

# listen="0.0.0.0",

# port=s.PORT,

# url_path=s.BOT_TOKEN,

# webhook_url=f"{s.WEBHOOK_URL}/{s.BOT_TOKEN}",

# allowed_updates=Update.ALL_TYPES,

# )
