from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def language_menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇮🇳 हिन्दी", callback_data="lang_hi"),
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            ]
        ]
    )


def main_menu(lang: str = "hi"):
    labels = {
        "hi": {
            "wallet": "💰 Wallet",
            "coins": "🪙 Coins",
            "withdraw": "💸 Withdraw",
            "rolls": "🎲 Rolls",
            "profile": "👤 Profile",
            "history": "📜 History",
            "faq": "❓ FAQ",
            "support": "👨‍💼 Human Support",
            "health": "🩺 Health",
            "tips": "💡 Tips",
            "commands": "📋 Commands",
            "summary": "🧾 Full Summary",
            "language": "🌐 भाषा / Language",
            "updates": "📢 Updates",
        },
        "en": {
            "wallet": "💰 Wallet",
            "coins": "🪙 Coins",
            "withdraw": "💸 Withdraw",
            "rolls": "🎲 Rolls",
            "profile": "👤 Profile",
            "history": "📜 History",
            "faq": "❓ FAQ",
            "support": "👨‍💼 Human Support",
            "health": "🩺 Health",
            "tips": "💡 Tips",
            "commands": "📋 Commands",
            "summary": "🧾 Full Summary",
            "language": "🌐 Language",
            "updates": "📢 Updates",
        },
    }.get(lang, {})

    keyboard = [
        [
            InlineKeyboardButton(labels.get("wallet", "💰 Wallet"), callback_data="wallet"),
            InlineKeyboardButton(labels.get("coins", "🪙 Coins"), callback_data="coins"),
        ],
        [
            InlineKeyboardButton(labels.get("withdraw", "💸 Withdraw"), callback_data="withdraw"),
            InlineKeyboardButton(labels.get("rolls", "🎲 Rolls"), callback_data="rolls"),
        ],
        [
            InlineKeyboardButton(labels.get("profile", "👤 Profile"), callback_data="profile"),
            InlineKeyboardButton(labels.get("history", "📜 History"), callback_data="history"),
        ],
        [
            InlineKeyboardButton(labels.get("faq", "❓ FAQ"), callback_data="faq"),
            InlineKeyboardButton(labels.get("support", "👨‍💼 Human Support"), callback_data="support"),
        ],
        [
            InlineKeyboardButton(labels.get("health", "🩺 Health"), callback_data="health"),
            InlineKeyboardButton(labels.get("tips", "💡 Tips"), callback_data="tips"),
        ],
        [
            InlineKeyboardButton(labels.get("commands", "📋 Commands"), callback_data="commands"),
            InlineKeyboardButton(labels.get("summary", "🧾 Full Summary"), callback_data="summary"),
        ],
        [
            InlineKeyboardButton(labels.get("language", "🌐 भाषा / Language"), callback_data="language"),
        ],
        [
            InlineKeyboardButton(labels.get("updates", "📢 Updates"), url="https://t.me/RollCashUpdates"),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)
