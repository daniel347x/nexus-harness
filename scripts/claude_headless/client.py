"""Local operator/Python client. Never retries Send or converts Claude IDs to OpenCode IDs."""
import asyncio
import json
import socket
import struct

from .host import SOCKET_PATH
from .wire import IPC_LIMIT, read_packet, write_packet


class HostError(RuntimeError):
    pass


def unpack(response):
    if not response.get('ok'):
        raise HostError(f"{response.get('error')}: {response.get('message')}")
    return response['result']


# @beacon[
#   id=opc@ClaudeHostClient,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Exact local execution IDs and durable cursors; requests never implicitly resend or impose execution deadlines.,
# ]
class Client:
    def __init__(self, socket_path=SOCKET_PATH):
        self.socket_path = str(socket_path)

    async def context(self, execution_id, generation):
        """Operator read; MCP uses its launch capability instead, never caller-selected identity."""
        return await self.acall('get_context_usage', execution_id=execution_id, generation=generation)

    def call(self, method, **params):
        data = json.dumps({'method': method, 'params': params}, ensure_ascii=False).encode('utf-8')
        if len(data) > IPC_LIMIT:
            raise ValueError('IPC request exceeds 64 MiB')
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(self.socket_path)
            connection.sendall(struct.pack('!Q', len(data)) + data)
            def exact(length):
                result = bytearray()
                while len(result) < length:
                    chunk = connection.recv(min(65536, length - len(result)))
                    if not chunk:
                        raise HostError('IPC response lost: query exact receipt; NEVER repeat uncertain Send')
                    result.extend(chunk)
                return result
            length = struct.unpack('!Q', exact(8))[0]
            if length > IPC_LIMIT:
                raise HostError('oversize IPC response; use raw slices')
            return unpack(json.loads(exact(length)))

    async def acall(self, method, **params):
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        try:
            await write_packet(writer, {'method': method, 'params': params})
            return unpack(await read_packet(reader))
        finally:
            writer.close()
            await writer.wait_closed()

    async def subscribe(self, *, cursor=None, execution_id=None, limit=100):
        """Yield bounded durable pages. A cancelled/disconnected viewer has no execution effect."""
        while True:
            page = await self.acall('subscribe', cursor=cursor, execution_id=execution_id, limit=limit)
            cursor = page['cursor']
            yield page
