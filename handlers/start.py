from __future__ import annotations

import asyncio
from datetime import datetime
from time import monotonic
from typing import Any

from google.cloud.firestore_v1.base_document import DocumentSnapshot
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from firebase import db
from handlers.support import handle_support_callback, handle_support_message
from keyboards.menu import language_menu, main_menu


USER_COLLECTIONS = ("users", "Users", "rollcash_users", "players", "customers")
USER_ID_FIELDS = ("displayId", "userId", "uid", "id", "telegramId", "telegram_id")
CURRENT_USER_ID_KEY = "current_user_id"
LANGUAGE_KEY = "language"
WITHDRAWAL_COLLECTIONS = ("withdrawals", "Withdrawals", "withdrawRequests", "payouts")
USER_LINK_FIELDS = ("userId", "uid", "user_id", "displayId")
CACHE_TTL_SECONDS = 20


USER_BUNDLE_CACHE: dict[str, tuple[float, DocumentSnapshot | None, dict[str, Any] | None]] = {}


def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get(LANGUAGE_KEY, "hi")


def line_items(items: list[str]) -> str:
    return "\n".join(f"• {item}" for item in items)


def language_prompt(first_name: str | None = None) -> str:
    name = first_name or "User"
    return (
        f"👋 Welcome {name}\n\n"
        "Please choose your language.\n"
        "कृपया अपनी भाषा चुनें।"
    )


def clean(value: Any, default: str = "Not available") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %I:%M %p")
    return str(value)


def get_field(data: dict[str, Any], *names: str, default: str = "Not available") -> str:
    for name in names:
        if name in data and data[name] not in (None, ""):
            return clean(data[name], default)
    return default


def find_user(user_id: str) -> tuple[DocumentSnapshot | None, dict[str, Any] | None]:
    try:
        for collection in USER_COLLECTIONS:
            snapshot = db.collection(collection).document(user_id).get()
            if snapshot.exists:
                return snapshot, snapshot.to_dict() or {}

            for field in USER_ID_FIELDS:
                matches = (
                    db.collection(collection)
                    .where(field, "==", user_id)
                    .limit(1)
                    .stream()
                )

                for matched_snapshot in matches:
                    return matched_snapshot, matched_snapshot.to_dict() or {}
    except Exception:
        return None, None

    return None, None


def user_lookup_ids(user_id: str, data: dict[str, Any]) -> list[str]:
    ids = [
        user_id,
        get_field(data, "uid", default=""),
        get_field(data, "displayId", default=""),
        get_field(data, "userId", default=""),
        get_field(data, "telegramId", "telegram_id", default=""),
    ]

    clean_ids = []
    for item in ids:
        if item and item not in ("Not available", "None") and item not in clean_ids:
            clean_ids.append(item)
    return clean_ids


def find_user_withdrawals(user_id: str, data: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    ids = user_lookup_ids(user_id, data)
    withdrawals: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for collection in WITHDRAWAL_COLLECTIONS:
            for link_field in USER_LINK_FIELDS:
                for lookup_id in ids:
                    matches = (
                        db.collection(collection)
                        .where(link_field, "==", lookup_id)
                        .limit(limit)
                        .stream()
                    )

                    for snapshot in matches:
                        if snapshot.id in seen:
                            continue

                        item = snapshot.to_dict() or {}
                        item["_id"] = snapshot.id
                        withdrawals.append(item)
                        seen.add(snapshot.id)
    except Exception:
        pass

    withdrawals.sort(
        key=lambda item: item.get("timestamp")
        or item.get("createdAt")
        or item.get("paidAt")
        or datetime.min,
        reverse=True,
    )
    return withdrawals[:limit]


def find_user_history(snapshot: DocumentSnapshot | None, limit: int = 5) -> list[dict[str, Any]]:
    if snapshot is None:
        return []

    history: list[dict[str, Any]] = []

    try:
        for item in snapshot.reference.collection("history").limit(limit).stream():
            data = item.to_dict() or {}
            data["_id"] = item.id
            history.append(data)
    except Exception:
        return []

    return history


def load_user_bundle(user_id: str) -> tuple[DocumentSnapshot | None, dict[str, Any] | None]:
    snapshot, data = find_user(user_id)
    if not snapshot or data is None:
        return snapshot, data

    data["_withdrawals"] = find_user_withdrawals(user_id, data)
    data["_history"] = find_user_history(snapshot)
    return snapshot, data


async def run_db_call(func, *args):
    return await asyncio.to_thread(func, *args)


async def load_user_bundle_fast(user_id: str) -> tuple[DocumentSnapshot | None, dict[str, Any] | None]:
    now = monotonic()
    cached = USER_BUNDLE_CACHE.get(user_id)

    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], cached[2]

    snapshot, data = await run_db_call(load_user_bundle, user_id)
    USER_BUNDLE_CACHE[user_id] = (monotonic(), snapshot, data)
    return snapshot, data


async def warm_user_bundle(user_id: str):
    try:
        await load_user_bundle_fast(user_id)
    except Exception:
        pass


def latest_withdrawal(data: dict[str, Any]) -> dict[str, Any]:
    withdrawals = data.get("_withdrawals")
    if isinstance(withdrawals, list) and withdrawals:
        return withdrawals[0]
    return {}


