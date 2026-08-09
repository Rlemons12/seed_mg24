import asyncio
from datetime import UTC, datetime

from fastapi import WebSocket

from gateway.app.schemas import WebSocketEvent


class WebSocketManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, event: str, device_id: str | None, data: dict) -> None:
        message = WebSocketEvent(event=event, device_id=device_id, timestamp=datetime.now(UTC), data=data)
        async with self._lock:
            clients = list(self._clients)
        failed: list[WebSocket] = []
        for client in clients:
            try:
                await asyncio.wait_for(client.send_text(message.model_dump_json()), timeout=2.0)
            except Exception:
                failed.append(client)
        if failed:
            async with self._lock:
                self._clients.difference_update(failed)
