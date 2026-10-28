"""Lossless native JSON value slices and length-prefixed local IPC."""
import asyncio
import json
import struct

IPC_LIMIT = 64 * 1024 * 1024
PARSE_LIMIT = 64 * 1024 * 1024


def member_raw(text, *path):
    """Return a member's original JSON spelling, including numbers/string escapes."""
    decoder = json.JSONDecoder()
    fragment = text.strip()
    for key in path:
        if not fragment.startswith('{'):
            raise ValueError('expected JSON object')
        pos = 1
        found = None
        while True:
            while pos < len(fragment) and fragment[pos].isspace():
                pos += 1
            if fragment[pos] == '}':
                break
            name, pos = decoder.raw_decode(fragment, pos)
            while fragment[pos].isspace():
                pos += 1
            if fragment[pos] != ':':
                raise ValueError('expected colon')
            pos += 1
            while fragment[pos].isspace():
                pos += 1
            start = pos
            _, pos = decoder.raw_decode(fragment, pos)
            if name == key:
                found = fragment[start:pos]
            while fragment[pos].isspace():
                pos += 1
            if fragment[pos] == '}':
                break
            if fragment[pos] != ',':
                raise ValueError('expected comma')
            pos += 1
        if found is None:
            raise KeyError(key)
        fragment = found
    return fragment


def array_items_raw(text):
    """Yield decoded values plus their exact lexical slices from a JSON array."""
    decoder = json.JSONDecoder()
    text = text.strip()
    if not text.startswith('['):
        raise ValueError('expected JSON array')
    pos = 1
    while True:
        while text[pos].isspace():
            pos += 1
        if text[pos] == ']':
            return
        start = pos
        value, pos = decoder.raw_decode(text, pos)
        yield value, text[start:pos]
        while text[pos].isspace():
            pos += 1
        if text[pos] == ']':
            return
        if text[pos] != ',':
            raise ValueError('expected comma')
        pos += 1


async def read_packet(reader):
    length = struct.unpack('!Q', await reader.readexactly(8))[0]
    if length > IPC_LIMIT:
        raise ValueError('IPC request exceeds explicit 64 MiB limit')
    value = json.loads((await reader.readexactly(length)).decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('IPC packet must be an object')
    return value


async def write_packet(writer, value):
    payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > IPC_LIMIT:
        raise ValueError('IPC response exceeds 64 MiB; use raw slice reads')
    writer.write(struct.pack('!Q', len(payload)))
    writer.write(payload)
    await writer.drain()
