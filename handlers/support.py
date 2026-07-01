from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from firebase_admin import firestore
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from config import ADMIN_TELEGRAM_ID
from firebase import db


logger = logging.getLogger(__name__)

SUPPORT_STATE_KEY = "support_state"
SUPPORT_USER_ID_KEY = "support_rollcash_user_id"
ADMIN_REPLY_TICKET_KEY = "admin_reply_ticket_id"
LANGUAGE_KEY = "language"

STATE_WAITING_USER_ID = "waiting_user_id"
STATE_WAITING_PROBLEM = "waiting_problem"

DATA_DIR = Path("data")
TICKET_STORE_PATH = DATA_DIR / "support_tickets.json"
ADMIN_CHAT_STORE_PATH = DATA_DIR / "admin_chat.json"
TICKET_LOCK = asyncio.Lock()
SUPPORT_TICKETS_COLLECTION = "support_tickets"
SUPPORT_META_COLLECTION = "support_meta"
SUPPORT_COUNTER_DOCUMENT = "tickets"


def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get(LANGUAGE_KEY, "hi")


def support_welcome_text(lang: str) -> str:
    if lang == "en":
        return (
            "👋 Welcome to RollCash Human Support.\n\n"
            "Please send your RollCash User ID.\n\n"
            "Example:\n"
            "RC-2NYJWB"
        )

    return (
        "👋 RollCash Human Support me aapka swagat hai.\n\n"
        "Kripya apna RollCash User ID bhejiye.\n\n"
        "Example:\n"
        "RC-2NYJWB"
    )


def support_problem_prompt(lang: str) -> str:
    if lang == "en":
        return (
            "✅ Your User ID has been received successfully.\n\n"
            "Now please write your problem in detail.\n\n"
            "Example:\n\n"
            "• My withdrawal is pending.\n"
            "• My coins were not added.\n"
            "• Wallet is not opening.\n"
            "• Daily rolls did not reset.\n"
            "• I am facing a login problem.\n"
            "• Or any other problem."
        )

    return (
        "✅ Aapka User ID safalta se receive ho gaya hai.\n\n"
        "Ab kripya apni problem detail me likhiye.\n\n"
        "Example:\n\n"
        "• Mera Withdrawal Pending hai.\n"
        "• Mere Coins Add nahi hue.\n"
        "• Wallet Open nahi ho raha.\n"
        "• Daily Rolls Reset nahi hue.\n"
        "• Login me problem aa rahi hai.\n"
        "• Ya koi aur problem."
    )


def support_success_text(ticket_id: str, lang: str) -> str:
    if lang == "en":
        return (
            "✅ Your Support Request has been received successfully.\n\n"
            "🎫 Ticket ID:\n\n"
            f"{ticket_id}\n\n"
            "Our Support Team will contact you soon.\n\n"
            "Thank you."
        )

    return (
        "✅ Aapki Support Request safalta se receive ho gayi hai.\n\n"
        "🎫 Ticket ID:\n\n"
        f"{ticket_id}\n\n"
        "Hamari Support Team jaldi hi aapse sampark karegi.\n\n"
        "Dhanyavaad."
    )


def support_closed_text(lang: str) -> str:
    if lang == "en":
        return (
            "✅ Your Support Request has been closed.\n\n"
            "Thank you for contacting RollCash Support.\n\n"
            "If the problem still remains, you can create a new request."
        )

    return (
        "✅ Aapki Support Request band kar di gayi hai.\n\n"
        "RollCash Support se sampark karne ke liye dhanyavaad.\n\n"
        "Agar phir bhi problem ho to nayi request bana sakte hain."
    )


def support_reply_text(message: str, lang: str) -> str:
    title = "📩 RollCash Support Reply" if lang == "en" else "📩 RollCash Support Reply"
    return f"{title}\n\n{message}"


def support_firebase_error_text(lang: str) -> str:
    if lang == "en":
        return (
            "❌ Support ticket could not be saved in Firebase.\n\n"
            "Please try again after some time."
        )

    return (
        "❌ Support ticket Firebase me save nahi ho paya.\n\n"
        "Kripya thodi der baad phir try karein."
    )