def format_user_summary(user_id: str, data: dict[str, Any], lang: str = "hi") -> str:
    display_id = get_field(data, "displayId", default=user_id)
    uid = get_field(data, "uid")
    name = get_field(data, "name", "fullName", "username", "displayName")
    email = get_field(data, "email", "mail")
    coin_balance = get_field(data, "coinBalance", "coins", "coin", "totalCoins", "balanceCoins", default="0")
    total_earned = get_field(data, "totalCoinsEarned", "earnedCoins", "totalEarned", "lifetimeCoins", default="0")
    today_rolls = get_field(data, "todayRolls", "dailyRolls", default="0")
    total_rolls = get_field(data, "totalRolls", "rolls", "rollCount", default="0")
    upi_id = get_field(data, "upiId", "upi", "paymentMethod")
    join_date = get_field(data, "joinDate", "createdAt")
    last_login = get_field(data, "lastLogin")
    last_roll_reset = get_field(data, "lastRollReset")
    photo_url = get_field(data, "photoUrl", "photoURL", "avatar")
    withdrawal = latest_withdrawal(data)
    withdraw_amount = get_field(withdrawal, "amount", "withdrawAmount", default="Not available")
    withdraw_status = get_field(withdrawal, "status", "withdrawStatus", "payoutStatus")
    withdraw_time = get_field(withdrawal, "timestamp", "createdAt")
    paid_at = get_field(withdrawal, "paidAt")

    if lang == "en":
        return (
            "✅ User data found\n\n"
            f"🆔 User ID: {display_id}\n"
            f"🔐 UID: {uid}\n"
            f"👤 Name: {name}\n"
            f"📧 Email: {email}\n"
            f"🪙 Coin balance: {coin_balance}\n"
            f"💰 Total coins earned: {total_earned}\n"
            f"🎲 Today rolls: {today_rolls}\n"
            f"📊 Total rolls: {total_rolls}\n"
            f"💸 UPI ID: {upi_id}\n"
            f"🏦 Latest withdraw amount: {withdraw_amount}\n"
            f"📌 Latest withdraw status: {withdraw_status}\n"
            f"🕘 Withdraw requested: {withdraw_time}\n"
            f"✅ Paid at: {paid_at}\n"
            f"📅 Join date: {join_date}\n"
            f"🕒 Last login: {last_login}\n"
            f"🔄 Last roll reset: {last_roll_reset}\n"
            f"🖼 Photo: {photo_url}\n\n"
            "Now you can ask: wallet, coins, withdraw, profile, history, rolls, FAQ."
        )

    return (
        "✅ User data found\n\n"
        f"🆔 User ID: {display_id}\n"
        f"🔐 UID: {uid}\n"
        f"👤 Name: {name}\n"
        f"📧 Email: {email}\n"
        f"🪙 Coin balance: {coin_balance}\n"
        f"💰 Total coins earned: {total_earned}\n"
        f"🎲 Today rolls: {today_rolls}\n"
        f"📊 Total rolls: {total_rolls}\n"
        f"💸 UPI ID: {upi_id}\n"
        f"🏦 Latest withdraw amount: {withdraw_amount}\n"
        f"📌 Latest withdraw status: {withdraw_status}\n"
        f"🕘 Withdraw requested: {withdraw_time}\n"
        f"✅ Paid at: {paid_at}\n"
        f"📅 Join date: {join_date}\n"
        f"🕒 Last login: {last_login}\n"
        f"🔄 Last roll reset: {last_roll_reset}\n"
        f"🖼 Photo: {photo_url}\n\n"
        "Ab aap pooch sakte ho: wallet, coins, withdraw, profile, history, rolls, FAQ."
    )


def format_wallet(data: dict[str, Any], lang: str = "hi") -> str:
    coin_balance = get_field(data, "coinBalance", "coins", "coin", "totalCoins", "balanceCoins", default="0")
    total_earned = get_field(data, "totalCoinsEarned", "earnedCoins", "totalEarned", default="0")
    upi_id = get_field(data, "upiId", "upi", "paymentMethod")
    payout = get_field(data, "payoutStatus", "withdrawStatus", "withdrawal")

    if lang == "en":
        return (
            "💰 Wallet details\n\n"
            f"Coin balance: {coin_balance}\n"
            f"Total coins earned: {total_earned}\n"
            f"UPI ID: {upi_id}\n"
            f"Payout status: {payout}"
        )

    return (
        "💰 Wallet details\n\n"
        f"Coin balance: {coin_balance}\n"
        f"Total coins earned: {total_earned}\n"
        f"UPI ID: {upi_id}\n"
        f"Payout status: {payout}"
    )


def format_coins(data: dict[str, Any], lang: str = "hi") -> str:
    coin_balance = get_field(data, "coinBalance", "coins", "coin", "totalCoins", "balanceCoins", default="0")
    earned = get_field(data, "totalCoinsEarned", "earnedCoins", "totalEarned", "lifetimeCoins", default="0")
    today_rolls = get_field(data, "todayRolls", "dailyRolls", default="0")
    total_rolls = get_field(data, "totalRolls", "rolls", "rollCount", default="0")

    if lang == "en":
        return (
            "🪙 Coins details\n\n"
            f"Available coins: {coin_balance}\n"
            f"Total coins earned: {earned}\n"
            f"Today rolls: {today_rolls}\n"
            f"Total rolls: {total_rolls}"
        )

    return (
        "🪙 Coins details\n\n"
        f"Available coins: {coin_balance}\n"
        f"Total coins earned: {earned}\n"
        f"Today rolls: {today_rolls}\n"
        f"Total rolls: {total_rolls}"
    )


def format_withdraw(data: dict[str, Any], lang: str = "hi") -> str:
    withdrawal = latest_withdrawal(data)
    status = get_field(withdrawal, "status", "withdrawStatus", "withdrawal", "payoutStatus")
    amount = get_field(withdrawal, "amount", "withdrawAmount", "lastWithdrawAmount", "payoutAmount")
    upi_id = get_field(withdrawal, "upiId", "withdrawMethod", "paymentMethod", "upi", "walletAddress", default=get_field(data, "upiId", "upi"))
    timestamp = get_field(withdrawal, "timestamp", "createdAt")
    paid_at = get_field(withdrawal, "paidAt")
    request_id = get_field(withdrawal, "_id")
    coin_balance = get_field(data, "coinBalance", default="0")

    if lang == "en":
        return (
            "💸 Withdraw details\n\n"
            f"Request ID: {request_id}\n"
            f"Status: {status}\n"
            f"Amount: {amount}\n"
            f"UPI ID: {upi_id}\n"
            f"Requested at: {timestamp}\n"
            f"Paid at: {paid_at}\n"
            f"Current coin balance: {coin_balance}"
        )

    return (
        "💸 Withdraw details\n\n"
        f"Request ID: {request_id}\n"
        f"Status: {status}\n"
        f"Amount: {amount}\n"
        f"UPI ID: {upi_id}\n"
        f"Requested at: {timestamp}\n"
        f"Paid at: {paid_at}\n"
        f"Current coin balance: {coin_balance}"
    )


def has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def is_available(value: str) -> bool:
    return value not in ("", "0", "Not available", "None")


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mask_email(email: str) -> str:
    if "@" not in email or not is_available(email):
        return email

    name, domain = email.split("@", 1)
    if len(name) <= 2:
        return f"{name[:1]}***@{domain}"
    return f"{name[:2]}***@{domain}"


def mask_upi(upi_id: str) -> str:
    if "@" not in upi_id or not is_available(upi_id):
        return upi_id

    name, bank = upi_id.split("@", 1)
    if len(name) <= 4:
        return f"{name[:1]}***@{bank}"
    return f"{name[:3]}***{name[-2:]}@{bank}"


