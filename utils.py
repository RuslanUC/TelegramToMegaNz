from PyOneSecMail import OneSecMailApi
from mega import Mega
from tg import File
from pyrogram.file_id import FileType, PHOTO_TYPES
from random import choice
from re import compile
from datetime import datetime
from time import time
from os import environ
from asyncpg import create_pool
from asyncio import get_event_loop, sleep as asleep, create_subprocess_shell, subprocess as asubprocess
from base64 import b64encode, b64decode

_re = compile(r"https{0,1}:\/\/mega.nz\/#confirm[a-zA-Z0-9_-]{80,512}")

class MegaAccount:
    def __init__(self, password=None, email=None):
        self.name = "".join(choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") for x in range(12))
        self.password = password
        self.email = email
        self._mega = Mega()

    async def init_mail(self):
        self.mapi = OneSecMailApi()
        self.email = await self.mapi.get_mail()
        return self

    async def register(self):
        if not self.email: return
        registration = await create_subprocess_shell(f"./megatools --register --email {self.email} --name {self.name} --password {self.password}", stdout=asubprocess.PIPE, stderr=asubprocess.DEVNULL)
        stdout, _ = await registration.communicate()
        self.verify_command = stdout.decode("utf8").strip()

    async def verify(self):
        if not self.email: return
        content = None
        for i in range(10):
            if content is not None:
                break
            await asleep(3)
            for mail in await self.mapi.fetch_inbox():
                if "MEGA" in mail.subject or "mega" in mail.text.lower() or "mega" in mail.mfrom.lower():
                    content = mail.text
                    break

        link = _re.findall(content)
        self.verify_command = "./"+self.verify_command.replace("@LINK@", link[0])

        try:
            verification = await create_subprocess_shell(self.verify_command, stdout=asubprocess.PIPE, stderr=asubprocess.DEVNULL)
            stdout, _ = await verification.communicate()
        except Exception as e:
            return

        return (self.email, self.password)

    async def login(self, login, password):
        self.email = login
        self.password = password
        await self._mega.login(login, password)
        return self

    async def upload(self, media, client, callback):
        size = media.file_size
        if not size:
            return
        await callback("Uploading...")
        file = File(media.file_id, client)
        mime_type = getattr(media, "mime_type", "")
        date = getattr(media, "date", 0)
        name = getattr(media, "file_name", "")
        file_type = file.id.file_type
        if not name:
            guessed_extension = client.guess_extension(mime_type)
            if file_type in PHOTO_TYPES:
                extension = ".jpg"
                if not mime_type: mime_type = "image/jpeg"
            elif file_type == FileType.VOICE:
                extension = guessed_extension or ".ogg"
                if not mime_type: mime_type = "audio/ogg"
            elif file_type in (FileType.VIDEO, FileType.ANIMATION, FileType.VIDEO_NOTE):
                extension = guessed_extension or ".mp4"
                if not mime_type: mime_type = "video/mp4"
            elif file_type == FileType.DOCUMENT:
                extension = guessed_extension or ".zip"
                if not mime_type: mime_type = "application/zip"
            elif file_type == FileType.AUDIO:
                extension = guessed_extension or ".mp3"
                if not mime_type: mime_type = "audio/mpeg"
            else:
                extension = ".unknown"
            name = f"{FileType(file.id.file_type).name.lower()}_{(date or datetime.fromtimestamp(time())).strftime('%Y-%m-%d_%H-%M-%S')}{extension}"

        name = name[::-1].split(".", 1)
        name[1] = f"_{int(time())-1654041600}_{choice('0123456789')}"[::-1]+name[1]
        name = ".".join(name)[::-1]
        link = await self._mega.upload_from_tg(file, size, name, callback)
        await client.mega_accountsManager.takeSpace(self, size)
        await callback(f"File uploaded to mega.nz!\n\nFile name: {name}\nLink: {link}")

class AccountsManager:
    def __init__(self):
        self._pool = None
        self._reg = False
        self._registerTask = None

    async def init(self):
        self._pool = await create_pool(environ.get("DB"), min_size=10, max_size=50)
        if not self._registerTask:
            self._registerTask = get_event_loop().create_task(self._registerAccountsTask())

    async def getAccount(self, file_size, callback):
        while self._reg:
            await callback("Registering new account...")
            await asleep(0.5)
        if not self._pool:
            await self.init()
        async with self._pool.acquire() as db:
            accounts = await db.fetch(f"SELECT login, password, user_hash, password_aes FROM accounts WHERE free_space > {file_size} LIMIT 10;")
        if accounts:
            account = _account = choice(accounts)
            account = (account["login"], account["password"])
            await callback("Authorization...")
            if (uh := _account["user_hash"]) and (pa := _account["password_aes"]):
                acc = MegaAccount(email=_account["login"])
                pa = b64decode(bytes(pa, "utf8"))
                await acc._mega._login_user_k(_account["login"], uh, pa)
                return acc
            account = await MegaAccount().login(*account)
            if (uh := getattr(account._mega, "_user_hash")) and (pa := getattr(account._mega, "_password_aes")):
                pa = b64encode(pa).decode("utf8")
                async with self._pool.acquire() as db:
                    await db.execute(f"UPDATE accounts SET user_hash='{uh}', password_aes='{pa}' WHERE login='{account.email}';")
            return account
        self._reg = True
        try:
            account = await MegaAccount("".join(choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for x in range(16))).init_mail()
            await account.register()
            acc = await account.verify()
            await callback("Authorization...")
            await account.login(*acc)
            async with self._pool.acquire() as db:
                if (uh := getattr(account._mega, "_user_hash")) and (pa := getattr(account._mega, "_password_aes")):
                    pa = b64encode(pa).decode("utf8")
                    await db.execute(f"INSERT INTO accounts (login, password, user_hash, password_aes) VALUES ('{acc[0]}', '{acc[1]}', '{uh}', '{pa}');")
                else:
                    await db.execute(f"INSERT INTO accounts (login, password) VALUES ('{acc[0]}', '{acc[1]}');")
        except Exception as e:
            print(e)
        self._reg = False
        return account

    async def takeSpace(self, account, bytes_count):
        if not self._pool:
            await self.init()
        async with self._pool.acquire() as db:
            await db.execute(f"UPDATE accounts SET free_space=free_space-{bytes_count} WHERE login='{account.email}';")

    async def _registerAccountsTask(self):
        while True:
            account = await MegaAccount("".join(choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for x in range(16))).init_mail()
            await account.register()
            acc = await account.verify()
            await account.login(*acc)
            async with self._pool.acquire() as db:
                if (uh := getattr(account._mega, "_user_hash")) and (pa := getattr(account._mega, "_password_aes")):
                    pa = b64encode(pa).decode("utf8")
                    await db.execute(f"INSERT INTO accounts (login, password, user_hash, password_aes) VALUES ('{acc[0]}', '{acc[1]}', '{uh}', '{pa}');")
                else:
                    await db.execute(f"INSERT INTO accounts (login, password) VALUES ('{acc[0]}', '{acc[1]}');")
            await asleep(60*60)