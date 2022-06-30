from pyrogram.file_id import FileId, FileType
from pyrogram.session import Session, Auth
from pyrogram.raw.functions.upload import GetFile
from pyrogram.raw.functions.auth import ImportAuthorization, ExportAuthorization
from pyrogram.raw.types import InputDocumentFileLocation, InputPhotoFileLocation, InputPeerPhotoFileLocation, InputPeerChannel, InputPeerChat, InputPeerUser

class Buffer:
    def __init__(self, file):
        self._offset = 0
        self._file = file
        self._bytes = b""
        self._eof = False

    async def read(self, count):
        if count > len(self._bytes):
            if not self._eof:
                chunk = await self._file.getChunkAt(self._offset)
                self._bytes += chunk
                if len(chunk) != 1024*1024:
                    self._eof = True
            else:
                if len(self._bytes) == 0:
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
        return (await session.send(GetFile(location=self.loc, offset=offset, limit=1024*1024))).bytes

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