def format_account_health(data: dict[str, Any], lang: str = "hi") -> str:
    issues = []
    good = []

    email = get_field(data, "email", "mail")
    upi_id = get_field(data, "upiId", "upi")
    coin_balance = to_int(data.get("coinBalance"))
    today_rolls = to_int(data.get("todayRolls"))
    total_rolls = to_int(data.get("totalRolls"))
    last_login = get_field(data, "lastLogin")
    last_roll_reset = get_field(data, "lastRollReset")
    withdrawal = latest_withdrawal(data)
    withdraw_status = get_field(withdrawal, "status", "withdrawStatus", "payoutStatus")
    withdraw_amount = get_field(withdrawal, "amount", "withdrawAmount")

    if lang == "en":
        if is_available(email):
            good.append("Email is linked")
        else:
            issues.append("Email is missing")

        if is_available(upi_id):
            good.append("UPI ID is added")
        else:
            issues.append("UPI ID is missing, withdrawal may have issues")

        if coin_balance > 0:
            good.append(f"Coin balance available: {coin_balance}")
        else:
            issues.append("Coin balance is 0")

        if today_rolls > 0:
            good.append(f"Rolls used today: {today_rolls}")
        else:
            issues.append("No roll activity found today")

        if total_rolls > 0:
            good.append(f"Total rolls: {total_rolls}")

        if is_available(withdraw_status):
            if "pending" in withdraw_status.lower():
                issues.append(f"Latest withdraw is pending: {withdraw_amount}")
            else:
                good.append(f"Latest withdraw status: {withdraw_status}")

        good_text = "\n".join(f"✅ {item}" for item in good) or "✅ Basic account data is available"
        issue_text = "\n".join(f"⚠️ {item}" for item in issues) or "✅ No obvious issue found"

        return (
            "🩺 Account Health Check\n\n"
            f"{good_text}\n\n"
            f"{issue_text}\n\n"
            f"Last login: {last_login}\n"
            f"Last roll reset: {last_roll_reset}"
        )

    if is_available(email):
        good.append("Email linked hai")
    else:
        issues.append("Email missing hai")

    if is_available(upi_id):
        good.append("UPI ID added hai")
    else:
        issues.append("UPI ID missing hai, withdraw me issue aa sakta hai")

    if coin_balance > 0:
        good.append(f"Coin balance available hai: {coin_balance}")
    else:
        issues.append("Coin balance 0 hai")

    if today_rolls > 0:
        good.append(f"Aaj rolls use hue hain: {today_rolls}")
    else:
        issues.append("Aaj rolls activity nahi dikh rahi")

    if total_rolls > 0:
        good.append(f"Total rolls: {total_rolls}")

    if is_available(withdraw_status):
        if "pending" in withdraw_status.lower():
            issues.append(f"Latest withdraw pending hai: {withdraw_amount}")
        else:
            good.append(f"Latest withdraw status: {withdraw_status}")

    good_text = "\n".join(f"✅ {item}" for item in good) or "✅ Basic account data available hai"
    issue_text = "\n".join(f"⚠️ {item}" for item in issues) or "✅ Koi obvious issue nahi dikh raha"

    return (
        "🩺 Account Health Check\n\n"
        f"{good_text}\n\n"
        f"{issue_text}\n\n"
        f"Last login: {last_login}\n"
        f"Last roll reset: {last_roll_reset}"
    )


def format_smart_tips(data: dict[str, Any], lang: str = "hi") -> str:
    coin_balance = to_int(data.get("coinBalance"))
    today_rolls = to_int(data.get("todayRolls"))
    upi_id = get_field(data, "upiId", "upi")

    if lang == "en":
        tips = [
            "Use daily rolls regularly to grow coins faster.",
            "Verify your UPI ID once before requesting withdrawal.",
            "If payment is pending, contact support with the same user ID.",
        ]

        if coin_balance <= 0:
            tips.insert(0, "Coin balance is low, complete rolls/tasks first.")
        if today_rolls == 0:
            tips.insert(0, "No roll activity found today, check Daily Rolls.")
        if not is_available(upi_id):
            tips.insert(0, "UPI ID is missing, add it before withdrawal.")

        return "💡 Smart Tips\n\n" + line_items(tips[:5])

    tips = [
        "Daily rolls regularly use karo, coins fast grow honge.",
        "Withdraw se pehle UPI ID ek baar verify kar lo.",
        "Agar payment pending ho to same user ID ke saath support ko message karo.",
    ]

    if coin_balance <= 0:
        tips.insert(0, "Coin balance low hai, pehle rolls/tasks complete karo.")
    if today_rolls == 0:
        tips.insert(0, "Aaj rolls activity nahi dikh rahi, Daily Rolls check karo.")
    if not is_available(upi_id):
        tips.insert(0, "UPI ID missing hai, withdraw se pehle add karna zaroori hai.")

    return "💡 Smart Tips\n\n" + "\n".join(f"• {tip}" for tip in tips[:5])


def commands_text(lang: str = "hi") -> str:
    if lang == "en":
        return (
            "📋 Smart Commands\n\n"
            "You can ask in natural language too:\n\n"
            "• When will my money arrive?\n"
            "• Is my withdrawal pending?\n"
            "• How many coins do I have?\n"
            "• How many rolls happened today?\n"
            "• Check my account\n"
            "• Is there any problem?\n"
            "• Give me tips\n"
            "• Show full data\n"
            "• change id"
        )

    return (
        "📋 Smart Commands\n\n"
        "Aap natural language me bhi pooch sakte ho:\n\n"
        "• Mera rupiya kab aayega?\n"
        "• Withdraw pending hai kya?\n"
        "• Mere coins kitne hain?\n"
        "• Aaj kitne rolls hue?\n"
        "• Mera account check karo\n"
        "• Koi problem hai kya?\n"
        "• Tips do\n"
        "• Sab data dikhao\n"
        "• change id"
    )


def format_full_summary(user_id: str, data: dict[str, Any], lang: str = "hi") -> str:
    summary = format_user_summary(user_id, data, lang)
    health = format_account_health(data, lang)
    tips = format_smart_tips(data, lang)

    return f"{summary}\n\n━━━━━━━━━━━━━━\n\n{health}\n\n━━━━━━━━━━━━━━\n\n{tips}"