def admin_chat_ref() -> int | str | None:
    value = (ADMIN_TELEGRAM_ID or "").strip()
    if not value:
        logger.error("ADMIN_TELEGRAM_ID is missing.")
        return None

    if value.startswith("@"):
        cached_chat_id = read_cached_admin_chat_id(value)
        if cached_chat_id:
            return cached_chat_id
        return value

    try:
        return int(value)
    except ValueError:
        logger.error("ADMIN_TELEGRAM_ID must be a numeric ID or @username.")
        return None


def read_cached_admin_chat_id(admin_username: str) -> int | None:
    if not ADMIN_CHAT_STORE_PATH.exists():
        return None

    try:
        with ADMIN_CHAT_STORE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read admin chat cache.")
        return None

    if data.get("username", "").lower() != admin_username.removeprefix("@").lower():
        return None

    try:
        return int(data.get("chat_id"))
    except (TypeError, ValueError):
        return None


def remember_admin_chat(update: Update) -> None:
    configured_admin = (ADMIN_TELEGRAM_ID or "").strip()
    user = update.effective_user
    chat = update.effective_chat

    if not configured_admin.startswith("@") or not user or not chat or not user.username:
        return

    if user.username.lower() != configured_admin.removeprefix("@").lower():
        return

    DATA_DIR.mkdir(exist_ok=True)
    with ADMIN_CHAT_STORE_PATH.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "username": user.username,
                "user_id": user.id,
                "chat_id": chat.id,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            },
            file,
            ensure_ascii=False,
            indent=2,
        )


def is_admin(user_id: int | None, username: str | None = None) -> bool:
    admin_ref = admin_chat_ref()
    if isinstance(admin_ref, int):
        return user_id == admin_ref
    if isinstance(admin_ref, str):
        return bool(username and username.lower() == admin_ref.removeprefix("@").lower())
    return False


def admin_ticket_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Reply", callback_data=f"support_reply:{ticket_id}"),
                InlineKeyboardButton("❌ Close Ticket", callback_data=f"support_close:{ticket_id}"),
            ]
        ]
    )


def read_ticket_store() -> dict[str, Any]:
    if not TICKET_STORE_PATH.exists():
        return {"last_number": 0, "tickets": {}}

    try:
        with TICKET_STORE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read support ticket store.")
        return {"last_number": 0, "tickets": {}}

    data.setdefault("last_number", 0)
    data.setdefault("tickets", {})
    return data


def write_ticket_store(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with TICKET_STORE_PATH.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, default=str)


