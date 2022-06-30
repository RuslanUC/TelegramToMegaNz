# -*- coding: utf-8 -*-
from pyrogram import Client, filters
from os import environ
from utils import AccountsManager

bot = Client(
    "MegaNzBot",
    api_id=int(environ.get("API_ID")),
    api_hash=environ.get("API_HASH"),
    bot_token=environ.get("TG_BOT_TOKEN"),
    in_memory=True
)
setattr(bot, "mega_accountsManager", AccountsManager())

@bot.on_message(~filters.bot & (filters.audio | filters.document | filters.photo | filters.animation | filters.video | filters.voice | filters.video_note))
async def file_handler(_cl, message):
    media = getattr(message, message.media.value)
    msg = await message.reply(f"Getting an account...")
    async def _cb(text):
        mid, cid = msg.id, message.from_user.id
        await bot.edit_message_text(cid, mid, text)
    acc = await bot.mega_accountsManager.getAccount(media.file_size, _cb)
    await acc.upload(media, bot, _cb)

@bot.on_message(~filters.bot & (filters.text | filters.command(["start"])))
async def message_account(_cl, message):
    return await message.reply("Send me a media and i send you a mega.nz link.")

if __name__ == "__main__":
    print("Bot running!")
    bot.run()