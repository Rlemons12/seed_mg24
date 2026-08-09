from __future__ import annotations

import secrets
from typing import Any

from .protocol import PREFIX, ProtocolError, decode_response, encode_request


class BootstrapSerialClient:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 3.0):
        if not port or timeout <= 0 or timeout > 30:
            raise ValueError("explicit port and bounded timeout required")
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required: python -m pip install pyserial") from exc
        self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=timeout, write_timeout=timeout)

    def close(self) -> None:
        self._serial.close()

    def request(self, action: str, **fields: Any) -> dict[str, Any]:
        request_id = secrets.token_hex(8)
        self._serial.reset_input_buffer()
        self._serial.write(encode_request(request_id, action, **fields))
        self._serial.flush()
        for _ in range(64):
            line = self._serial.readline()
            if not line:
                break
            if line.startswith(PREFIX.encode()):
                try:
                    response = decode_response(line, request_id, action)
                except ProtocolError as exc:
                    # A response already queued for an earlier bounded request is not this
                    # request's acknowledgement. Ignore it, but preserve all other failures.
                    if str(exc) == "response correlation mismatch":
                        continue
                    raise
                if response["status"] == "error":
                    raise ProtocolError(str(response.get("error_code", "device_error")))
                return response
        raise ProtocolError("bootstrap response timeout")

    def __enter__(self) -> BootstrapSerialClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