def save_ticket_to_firestore(ticket: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    transaction = db.transaction()
    counter_ref = db.collection(SUPPORT_META_COLLECTION).document(SUPPORT_COUNTER_DOCUMENT)

    @firestore.transactional
    def create_in_transaction(transaction):
        snapshot = counter_ref.get(transaction=transaction)
        counter_data = snapshot.to_dict() if snapshot.exists else {}
        next_number = int((counter_data or {}).get("last_number", 0) or 0) + 1
        ticket_id = f"RC-{next_number:06d}"

        ticket_ref = db.collection(SUPPORT_TICKETS_COLLECTION).document(ticket_id)
        firestore_payload = {
            **ticket,
            "ticket_id": ticket_id,
            "status": "open",
            "created_at_server": firestore.SERVER_TIMESTAMP,
            "updated_at_server": firestore.SERVER_TIMESTAMP,
        }
        local_payload = {
            **ticket,
            "ticket_id": ticket_id,
            "status": "open",
        }

        transaction.set(
            counter_ref,
            {
                "last_number": next_number,
                "updated_at_server": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        transaction.set(ticket_ref, firestore_payload)
        return ticket_id, local_payload

    return create_in_transaction(transaction)


def get_ticket_from_firestore(ticket_id: str) -> dict[str, Any] | None:
    snapshot = db.collection(SUPPORT_TICKETS_COLLECTION).document(ticket_id).get()
    if not snapshot.exists:
        return None
    return snapshot.to_dict() or None


def update_ticket_in_firestore(ticket_id: str, updates: dict[str, Any]) -> None:
    db.collection(SUPPORT_TICKETS_COLLECTION).document(ticket_id).set(
        {
            **updates,
            "updated_at_server": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )


def mirror_ticket_locally(ticket_id: str, ticket: dict[str, Any]) -> None:
    data = read_ticket_store()
    ticket_number = int(ticket_id.split("-")[-1]) if "-" in ticket_id else 0
    data["last_number"] = max(int(data.get("last_number", 0) or 0), ticket_number)
    data["tickets"][ticket_id] = ticket
    write_ticket_store(data)


async def create_ticket(ticket: dict[str, Any]) -> str:
    ticket_id, saved_ticket = await asyncio.to_thread(save_ticket_to_firestore, ticket)
    try:
        async with TICKET_LOCK:
            mirror_ticket_locally(ticket_id, saved_ticket)
    except Exception:
        logger.exception("Ticket %s saved to Firebase but local mirror failed.", ticket_id)
    return ticket_id


async def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    async with TICKET_LOCK:
        data = read_ticket_store()
        ticket = data.get("tickets", {}).get(ticket_id)
        if isinstance(ticket, dict):
            return ticket

    ticket = await asyncio.to_thread(get_ticket_from_firestore, ticket_id)
    if ticket:
        async with TICKET_LOCK:
            mirror_ticket_locally(ticket_id, ticket)
    return ticket


async def update_ticket(ticket_id: str, **updates: Any) -> dict[str, Any] | None:
    await asyncio.to_thread(update_ticket_in_firestore, ticket_id, updates)
    async with TICKET_LOCK:
        data = read_ticket_store()
        ticket = data.get("tickets", {}).get(ticket_id)
        if not isinstance(ticket, dict):
            ticket = await asyncio.to_thread(get_ticket_from_firestore, ticket_id)
            if not ticket:
                return None

        ticket.update(updates)
        mirror_ticket_locally(ticket_id, ticket)
        return ticket


def telegram_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Not available"

    full_name = user.full_name or "Not available"
    username = f"@{user.username}" if user.username else "No username"
    return f"{full_name} ({username})"


def format_admin_ticket(ticket: dict[str, Any]) -> str:
    return (
        "🚨 New Support Request\n\n"
        f"🎫 Ticket ID: {ticket['ticket_id']}\n"
        f"👤 RollCash User ID: {ticket['rollcash_user_id']}\n"
        f"👤 Telegram Name: {ticket['telegram_name']}\n"
        f"🆔 Telegram ID: {ticket['telegram_id']}\n"
        f"📝 User ki Problem:\n{ticket['problem']}\n\n"
        f"📅 Date: {ticket['date']}\n"
        f"🕒 Time: {ticket['time']}"
    )


async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[SUPPORT_STATE_KEY] = STATE_WAITING_USER_ID
    context.user_data.pop(SUPPORT_USER_ID_KEY, None)

    await update.effective_message.reply_text(support_welcome_text(get_lang(context)))


async def show_telegram_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return

    remember_admin_chat(update)

    username = f"@{user.username}" if user.username else "No username"
    await update.effective_message.reply_text(
        "🆔 Telegram ID Details\n\n"
        f"👤 Name: {user.full_name}\n"
        f"🔗 Username: {username}\n"
        f"🆔 User ID: {user.id}\n"
        f"💬 Chat ID: {chat.id}\n\n"
        "Admin notification ke liye .env me User ID use karein."
    )


async def handle_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    remember_admin_chat(update)

    query = update.callback_query
    if not query or not query.data:
        return False

    data = query.data
    if data == "support":
        try:
            await query.answer()
        except BadRequest:
            pass
        await start_support(update, context)
        return True

    if not data.startswith(("support_reply:", "support_close:")):
        return False

    try:
        await query.answer()
    except BadRequest:
        pass

    if not is_admin(query.from_user.id, query.from_user.username):
        await query.message.reply_text("❌ Aap is ticket action ke liye authorized nahi hain.")
        return True

    action, ticket_id = data.split(":", 1)
    ticket = await get_ticket(ticket_id)
    if not ticket:
        await query.message.reply_text("❌ Ticket nahi mila ya store corrupt hai.")
        return True

    if action == "support_reply":
        context.user_data[ADMIN_REPLY_TICKET_KEY] = ticket_id
        await query.message.reply_text(
            f"✍️ Ticket {ticket_id} ke liye reply likhiye.\n\n"
            "Admin ka next message user ko automatically send ho jayega."
        )
        return True

    await close_ticket(update, context, ticket_id, ticket)
    return True


async def close_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: str,
    ticket: dict[str, Any],
) -> None:
    await update_ticket(ticket_id, status="closed", closed_at=datetime.now().isoformat(timespec="seconds"))
    user_message = support_closed_text(ticket.get("language", "hi"))

    try:
        await context.bot.send_message(chat_id=int(ticket["user_chat_id"]), text=user_message)
    except (Forbidden, BadRequest, TelegramError):
        logger.exception("Could not notify user while closing ticket %s.", ticket_id)

    await update.effective_message.reply_text(f"✅ Ticket {ticket_id} close kar diya gaya hai.")


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> bool:
    if not is_admin(
        update.effective_user.id if update.effective_user else None,
        update.effective_user.username if update.effective_user else None,
    ):
        return False

    ticket_id = context.user_data.get(ADMIN_REPLY_TICKET_KEY)
    if not ticket_id:
        return False

    ticket = await get_ticket(ticket_id)
    if not ticket:
        context.user_data.pop(ADMIN_REPLY_TICKET_KEY, None)
        await update.message.reply_text("❌ Ticket nahi mila. Reply cancel kar diya gaya hai.")
        return True

    user_text = support_reply_text(message, ticket.get("language", "hi"))

    try:
        await context.bot.send_message(chat_id=int(ticket["user_chat_id"]), text=user_text)
    except (Forbidden, BadRequest, TelegramError):
        logger.exception("Could not send admin reply for ticket %s.", ticket_id)
        await update.message.reply_text("❌ User ko reply send nahi ho paya. Logs check karein.")
        return True

    context.user_data.pop(ADMIN_REPLY_TICKET_KEY, None)
    await update_ticket(
        ticket_id,
        status="replied",
        last_reply=message,
        last_replied_at=datetime.now().isoformat(timespec="seconds"),
    )
    await update.message.reply_text(f"✅ Reply ticket {ticket_id} ke user ko send ho gaya hai.")
    return True


async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    remember_admin_chat(update)

    message = (update.message.text or "").strip() if update.message else ""
    if not message:
        return False

    if await handle_admin_reply(update, context, message):
        return True

    state = context.user_data.get(SUPPORT_STATE_KEY)
    if not state:
        return False

    if state == STATE_WAITING_USER_ID:
        context.user_data[SUPPORT_USER_ID_KEY] = message
        context.user_data[SUPPORT_STATE_KEY] = STATE_WAITING_PROBLEM
        await update.message.reply_text(support_problem_prompt(get_lang(context)))
        return True

    if state == STATE_WAITING_PROBLEM:
        await submit_support_ticket(update, context, message)
        return True

    return False


async def submit_support_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    problem: str,
) -> None:
    admin_ref = admin_chat_ref()
    if not admin_ref:
        context.user_data.pop(SUPPORT_STATE_KEY, None)
        context.user_data.pop(SUPPORT_USER_ID_KEY, None)
        await update.message.reply_text(
            "❌ Support system abhi configure nahi hai.\n\n"
            "Admin ko ADMIN_TELEGRAM_ID .env me add karna hoga."
        )
        return

    now = datetime.now()
    ticket = {
        "rollcash_user_id": context.user_data.get(SUPPORT_USER_ID_KEY, "Not available"),
        "telegram_name": telegram_name(update),
        "telegram_id": update.effective_user.id if update.effective_user else "Not available",
        "user_chat_id": update.effective_chat.id if update.effective_chat else "Not available",
        "problem": problem,
        "language": get_lang(context),
        "date": now.strftime("%d-%m-%Y"),
        "time": now.strftime("%I:%M %p"),
        "created_at": now.isoformat(timespec="seconds"),
    }

    try:
        ticket_id = await create_ticket(ticket)
    except Exception:
        logger.exception("Could not save support ticket to Firebase.")
        context.user_data.pop(SUPPORT_STATE_KEY, None)
        context.user_data.pop(SUPPORT_USER_ID_KEY, None)
        await update.message.reply_text(support_firebase_error_text(get_lang(context)))
        return

    ticket["ticket_id"] = ticket_id

    await update.message.reply_text(support_success_text(ticket_id, get_lang(context)))

    try:
        await context.bot.send_message(
            chat_id=admin_ref,
            text=format_admin_ticket(ticket),
            reply_markup=admin_ticket_keyboard(ticket_id),
        )
    except (Forbidden, BadRequest, TelegramError):
        logger.exception("Could not send support ticket %s to admin.", ticket_id)

    context.user_data.pop(SUPPORT_STATE_KEY, None)
    context.user_data.pop(SUPPORT_USER_ID_KEY, None)