def format_payment_question(data: dict[str, Any], lang: str = "hi") -> str:
    withdrawal = latest_withdrawal(data)
    status = get_field(withdrawal, "status", "withdrawStatus", "withdrawal", "payoutStatus")
    amount = get_field(withdrawal, "amount", "withdrawAmount", "lastWithdrawAmount", "payoutAmount")
    upi_id = get_field(withdrawal, "upiId", "withdrawMethod", "paymentMethod", "upi", "walletAddress", default=get_field(data, "upiId", "upi"))
    timestamp = get_field(withdrawal, "timestamp", "createdAt")
    paid_at = get_field(withdrawal, "paidAt")
    coin_balance = get_field(data, "coinBalance", default="0")
    last_login = get_field(data, "lastLogin")

    status_text = status.lower()

    if lang == "en":
        if "pending" in status_text:
            answer = (
                "Your withdrawal is currently showing as pending. "
                "The payment team will process it after verification."
            )
        elif any(word in status_text for word in ("paid", "success", "completed", "done", "approved")):
            answer = "Your withdrawal is showing as completed/paid."
        elif any(word in status_text for word in ("reject", "failed", "cancel")):
            answer = "Your withdrawal is showing as failed/rejected. Please check UPI ID and details."
        elif is_available(status):
            answer = f"Current withdrawal status: {status}."
        else:
            answer = (
                "Withdrawal request status is not available in the database right now. "
                "If you already requested withdrawal, please confirm with support."
            )

        return (
            f"💸 {answer}\n\n"
            f"Amount: {amount}\n"
            f"UPI ID: {upi_id}\n"
            f"Requested at: {timestamp}\n"
            f"Paid at: {paid_at}\n"
            f"Current coin balance: {coin_balance}\n"
            f"Last login: {last_login}\n\n"
            "If payment is pending, please check whether your UPI ID is correct."
        )

    if "pending" in status_text:
        answer = (
            "Bhai aapka withdraw abhi pending dikh raha hai. "
            "Payment team verification ke baad process karegi."
        )
    elif any(word in status_text for word in ("paid", "success", "completed", "done", "approved")):
        answer = "Bhai aapka withdraw completed/paid dikh raha hai."
    elif any(word in status_text for word in ("reject", "failed", "cancel")):
        answer = "Bhai aapka withdraw failed/rejected dikh raha hai. UPI ID aur details check kar lo."
    elif is_available(status):
        answer = f"Bhai withdraw ka current status: {status}."
    else:
        answer = (
            "Bhai database me withdraw request ka status abhi available nahi hai. "
            "Agar aapne request dali hai to support team se confirm karna padega."
        )

    return (
        f"💸 {answer}\n\n"
        f"Amount: {amount}\n"
        f"UPI ID: {upi_id}\n"
        f"Requested at: {timestamp}\n"
        f"Paid at: {paid_at}\n"
        f"Current coin balance: {coin_balance}\n"
        f"Last login: {last_login}\n\n"
        "Agar payment pending hai, please UPI ID sahi hai ya nahi check kar lo."
    )


def format_natural_reply(message: str, user_id: str, data: dict[str, Any]) -> str | None:
    text = message.lower()
    name = get_field(data, "name", "fullName", "username", "displayName", default="bhai")
    display_id = get_field(data, "displayId", default=user_id)

    if has_any(text, ("rupiya", "rupee", "paisa", "paise", "payment", "payout", "withdraw", "withdrow", "nikal", "pending", "kab aayega", "kab milega")):
        return format_payment_question(data)

    if has_any(text, ("problem", "issue", " dikkat", "dikkt", "health", "check", "safe", "sahi hai", "account check")):
        return format_account_health(data)

    if has_any(text, ("tips", "tip", "suggest", "suggestion", "kya karu", "kaise badhe", "kaise badhau")):
        return format_smart_tips(data)

    if has_any(text, ("command", "commands", "kya puch", "kya pooch", "help menu")):
        return commands_text()

    if has_any(text, ("sab data", "full data", "summary", "poora data", "pura data", "all data")):
        return format_full_summary(user_id, data)

    if has_any(text, ("kitna coin", "mere coin", "coins kitne", "coin balance", "balance kitna")):
        coin_balance = get_field(data, "coinBalance", default="0")
        total_earned = get_field(data, "totalCoinsEarned", default="0")
        return (
            f"🪙 {name}, aapka current coin balance {coin_balance} hai.\n"
            f"Total earned coins: {total_earned}."
        )

    if has_any(text, ("mera account", "meri id", "profile", "details", "data")):
        return format_profile(user_id, data)

    if has_any(text, ("roll bacha", "rolls bache", "today roll", "aaj roll", "daily roll")):
        today_rolls = get_field(data, "todayRolls", default="0")
        total_rolls = get_field(data, "totalRolls", default="0")
        last_roll_reset = get_field(data, "lastRollReset")
        return (
            "🎲 Roll details\n\n"
            f"Today rolls: {today_rolls}\n"
            f"Total rolls: {total_rolls}\n"
            f"Last roll reset: {last_roll_reset}"
        )

    if has_any(text, ("hello", "hi", "hii", "hey", "namaste")):
        return (
            f"Hi {name} 👋\n\n"
            f"Aapka RollCash ID {display_id} set hai. "
            "Aap wallet, coins, withdraw, rolls, profile ya history ke baare me pooch sakte ho."
        )

    if has_any(text, ("thank", "thanks", "dhanyawad", "shukriya")):
        return "Welcome bhai 😊 Aur kuch check karna ho to bolo."

    return None


def format_profile(user_id: str, data: dict[str, Any]) -> str:
    display_id = get_field(data, "displayId", default=user_id)
    uid = get_field(data, "uid")
    name = get_field(data, "name", "fullName", "username", "displayName")
    email = get_field(data, "email", "mail")
    phone = get_field(data, "phone", "mobile", "phoneNumber")
    upi_id = get_field(data, "upiId", "upi")
    join_date = get_field(data, "joinDate", "createdAt")
    last_login = get_field(data, "lastLogin")
    photo_url = get_field(data, "photoUrl", "photoURL", "avatar")
    status = get_field(data, "status", "accountStatus", default="Active")

    return (
        "👤 Profile\n\n"
        f"User ID: {display_id}\n"
        f"UID: {uid}\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Phone: {phone}\n"
        f"UPI ID: {upi_id}\n"
        f"Join date: {join_date}\n"
        f"Last login: {last_login}\n"
        f"Photo: {photo_url}\n"
        f"Status: {status}"
    )


