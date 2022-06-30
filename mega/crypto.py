from Crypto.Cipher import AES
from json import dumps as jdumps
from base64 import b64decode, b64encode
from struct import pack, unpack
from binascii import hexlify
from random import choice

def getAES(key):
    return AES.new(key, AES.MODE_CBC, b'\x00' * 16)

def aes_cbc_encrypt(data, key):
    return getAES(key).encrypt(data)

def aes_cbc_decrypt(data, key):
    return getAES(key).decrypt(data)

def prepare_key(password):
    password = bytes(password, "utf8")
    password += b'\x00' * (-(l:=len(password)) % 16)
    pkey = b"\x93\xC4\x67\xE3\x7D\xB0\xC7\xA4\xD1\xBE\x3F\x81\x01\x52\xCB\x56"
    keys = []
    for p in range(0, l, 16):
        keys.append(password[p:p+16])
    for r in range(0x10000):
        for a in keys:
            pkey = aes_cbc_encrypt(pkey, a)
    return pkey

def username_hash(email, key):
    email = bytes(email, "utf8")
    h = bytearray(b"\x00"*16)
    for i in range(len(email)):
        h[i % 16] ^= email[i]
    h = bytes(h)
    for i in range(0x4000):
        h = aes_cbc_encrypt(h, key)
    oh = h[:4]+h[8:12]
    return base64_url_encode(oh)

def encrypt_key(data, key):
    return b"".join((aes_cbc_encrypt(data[i:i + 16], key)) for i in range(0, len(data), 16))

def decrypt_key(data, key):
    return b"".join((aes_cbc_decrypt(data[i:i + 16], key)) for i in range(0, len(data), 16))

def encrypt_attr(attr, key):
    attr = bytes('MEGA' + jdumps(attr), "utf8")
    if len(attr) % 16:
        attr += b'\0' * (16 - len(attr) % 16)
    return aes_cbc_encrypt(attr, a32_to_str(key))

def a32_to_str(a):
    return pack('>%dI' % len(a), *a)

def str_to_a32(b):
    if isinstance(b, str):
        b = bytes(b, "utf8")
    if len(b) % 4:
        b += b'\0' * (4 - len(b) % 4)
    return unpack('>%dI' % (len(b) / 4), b)

def mpi_to_int(s):
    return int(hexlify(s[2:]), 16)

def extended_gcd(a, b):
    if a == 0:
        return (b, 0, 1)
    else:
        g, y, x = extended_gcd(b % a, a)
        return (g, x - (b // a) * y, y)

def modular_inverse(a, m):
    g, x, y = extended_gcd(a, m)
    if g != 1:
        raise Exception('modular inverse does not exist')
    else:
        return x % m

def base64_url_decode(data):
    if data is None:
        return None
    data += '=' * (-len(data) % 4)
    for search, replace in (('-', '+'), ('_', '/'), (',', '')):
        data = data.replace(search, replace)
    return b64decode(data)

def base64_url_encode(data):
    data = b64encode(data).decode("utf8")
    for search, replace in (('+', '-'), ('/', '_'), ('=', '')):
        data = data.replace(search, replace)
    return data

def get_chunks(size):
    p = 0
    s = 0x20000
    while p + s < size:
        yield (p, s)
        p += s
        if s < 0x100000:
            s += 0x20000
    yield (p, size - p)

def make_id(length):
    text = ''
    possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    for i in range(length):
        text += choice(possible)
    return text
