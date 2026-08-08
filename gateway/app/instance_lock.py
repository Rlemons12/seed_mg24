import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO


class GatewayInstanceLock:
    """OS-released non-blocking lock preventing competing gateway owners."""

    def __init__(self, database_url: str, port: int) -> None:
        # A port represents one local gateway owner even when operators accidentally select different databases.
        key = hashlib.sha256(f"{Path.cwd().resolve()}|{port}".encode()).hexdigest()[:20]
        self.path = Path(tempfile.gettempdir()) / f"seed-mg24-gateway-{key}.lock"
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        try:
            handle = self.path.open("a+b")
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if "handle" in locals():
                handle.close()
            raise RuntimeError("Another dashboard instance is already using this database and sensor gateway.") from exc
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        self._file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None
