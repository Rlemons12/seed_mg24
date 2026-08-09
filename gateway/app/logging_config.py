import logging
from collections import defaultdict
from time import monotonic


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class RateLimitedLogger:
    def __init__(self, logger: logging.Logger, interval_seconds: float = 10.0) -> None:
        self.logger = logger
        self.interval_seconds = interval_seconds
        self._last: dict[str, float] = defaultdict(lambda: float("-inf"))

    def warning(self, key: str, message: str, *args: object) -> None:
        now = monotonic()
        if now - self._last[key] >= self.interval_seconds:
            self._last[key] = now
            self.logger.warning(message, *args)
