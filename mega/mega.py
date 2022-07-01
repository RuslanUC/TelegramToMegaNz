from math import ceil
from json import dumps as jdumps
from hashlib import pbkdf2_hmac
from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Util import Counter
from os.path import getsize as ogetsize
from random import randint
from binascii import unhexlify
from io import BytesIO
from functools import wraps
from asyncio import sleep as asleep
from aiohttp import ClientSession

from .errors import RequestError
from .crypto import encrypt_key, base64_url_encode, encrypt_attr, base64_url_decode, a32_to_str, get_chunks, str_to_a32, decrypt_key, mpi_to_int, username_hash, make_id, modular_inverse, prepare_key

def retry(f):
    @wraps(f)
    async def wrapped(*args, **kwargs):
        for i in range(5):
            try:
                return await f(*args, **kwargs)
            except RequestError:
                if i == 4:
                    raise
                await asleep(3)
    return wrapped

class Mega:
    def __init__(self):
        self.sid = None
        self.sequence_num = randint(0, 0xFFFFFFFF)
        self.request_id = make_id(10)

    async def login(self, email=None, password=None):
        await self._login_user(email, password)
        return self

    async def _login_user(self, email, password):
        email = email.lower()
        get_user_salt_resp = await self._api_request({'a': 'us0', 'user': email})
        if (user_salt := base64_url_decode(get_user_salt_resp.get('s'))):
            pbkdf2_key = pbkdf2_hmac(hash_name='sha512', password=password.encode(), salt=user_salt, iterations=100000, dklen=32)
            password_aes = pbkdf2_key[:16]
            user_hash = base64_url_encode(pbkdf2_key[-16:])
        else:
            password_aes = prepare_key(password)
            user_hash = username_hash(email, password_aes)
        self._user_hash = user_hash
        self._password_aes = password_aes
        await self._login_user_k(email, user_hash, password_aes)

    async def _login_user_k(self, email, user_hash, password_aes):
        resp = await self._api_request({'a': 'us', 'user': email, 'uh': user_hash})
        if isinstance(resp, int):
            raise RequestError(resp)
        await self._login_process(resp, password_aes)

    async def _login_process(self, resp, password):
        encrypted_master_key = base64_url_decode(resp['k'])
        self.master_key = decrypt_key(encrypted_master_key, password)
        if 'tsid' in resp:
            tsid = base64_url_decode(resp['tsid'])
            key_encrypted = encrypt_key(tsid[:16], self.master_key)
            if key_encrypted == tsid[-16:]:
                self.sid = resp['tsid']
        elif 'csid' in resp:
            encrypted_rsa_private_key = base64_url_decode(resp['privk'])
            private_key = decrypt_key(encrypted_rsa_private_key, self.master_key)

            rsa_private_key = [0, 0, 0, 0]
            for i in range(4):
                bitlength = (private_key[0] * 256) + private_key[1]
                bytelength = ceil(bitlength / 8)
                bytelength += 2
                rsa_private_key[i] = mpi_to_int(private_key[:bytelength])
                private_key = private_key[bytelength:]

            first_factor_p = rsa_private_key[0]
            second_factor_q = rsa_private_key[1]
            private_exponent_d = rsa_private_key[2]
            rsa_modulus_n = first_factor_p * second_factor_q
            phi = (first_factor_p - 1) * (second_factor_q - 1)
            public_exponent_e = modular_inverse(private_exponent_d, phi)

            rsa_components = (
                rsa_modulus_n,
                public_exponent_e,
                private_exponent_d,
                first_factor_p,
                second_factor_q,
            )
            rsa_decrypter = RSA.construct(rsa_components)

            encrypted_sid = mpi_to_int(base64_url_decode(resp['csid']))

            sid = '%x' % rsa_decrypter._decrypt(encrypted_sid)
            sid = unhexlify('0' + sid if len(sid) % 2 else sid)
            self.sid = base64_url_encode(sid[:43])
        await self._get_root_id()

    @retry
    async def _api_request(self, data):
        params = {'id': self.sequence_num}
        self.sequence_num += 1

        if self.sid:
            params.update({'sid': self.sid})

        if not isinstance(data, list):
            data = [data]

        async with ClientSession() as sess:
            async with sess.post("https://g.api.mega.co.nz/cs", params=params, data=jdumps(data), timeout=120) as resp:
                json_resp = await resp.json()
        try:
            if isinstance(json_resp, list):
                int_resp = json_resp[0] if isinstance(json_resp[0], int) else None
            elif isinstance(json_resp, int):
                int_resp = json_resp
        except IndexError:
            int_resp = None
        if int_resp is not None:
            if int_resp == 0:
                return int_resp
            raise RequestError(int_resp)
        return json_resp[0]

    async def _get_root_id(self):
        files = await self._api_request({'a': 'f', 'c': 1, 'r': 1})
        files = files["f"]
        self.root_id = [f for f in files if f["t"] == 2][0]['h']

    async def _get_upload_link(self, file):
        file = file['f'][0]
        public_handle = await self._api_request({'a': 'l', 'n': file['h']})
        file_key = file['k'][file['k'].index(':') + 1:]
        decrypted_key = base64_url_encode(decrypt_key(base64_url_decode(file_key), self.master_key))
        return f'https://mega.nz/file/{public_handle}#{decrypted_key}'

    async def upload_from_tg(self, file, file_size, filename, callback):
        ul_url = await self._api_request({'a': 'u', 's': file_size})
        ul_url = ul_url['p']

        ul_key = [randint(0, 0xFFFFFFFF) for _ in range(6)]
        k_str = a32_to_str(ul_key[:4])
        count = Counter.new(128, initial_value=((ul_key[4] << 32) + ul_key[5]) << 64)
        aes = AES.new(k_str, AES.MODE_CTR, counter=count)

        upload_progress = 0
        completion_file_handle = None

        mac_str = b'\x00' * 16
        mac_encryptor = AES.new(k_str, AES.MODE_CBC, mac_str)
        iv_str = a32_to_str([ul_key[4], ul_key[5], ul_key[4], ul_key[5]])
        
        fs_mb = round(file_size/1024/1024, 2)

        for chunk_start, chunk_size in get_chunks(file_size):
            chunk = await file.read(chunk_size)
            upload_progress += len(chunk)
            await callback(
                f"Downloading...\n" +
                f"{round(file._buf._dl/1024/1024, 2)}/{fs_mb} MB " +
                f"({round(file._buf._dl/file_size*100, 1)}%)\n" +
                f"Buffered: {round(len(file._buf._bytes)/1024/1024, 2)} MB\n\n" +
                f"Uploading...\n{round(upload_progress/1024/1024, 2)}/{fs_mb} MB " +
                f"({round(upload_progress/file_size*100, 1)}%)"
            )

            encryptor = AES.new(k_str, AES.MODE_CBC, iv_str)
            for i in range(0, len(chunk) - 16, 16):
                block = chunk[i:i + 16]
                encryptor.encrypt(block)

            if file_size > 16:
                i += 16
            else:
                i = 0

            block = chunk[i:i + 16]
            if len(block) % 16:
                block += b'\x00' * (-len(block) % 16)
            mac_str = mac_encryptor.encrypt(encryptor.encrypt(block))

            chunk = aes.encrypt(chunk)
            async with ClientSession() as sess:
                async with sess.post(f"{ul_url}/{chunk_start}", data=chunk, timeout=120) as resp:
                    completion_file_handle = await resp.text()

        file_mac = str_to_a32(mac_str)

        # determine meta mac
        meta_mac = (file_mac[0] ^ file_mac[1], file_mac[2] ^ file_mac[3])

        attribs = {'n': filename}

        encrypt_attribs = base64_url_encode(encrypt_attr(attribs, ul_key[:4]))
        key = [
            ul_key[0] ^ ul_key[4], ul_key[1] ^ ul_key[5],
            ul_key[2] ^ meta_mac[0], ul_key[3] ^ meta_mac[1], ul_key[4],
            ul_key[5], meta_mac[0], meta_mac[1]
        ]
        encrypted_key = base64_url_encode(encrypt_key(a32_to_str(key), self.master_key))
        # update attributes
        data = await self._api_request({
            'a': 'p',
            't': self.root_id,
            'i': self.request_id,
            'n': [{
                'h': completion_file_handle,
                't': 0,
                'a': encrypt_attribs,
                'k': encrypted_key
            }]
        })
        return await self._get_upload_link(data)

    async def upload(self, filename):
        with open(filename, 'rb') as input_file:
            file_size = ogetsize(filename)
            ul_url = await self._api_request({'a': 'u', 's': file_size})
            ul_url = ul_url['p']

            # generate random aes key (128) for file
            ul_key = [randint(0, 0xFFFFFFFF) for _ in range(6)]
            k_str = a32_to_str(ul_key[:4])
            count = Counter.new(128, initial_value=((ul_key[4] << 32) + ul_key[5]) << 64)
            aes = AES.new(k_str, AES.MODE_CTR, counter=count)

            upload_progress = 0
            completion_file_handle = None

            mac_str = b'\x00' * 16
            mac_encryptor = AES.new(k_str, AES.MODE_CBC, mac_str)
            iv_str = a32_to_str([ul_key[4], ul_key[5], ul_key[4], ul_key[5]])
            if file_size > 0:
                for chunk_start, chunk_size in get_chunks(file_size):
                    chunk = input_file.read(chunk_size)
                    upload_progress += len(chunk)

                    encryptor = AES.new(k_str, AES.MODE_CBC, iv_str)
                    for i in range(0, len(chunk) - 16, 16):
                        block = chunk[i:i + 16]
                        encryptor.encrypt(block)

                    if file_size > 16:
                        i += 16
                    else:
                        i = 0

                    block = chunk[i:i + 16]
                    if len(block) % 16:
                        block += b'\x00' * (-len(block) % 16)
                    mac_str = mac_encryptor.encrypt(encryptor.encrypt(block))

                    chunk = aes.encrypt(chunk)
                    async with ClientSession() as sess:
                        async with sess.post(f"{ul_url}/{chunk_start}", data=chunk, timeout=120) as resp:
                            completion_file_handle = await resp.text()
            else:
                async with ClientSession() as sess:
                    async with sess.post(f"{ul_url}/0", data='', timeout=120) as resp:
                        completion_file_handle = await resp.text()

            file_mac = str_to_a32(mac_str)

            # determine meta mac
            meta_mac = (file_mac[0] ^ file_mac[1], file_mac[2] ^ file_mac[3])

            attribs = {'n': filename}

            encrypt_attribs = base64_url_encode(encrypt_attr(attribs, ul_key[:4]))
            key = [
                ul_key[0] ^ ul_key[4], ul_key[1] ^ ul_key[5],
                ul_key[2] ^ meta_mac[0], ul_key[3] ^ meta_mac[1], ul_key[4],
                ul_key[5], meta_mac[0], meta_mac[1]
            ]
            encrypted_key = base64_url_encode(encrypt_key(a32_to_str(key), self.master_key))
            # update attributes
            data = await self._api_request({
                'a': 'p',
                't': self.root_id,
                'i': self.request_id,
                'n': [{
                    'h': completion_file_handle,
                    't': 0,
                    'a': encrypt_attribs,
                    'k': encrypted_key
                }]
            })
            return await self._get_upload_link(data)