def format_history(data: dict[str, Any]) -> str:
    history = data.get("history") or data.get("rollHistory") or data.get("transactions")
    sub_history = data.get("_history")
    withdrawals = data.get("_withdrawals")

    if isinstance(history, list) and history:
        lines = []
        for item in history[-5:]:
            lines.append(f"• {clean(item)}")
        return "📜 Recent history\n\n" + "\n".join(lines)

    if isinstance(sub_history, list) and sub_history:
        lines = []
        for item in sub_history[:5]:
            label = get_field(item, "type", "title", "name", default="History item")
            amount = get_field(item, "amount", "coins", "coinBalance")
            time = get_field(item, "timestamp", "createdAt", "date")
            lines.append(f"• {label} | {amount} | {time}")
        return "📜 Recent history\n\n" + "\n".join(lines)

    if isinstance(withdrawals, list) and withdrawals:
        lines = []
        for item in withdrawals[:5]:
            amount = get_field(item, "amount", default="Not available")
            status = get_field(item, "status", default="Not available")
            time = get_field(item, "timestamp", "createdAt")
            lines.append(f"• Withdraw {amount} | {status} | {time}")
        return "📜 Recent withdrawal history\n\n" + "\n".join(lines)

    return (
        "📜 History\n\n"
        f"Join date: {get_field(data, 'joinDate', 'createdAt')}\n"
        f"Last login: {get_field(data, 'lastLogin')}\n"
        f"Last roll reset: {get_field(data, 'lastRollReset')}\n\n"
        "Detailed transaction history abhi database me list form me available nahi hai."
    )


def format_rolls(data: dict[str, Any]) -> str:
    today_rolls = get_field(data, "todayRolls", "dailyRolls", default="0")
    total_rolls = get_field(data, "totalRolls", "rolls", "rollCount", default="0")
    last_roll = get_field(data, "lastRoll", "lastRollAt", "lastDailyRoll")
    last_roll_reset = get_field(data, "lastRollReset")

    return (
        "🎲 Daily Rolls\n\n"
        f"Today rolls: {today_rolls}\n"
        f"Total rolls: {total_rolls}\n"
        f"Last roll: {last_roll}\n"
        f"Last roll reset: {last_roll_reset}"
    )


def faq_text() -> str:
    return (
        "❓ FAQ\n\n"
        "Q. User data kaise dekhein?\n"
        "A. Pehle RollCash user ID/displayId bhejo. Example: RC-2xxxxB\n\n"
        "Q. User ka coins kaise dekhein?\n"
        "A. User ID set karne ke baad Coins button dabao ya 'coins' type karo.\n\n"
        "Q. Withdraw info kaise check karein?\n"
        "A. User ID set karne ke baad Withdraw button dabao."
    )


def support_text() -> str:
    return (
        "👨‍💼 Human Support\n\n"
        "Agar bot answer na de paye to support team se contact karo:\n"
        "@RollCashSupport"
    )


def welcome_text(first_name: str) -> str:
    return (
        f"👋 Welcome {first_name}\n\n"
        "🎲 RollCash Smart Support Assistant\n\n"
        "Please apna RollCash user ID bhejo.\n"
        "Example: RC-2NYJWB\n\n"
        "ID milne ke baad main full data, withdraw status, coins, rolls, account health aur smart tips dikha dunga."
    )


def welcome_text_localized(first_name: str, lang: str = "hi") -> str:
    if lang == "en":
        return (
            f"👋 Welcome {first_name}\n\n"
            "🎲 RollCash Smart Support Assistant\n\n"
            "Please send your RollCash user ID.\n"
            "Example: RC-2NYJWB\n\n"
            "After receiving the ID, I can show full data, withdrawal status, coins, rolls, account health, and smart tips."
        )

    return (
        f"👋 स्वागत है {first_name}\n\n"
        "🎲 RollCash Smart Support Assistant\n\n"
        "कृपया अपना RollCash user ID भेजें।\n"
        "Example: RC-2NYJWB\n\n"
        "ID मिलने के बाद मैं full data, withdraw status, coins, rolls, account health और smart tips दिखा दूंगा।"
    )


def not_found_text(lang: str = "hi") -> str:
    if lang == "en":
        return (
            "❌ Data was not found for this user ID.\n\n"
            "Please send the correct RollCash user ID.\n"
            "Example: RC-2NYJWB"
        )

    return (
        "❌ इस user ID का data नहीं मिला।\n\n"
        "कृपया सही RollCash user ID भेजें।\n"
        "Example: RC-2NYJWB"
    )


def change_id_text(lang: str = "hi") -> str:
    if lang == "en":
        return "Okay, please send the new RollCash user ID."
    return "ठीक है भाई, नया RollCash user ID भेजो।"


def saved_id_missing_text(lang: str = "hi") -> str:
    if lang == "en":
        return "Saved user ID data is not available now. Please send the user ID again."
    return "Saved user ID का data अब नहीं मिल रहा। कृपया user ID दोबारा भेजें।"


def format_user_summary_localized(user_id: str, data: dict[str, Any], lang: str = "hi") -> str:
    if lang == "en":
        return format_user_summary(user_id, data, lang)

    display_id = get_field(data, "displayId", default=user_id)
    uid = get_field(data, "uid")
    name = get_field(data, "name", "fullName", "username", "displayName")
    email = get_field(data, "email", "mail")
    coin_balance = get_field(data, "coinBalance", "coins", "coin", "totalCoins", "balanceCoins", default="0")
    total_earned = get_field(data, "totalCoinsEarned", "earnedCoins", "totalEarned", "lifetimeCoins", default="0")
    today_rolls = get_field(data, "todayRolls", "dailyRolls", default="0")
    total_rolls = get_field(data, "totalRolls", "rolls", "rollCount", default="0")
    upi_id = get_field(data, "upiId", "upi", "paymentMethod")
    join_date = get_field(data, "joinDate", "createdAt")
    last_login = get_field(data, "lastLogin")
    last_roll_reset = get_field(data, "lastRollReset")
    withdrawal = latest_withdrawal(data)
    withdraw_amount = get_field(withdrawal, "amount", "withdrawAmount", default="Not available")
    withdraw_status = get_field(withdrawal, "status", "withdrawStatus", "payoutStatus")

    return (
        "✅ User data मिल गया\n\n"
        f"🆔 User ID: {display_id}\n"
        f"🔐 UID: {uid}\n"
        f"👤 नाम: {name}\n"
        f"📧 Email: {email}\n"
        f"🪙 Coin balance: {coin_balance}\n"
        f"💰 Total earned coins: {total_earned}\n"
        f"🎲 आज के rolls: {today_rolls}\n"
        f"📊 Total rolls: {total_rolls}\n"
        f"💸 UPI ID: {upi_id}\n"
        f"🏦 Latest withdraw amount: {withdraw_amount}\n"
        f"📌 Latest withdraw status: {withdraw_status}\n"
        f"📅 Join date: {join_date}\n"
        f"🕒 Last login: {last_login}\n"
        f"🔄 Last roll reset: {last_roll_reset}\n\n"
        "अब आप wallet, coins, withdraw, profile, history, rolls या FAQ पूछ सकते हैं।"
    )


