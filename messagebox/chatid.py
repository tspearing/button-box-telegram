"""Print chat ids the bot has already seen.

Stop the message-box service first. Telegram allows only one getUpdates
client at a time. While the service is running, send /chatid in the chat
instead.
"""

from __future__ import annotations

import logging

log = logging.getLogger("messagebox.chatid")


async def print_chat_ids(token: str) -> int:
    from telegram import Bot
    from telegram.error import Conflict, NetworkError, TimedOut

    bot = Bot(token)
    try:
        async with bot:
            try:
                updates = await bot.get_updates(timeout=0, allowed_updates=["message"])
            except Conflict:
                print(
                    "The bot is already being polled, probably by the message-box service.\n"
                    "Either run:  sudo systemctl stop message-box\n"
                    "and try again, or send /chatid in the family chat while the service is up."
                )
                return 1
            except (NetworkError, TimedOut) as exc:
                print(f"Could not reach Telegram ({type(exc).__name__}). Check the network and try again.")
                return 1
    except Exception as exc:
        log.error("chat id lookup failed: %s", type(exc).__name__)
        print(f"Could not talk to Telegram ({type(exc).__name__}).")
        return 1

    found: dict[int, str] = {}
    for update in updates:
        chat = update.effective_chat
        if chat is None:
            continue
        full_name = getattr(chat, "full_name", None)
        if chat.title:
            label = f"{chat.type} {chat.title}"
        elif chat.username:
            label = f"{chat.type} @{chat.username}"
        elif full_name:
            label = f"{chat.type} {full_name}"
        else:
            label = chat.type or "chat"
        found[chat.id] = label

    if not found:
        print(
            "No messages waiting for the bot.\n"
            "In Telegram, open your bot and send /start. For the family, create a\n"
            "group, add the bot, and send any message. Then run this command again.\n"
            "If the service is already running, send /chatid in the chat instead."
        )
        return 1

    print("Chats that have messaged the bot:\n")
    for chat_id, label in found.items():
        print(f"  {chat_id}\t{label}")
    print(
        "\nUse the family GROUP id (it is a negative number), not a private chat,\n"
        "unless you really want notes to go to one person only.\n"
        "\nExample config:\n"
    )
    # Prefer a group id in the sample so a private chat with the bot is not
    # silently chosen as the whole family.
    sample = next((cid for cid in found if cid < 0), next(iter(found)))
    print("  [telegram]")
    print(f"  allowed_chat_ids = [{sample}]")
    print(f"  family_chat_id = {sample}")
    print("\nThese updates were left unread, so the service can still download them.")
    return 0
