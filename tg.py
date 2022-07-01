from pyrogram.file_id import FileId, FileType
from pyrogram.session import Session, Auth
from pyrogram.raw.functions.upload import GetFile
from pyrogram.raw.functions.auth import ImportAuthorization, ExportAuthorization
from pyrogram.raw.types import InputDocumentFileLocation, InputPhotoFileLocation, InputPeerPhotoFileLocation, InputPeerChannel, InputPeerChat, InputPeerUser
from asyncio import get_event_loop, sleep as asleep, gather

class Buffer:
    def __init__(self, file):
        self._offset = 0
        self._file = file
        self._bytes = b""
        self._eof = False
        self._dl = 0

    async def _start(self, task=False, loop=None):
        if not loop:
            loop = get_event_loop()
        if not task:
            loop.create_task(self._start(True, loop))
            return
        downloading = []
        part_id = 0
        while not self._eof:
            if len(downloading) < 2 and len(self._bytes) < 16*1024*1024:
                downloading.append(loop.create_task(self._file.getChunkAt(part_id*1024*1024)))
                part_id += 1
            if downloading and downloading[0].done():
                dtask = downloading.pop(0)
                res = await gather(dtask)
                chunk = res[0]
                self._dl += len(chunk)
                if len(chunk) != 1024*1024:
                    self._eof = True
                self._bytes += chunk
            await asleep(0.1)

    async def read(self, count):
        while count > len(self._bytes):
            if self._eof:
                break
            await asleep(0.1)
        if len(self._bytes) == 0 and self._eof:
            return b""
        ret = self._bytes[:count]
        self._bytes = self._bytes[count:]
        return ret

class File:
    def __init__(self, id, client):
        self.id = FileId.decode(id)
        self.client = client
        self.loc = get_location(self.id)
        self._buf = Buffer(self)

    async def getChunkAt(self, offset=0):
        session = await get_media_session(self.client, self.id)
        for i in range(5):
            try:
                return (await session.send(GetFile(location=self.loc, offset=offset, limit=1024*1024))).bytes
            except Exception as e:
                if i == 4:
                    raise
                print(f"tg:{e.__class__.__name__}: {e}")
                await asleep(1)

    async def start_buf(self):
        await self._buf._start()

    async def read(self, count):
        return await self._buf.read(count)

def get_location(file_id):
    file_type = file_id.file_type
    if file_type == FileType.CHAT_PHOTO:
        if file_id.chat_id > 0:
            peer = InputPeerUser(user_id=file_id.chat_id, access_hash=file_id.chat_access_hash)
        else:
            if file_id.chat_access_hash == 0:
                peer = InputPeerChat(chat_id=-file_id.chat_id)
            else:
                peer = InputPeerChannel(channel_id=utils.get_channel_id(file_id.chat_id), access_hash=file_id.chat_access_hash)

        location = InputPeerPhotoFileLocation(peer=peer, volume_id=file_id.volume_id, local_id=file_id.local_id, big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG)
    elif file_type == FileType.PHOTO:
        location = InputPhotoFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference, thumb_size=file_id.thumbnail_size)
    else:
        location = InputDocumentFileLocation(id=file_id.media_id, access_hash=file_id.access_hash, file_reference=file_id.file_reference,thumb_size=file_id.thumbnail_size)

    return location

async def get_media_session(client, file_id):
    if not (media_session := client.media_sessions.get(file_id.dc_id, None)):
        if file_id.dc_id != await client.storage.dc_id():
            media_session = Session(client, file_id.dc_id, await Auth(client, file_id.dc_id, await client.storage.test_mode()).create(), await client.storage.test_mode(), is_media=True)
            await media_session.start()

            for _ in range(6):
                exported_auth = await client.send(ExportAuthorization(dc_id=file_id.dc_id))
                try:
                    await media_session.send(ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes))
                    break
                except AuthBytesInvalid:
                    continue
            else:
                await media_session.stop()
                raise AuthBytesInvalid
        else:
            media_session = Session(client, file_id.dc_id, await client.storage.auth_key(), await client.storage.test_mode(), is_media=True)
            await media_session.start()
        client.media_sessions[file_id.dc_id] = media_session
    return media_session

async def stream_file(parts, bot):
    parts.sort(key=lambda x: x["part_id"])
    parts = [p["tg_file"] for p in parts]
    for part in parts:
        file = File(part, bot)
        async for chunk in file.stream():
            yield chunk