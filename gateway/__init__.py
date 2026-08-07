"""Independently versioned Seed MG24 Raspberry Pi gateway product."""

from pathlib import Path

__version__ = (Path(__file__).with_name("VERSION").read_text(encoding="utf-8").strip())
