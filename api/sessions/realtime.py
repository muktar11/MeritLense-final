import json
from collections import defaultdict

from asgiref.sync import async_to_sync


class InterviewSessionSocketRegistry:
    _connections = defaultdict(dict)

    @classmethod
    async def register(cls, session_key, connection_id, queue, loop):
        cls._connections[session_key][connection_id] = (loop, queue)

    @classmethod
    async def unregister(cls, session_key, connection_id):
        cls._connections.get(session_key, {}).pop(connection_id, None)
        if session_key in cls._connections and not cls._connections[session_key]:
            cls._connections.pop(session_key, None)

    @classmethod
    async def broadcast(cls, session_key, payload):
        message = json.dumps(payload)
        for loop, queue in list(cls._connections.get(session_key, {}).values()):
            loop.call_soon_threadsafe(queue.put_nowait, message)

    @classmethod
    def broadcast_sync(cls, session_key, payload):
        async_to_sync(cls.broadcast)(session_key, payload)