def format_wallet_localized(data: dict[str, Any], lang: str = "hi") -> str:
    if lang == "en":
        return format_wallet(data, lang)

    coin_balance = get_field(data, "coinBalance", "coins", "coin", "totalCoins", "balanceCoins", default="0")
    total_earned = get_field(data, "totalCoinsEarned", "earnedCoins", "totalEarned", default="0")
    upi_id = get_field(data, "upiId", "upi", "paymentMethod")
    payout = get_field(data, "payoutStatus", "withdrawStatus", "withdrawal")

    return (
        "💰 Wallet details\n\n"
        f"Coin balance: {coin_balance}\n"
        f"Total earned coins: {total_earned}\n"
        f"UPI ID: {upi_id}\n"
        f"Payout status: {payout}"
    )


def format_coins_localized(data: dict[str, Any], lang: str = "hi") -> str:
    if lang == "en":
        return format_coins(data, lang)

    coin_balance = get_field(data, "coinBalance", "coins", "coin", "totalCoins", "balanceCoins", default="0")
    earned = get_field(data, "totalCoinsEarned", "earnedCoins", "totalEarned", "lifetimeCoins", default="0")
    today_rolls = get_field(data, "todayRolls", "dailyRolls", default="0")
    total_rolls = get_field(data, "totalRolls", "rolls", "rollCount", default="0")

    return (
        "🪙 Coins details\n\n"
        f"Available coins: {coin_balance}\n"
        f"Total earned coins: {earned}\n"
        f"आज के rolls: {today_rolls}\n"
        f"Total rolls: {total_rolls}"
    )


def format_withdraw_localized(data: dict[str, Any], lang: str = "hi") -> str:
    if lang == "en":
        return format_withdraw(data, lang)

    withdrawal = latest_withdrawal(data)
    status = get_field(withdrawal, "status", "withdrawStatus", "withdrawal", "payoutStatus")
    amount = get_field(withdrawal, "amount", "withdrawAmount", "lastWithdrawAmount", "payoutAmount")
    upi_id = get_field(withdrawal, "upiId", "withdrawMethod", "paymentMethod", "upi", "walletAddress", default=get_field(data, "upiId", "upi"))
    timestamp = get_field(withdrawal, "timestamp", "createdAt")
    paid_at = get_field(withdrawal, "paidAt")
    request_id = get_field(withdrawal, "_id")
    coin_balance = get_field(data, "coinBalance", default="0")

    return (
        "💸 Withdraw details\n\n"
        f"Request ID: {request_id}\n"
        f"Status: {status}\n"
        f"Amount: {amount}\n"
        f"UPI ID: {upi_id}\n"
        f"Requested at: {timestamp}\n"
        f"Paid at: {paid_at}\n"
        f"Current coin balance: {coin_balance}"
    )


def format_full_summary_localized(user_id: str, data: dict[str, Any], lang: str = "hi") -> str:
    summary = format_user_summary_localized(user_id, data, lang)
    health = format_account_health(data, lang)
    tips = format_smart_tips(data, lang)
    return f"{summary}\n\n━━━━━━━━━━━━━━\n\n{health}\n\n━━━━━━━━━━━━━━\n\n{tips}"


def unknown_question_text(lang: str = "hi") -> str:
    if lang == "en":
        return (
            "I could not find an exact database answer for this question.\n\n"
            "You can ask like this:\n"
            "• When will my money arrive?\n"
            "• Is my withdrawal pending?\n"
            "• How many coins do I have?\n"
            "• How many rolls happened today?\n\n"
            "To check another user, send 'change id'."
        )

    return (
        "भाई इस question का exact answer database से नहीं निकाल पा रहा।\n\n"
        "आप ऐसे पूछ सकते हो:\n"
        "• मेरा रुपया कब आएगा?\n"
        "• Withdraw pending है क्या?\n"
        "• मेरे coins कितने हैं?\n"
        "• आज कितने rolls हुए?\n\n"
        "दूसरा user check करना हो तो 'change id' भेजो।"
    )


def format_profile_localized(user_id: str, data: dict[str, Any], lang: str = "hi") -> str:
    display_id = get_field(data, "displayId", default=user_id)
    uid = get_field(data, "uid")
    name = get_field(data, "name", "fullName", "username", "displayName")
    email = get_field(data, "email", "mail")
    phone = get_field(data, "phone", "mobile", "phoneNumber")
    upi_id = get_field(data, "upiId", "upi")
    join_date = get_field(data, "joinDate", "createdAt")
    last_login = get_field(data, "lastLogin")
    photo_url = get_field(data, "photoUrl", "photoURL", "avatar")
    status = get_field(data, "status", "accountStatus", default="Active")

    title = "👤 Profile" if lang == "en" else "👤 प्रोफाइल"
    return (
        f"{title}\n\n"
        f"User ID: {display_id}\n"
        f"UID: {uid}\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Phone: {phone}\n"
        f"UPI ID: {upi_id}\n"
        f"Join date: {join_date}\n"
        f"Last login: {last_login}\n"
        f"Photo: {photo_url}\n"
        f"Status: {status}"
    )


def format_rolls_localized(data: dict[str, Any], lang: str = "hi") -> str:
    today_rolls = get_field(data, "todayRolls", "dailyRolls", default="0")
    total_rolls = get_field(data, "totalRolls", "rolls", "rollCount", default="0")
    last_roll = get_field(data, "lastRoll", "lastRollAt", "lastDailyRoll")
    last_roll_reset = get_field(data, "lastRollReset")

    if lang == "en":
        return (
            "🎲 Daily Rolls\n\n"
            f"Today rolls: {today_rolls}\n"
            f"Total rolls: {total_rolls}\n"
            f"Last roll: {last_roll}\n"
            f"Last roll reset: {last_roll_reset}"
        )

    return (
        "🎲 Daily Rolls\n\n"
        f"आज के rolls: {today_rolls}\n"
        f"Total rolls: {total_rolls}\n"
        f"Last roll: {last_roll}\n"
        f"Last roll reset: {last_roll_reset}"
    )


