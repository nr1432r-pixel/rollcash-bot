import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN
from handlers.start import button, handle_message, start
from handlers.support import show_telegram_id


logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)


async def error_handler(update, context):
    logging.exception("Telegram bot error", exc_info=context.error)


def main():
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_telegram_id))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("RollCash Bot Started Successfully")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
