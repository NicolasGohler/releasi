"""Human-like delay generation for anti-detection."""
from __future__ import annotations

import random
import asyncio

from linauto.config import get_settings


class DelayGenerator:
    """Generates random delays that mimic human behavior."""

    def __init__(self):
        self._settings = get_settings()

    async def action_delay(self):
        """Delay between major actions (e.g., between sending two connection requests)."""
        delay = random.uniform(
            self._settings.min_action_delay_seconds,
            self._settings.max_action_delay_seconds,
        )
        await asyncio.sleep(delay)

    async def page_delay(self):
        """Short delay after page navigation."""
        delay = random.uniform(
            self._settings.page_load_delay_min,
            self._settings.page_load_delay_max,
        )
        await asyncio.sleep(delay)

    async def micro_delay(self, min_s: float = 0.3, max_s: float = 1.5):
        """Very short delay between UI interactions (clicks, etc.)."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def type_character_delay(self):
        """Delay between keystrokes for human-like typing."""
        base_ms = random.randint(
            self._settings.typing_delay_min_ms,
            self._settings.typing_delay_max_ms,
        )
        # Occasional longer pause (thinking) — 8% chance
        if random.random() < 0.08:
            base_ms += random.randint(200, 500)
        await asyncio.sleep(base_ms / 1000.0)

    async def type_text(self, page_or_element, text: str):
        """Type text character by character with human-like delays."""
        for char in text:
            await page_or_element.press(f"{char}" if len(char) == 1 else char)
            await self.type_character_delay()