def format_history_localized(data: dict[str, Any], lang: str = "hi") -> str:
    history = data.get("history") or data.get("rollHistory") or data.get("transactions")
    sub_history = data.get("_history")
    withdrawals = data.get("_withdrawals")

    if isinstance(history, list) and history:
        return ("📜 Recent history\n\n" if lang == "en" else "📜 Recent history\n\n") + "\n".join(f"• {clean(item)}" for item in history[-5:])

    if isinstance(sub_history, list) and sub_history:
        lines = []
        for item in sub_history[:5]:
            label = get_field(item, "type", "title", "name", default="History item")
            amount = get_field(item, "amount", "coins", "coinBalance")
            time = get_field(item, "timestamp", "createdAt", "date")
            lines.append(f"• {label} | {amount} | {time}")
        return "📜 Recent history\n\n" + "\n".join(lines)

    if isinstance(withdrawals, list) and withdrawals:
        lines = []
        for item in withdrawals[:5]:
            amount = get_field(item, "amount", default="Not available")
            status = get_field(item, "status", default="Not available")
            time = get_field(item, "timestamp", "createdAt")
            lines.append(f"• Withdraw {amount} | {status} | {time}")
        return "📜 Recent withdrawal history\n\n" + "\n".join(lines)

    if lang == "en":
        return (
            "📜 History\n\n"
            f"Join date: {get_field(data, 'joinDate', 'createdAt')}\n"
            f"Last login: {get_field(data, 'lastLogin')}\n"
            f"Last roll reset: {get_field(data, 'lastRollReset')}\n\n"
            "Detailed transaction history is not available as a list in the database right now."
        )

    return (
        "📜 History\n\n"
        f"Join date: {get_field(data, 'joinDate', 'createdAt')}\n"
        f"Last login: {get_field(data, 'lastLogin')}\n"
        f"Last roll reset: {get_field(data, 'lastRollReset')}\n\n"
        "Detailed transaction history अभी database में list form में available नहीं है।"
    )


def faq_text_localized(lang: str = "hi") -> str:
    if lang == "en":
        return (
            "❓ FAQ\n\n"
            "Q. How to view user data?\n"
            "A. First send the RollCash user ID/displayId. Example: RC-2NYJWB\n\n"
            "Q. How to check user coins?\n"
            "A. After setting the user ID, press Coins or type 'coins'.\n\n"
            "Q. How to check withdrawal info?\n"
            "A. After setting the user ID, press Withdraw."
        )

    return (
        "❓ FAQ\n\n"
        "Q. User data कैसे देखें?\n"
        "A. पहले RollCash user ID/displayId भेजें। Example: RC-2NYJWB\n\n"
        "Q. User के coins कैसे देखें?\n"
        "A. User ID set करने के बाद Coins button दबाएं या 'coins' type करें।\n\n"
        "Q. Withdraw info कैसे check करें?\n"
        "A. User ID set करने के बाद Withdraw button दबाएं।"
    )


def support_text_localized(lang: str = "hi") -> str:
    if lang == "en":
        return (
            "👨‍💼 Human Support\n\n"
            "If the bot cannot answer, contact the support team:\n"
            "@RollCashSupport"
        )

    return (
        "👨‍💼 Human Support\n\n"
        "अगर bot answer न दे पाए तो support team से contact करें:\n"
        "@RollCashSupport"
    )


def format_natural_reply_localized(message: str, user_id: str, data: dict[str, Any], lang: str = "hi") -> str | None:
    text = message.lower()
    name = get_field(data, "name", "fullName", "username", "displayName", default="friend" if lang == "en" else "bhai")
    display_id = get_field(data, "displayId", default=user_id)

    if has_any(text, ("rupiya", "rupee", "paisa", "paise", "payment", "payout", "withdraw", "withdrow", "nikal", "pending", "kab aayega", "kab milega", "money", "paid")):
        return format_payment_question(data, lang)
    if has_any(text, ("problem", "issue", " dikkat", "dikkt", "health", "check", "safe", "sahi hai", "account check")):
        return format_account_health(data, lang)
    if has_any(text, ("tips", "tip", "suggest", "suggestion", "kya karu", "kaise badhe", "kaise badhau")):
        return format_smart_tips(data, lang)
    if has_any(text, ("command", "commands", "kya puch", "kya pooch", "help menu")):
        return commands_text(lang)
    if has_any(text, ("sab data", "full data", "summary", "poora data", "pura data", "all data")):
        return format_full_summary_localized(user_id, data, lang)

    if has_any(text, ("kitna coin", "mere coin", "coins kitne", "coin balance", "balance kitna", "how many coin", "my coin")):
        coin_balance = get_field(data, "coinBalance", default="0")
        total_earned = get_field(data, "totalCoinsEarned", default="0")
        if lang == "en":
            return f"🪙 {name}, your current coin balance is {coin_balance}.\nTotal earned coins: {total_earned}."
        return f"🪙 {name}, aapka current coin balance {coin_balance} hai.\nTotal earned coins: {total_earned}."

    if has_any(text, ("mera account", "meri id", "profile", "details", "data", "my account")):
        return format_profile_localized(user_id, data, lang)

    if has_any(text, ("roll bacha", "rolls bache", "today roll", "aaj roll", "daily roll")):
        return format_rolls_localized(data, lang)

    if has_any(text, ("hello", "hi", "hii", "hey", "namaste")):
        if lang == "en":
            return (
                f"Hi {name} 👋\n\n"
                f"Your RollCash ID {display_id} is set. "
                "You can ask about wallet, coins, withdrawal, rolls, profile, or history."
            )
        return (
            f"Hi {name} 👋\n\n"
            f"Aapka RollCash ID {display_id} set hai. "
            "Aap wallet, coins, withdraw, rolls, profile ya history ke baare me pooch sakte ho."
        )

    if has_any(text, ("thank", "thanks", "dhanyawad", "shukriya")):
        if lang == "en":
            return "Welcome 😊 Tell me if you want to check anything else."
        return "Welcome bhai 😊 Aur kuch check karna ho to bolo."

    return None


def answer_for_option_localized(option: str, user_id: str, data: dict[str, Any], lang: str = "hi") -> str:
    if option == "wallet":
        return format_wallet_localized(data, lang)
    if option == "coins":
        return format_coins_localized(data, lang)
    if option == "withdraw":
        return format_withdraw_localized(data, lang)
    if option == "rolls":
        return format_rolls_localized(data, lang)
    if option == "profile":
        return format_profile_localized(user_id, data, lang)
    if option == "history":
        return format_history_localized(data, lang)
    if option == "faq":
        return faq_text_localized(lang)
    if option == "support":
        return support_text_localized(lang)
    if option == "health":
        return format_account_health(data, lang)
    if option == "tips":
        return format_smart_tips(data, lang)
    if option == "commands":
        return commands_text(lang)
    if option == "summary":
        return format_full_summary_localized(user_id, data, lang)
    if option == "language":
        return "Please choose your language.\nकृपया अपनी भाषा चुनें।"
    return "Please choose a valid option." if lang == "en" else "कृपया valid option choose करें।"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(CURRENT_USER_ID_KEY, None)

    await update.message.reply_text(
        language_prompt(update.effective_user.first_name),
        reply_markup=language_menu(),
    )


async def show_user_data(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str):
    lang = get_lang(context)
    snapshot, data = await run_db_call(find_user, user_id)

    if not snapshot or data is None:
        await update.message.reply_text(
            not_found_text(lang),
            reply_markup=main_menu(lang),
        )
        return

    context.user_data[CURRENT_USER_ID_KEY] = user_id
    await update.message.reply_text(format_user_summary_localized(user_id, data, lang), reply_markup=main_menu(lang))
    asyncio.create_task(warm_user_bundle(user_id))
    return

    if not snapshot or data is None:
        await update.message.reply_text(
            "❌ Is user ID ka data nahi mila.\n\n"
            "Please correct RollCash user ID bhejo.\n"
            "Example: RC-2NYJWB",
            reply_markup=main_menu(),
        )
        return

    context.user_data[CURRENT_USER_ID_KEY] = user_id
    await update.message.reply_text(format_user_summary(user_id, data), reply_markup=main_menu())
    asyncio.create_task(warm_user_bundle(user_id))


def answer_for_option(option: str, user_id: str, data: dict[str, Any]) -> str:
    if option == "wallet":
        return format_wallet(data)
    if option == "coins":
        return format_coins(data)
    if option == "withdraw":
        return format_withdraw(data)
    if option == "rolls":
        return format_rolls(data)
    if option == "profile":
        return format_profile(user_id, data)
    if option == "history":
        return format_history(data)
    if option == "faq":
        return faq_text()
    if option == "support":
        return support_text()
    if option == "health":
        return format_account_health(data)
    if option == "tips":
        return format_smart_tips(data)
    if option == "commands":
        return commands_text()
    if option == "summary":
        return format_full_summary(user_id, data)
    return "Please valid option choose karo."


def detect_question_option(message: str) -> str | None:
    text = message.lower()

    if any(word in text for word in ("wallet", "balance", "paise", "paisa", "rupiya", "rupee", "amount")):
        return "wallet"
    if any(word in text for word in ("coin", "coins", "sikka")):
        return "coins"
    if any(word in text for word in ("withdraw", "withdrow", "payout", "payment", "upi", "pending")):
        return "withdraw"
    if any(word in text for word in ("roll", "daily")):
        return "rolls"
    if any(word in text for word in ("profile", "name", "email", "phone")):
        return "profile"
    if any(word in text for word in ("history", "transaction", "record")):
        return "history"
    if any(word in text for word in ("faq", "help", "kaise", "how")):
        return "faq"
    if any(word in text for word in ("support", "human", "admin")):
        return "support"
    if any(word in text for word in ("health", "problem", "issue", "check")):
        return "health"
    if any(word in text for word in ("tips", "suggestion", "suggest")):
        return "tips"
    if any(word in text for word in ("command", "commands")):
        return "commands"
    if any(word in text for word in ("summary", "all data", "full data")):
        return "summary"

    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (update.message.text or "").strip()
    lang = get_lang(context)

    if not message:
        return

    if await handle_support_message(update, context):
        return

    current_user_id = context.user_data.get(CURRENT_USER_ID_KEY)

    if not current_user_id:
        await show_user_data(update, context, message)
        return

    if message.lower() in ("change", "change id", "new id", "dusra id", "id change"):
        context.user_data.pop(CURRENT_USER_ID_KEY, None)
        await update.message.reply_text(change_id_text(lang), reply_markup=main_menu(lang))
        return

    _, data = await load_user_bundle_fast(current_user_id)

    if data is None:
        context.user_data.pop(CURRENT_USER_ID_KEY, None)
        await update.message.reply_text(
            saved_id_missing_text(lang),
            reply_markup=main_menu(lang),
        )
        return

    natural_reply = format_natural_reply_localized(message, current_user_id, data, lang)
    if natural_reply:
        await update.message.reply_text(natural_reply, reply_markup=main_menu(lang))
        return

    option = detect_question_option(message)

    if not option:
        await update.message.reply_text(
            unknown_question_text(lang),
            reply_markup=main_menu(lang),
        )
        return

    if not option:
        await update.message.reply_text(
            "Bhai main is question ka exact answer database se nahi nikal pa raha.\n\n"
            "Aap aise pooch sakte ho:\n"
            "• Mera rupiya kab aayega?\n"
            "• Withdraw pending hai kya?\n"
            "• Mere coins kitne hain?\n"
            "• Aaj kitne rolls hue?\n\n"
            "Dusra user check karna ho to 'change id' bhejo.",
            reply_markup=main_menu(),
        )
        return

    await update.message.reply_text(
        answer_for_option_localized(option, current_user_id, data, lang),
        reply_markup=main_menu(lang),
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_support_callback(update, context):
        return

    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass

    option = query.data
    lang = get_lang(context)
    current_user_id = context.user_data.get(CURRENT_USER_ID_KEY)

    if option in ("lang_hi", "lang_en"):
        lang = "hi" if option == "lang_hi" else "en"
        context.user_data[LANGUAGE_KEY] = lang
        text = welcome_text_localized(query.from_user.first_name, lang)
        await query.message.reply_text(text, reply_markup=main_menu(lang))
        return

    if option == "language":
        await query.message.reply_text(language_prompt(query.from_user.first_name), reply_markup=language_menu())
        return

    if option in ("faq", "support", "commands"):
        text = answer_for_option_localized(option, "", {}, lang)
    elif not current_user_id:
        text = (
            "Please send your RollCash user ID first. Example: RC-2NYJWB"
            if lang == "en"
            else "पहले अपना RollCash user ID भेजो। Example: RC-2NYJWB"
        )
    else:
        _, data = await load_user_bundle_fast(current_user_id)
        if data is None:
            context.user_data.pop(CURRENT_USER_ID_KEY, None)
            text = saved_id_missing_text(lang)
        else:
            text = answer_for_option_localized(option, current_user_id, data, lang)

    try:
        await query.message.reply_text(text, reply_markup=main_menu(lang))
    except BadRequest:
        await query.message.reply_text(text)
