"""Browsing noise — natural-looking activity between connection request sessions."""
from __future__ import annotations

import random
import asyncio
from typing import Optional

import structlog
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from releasi.linkedin import selectors
from releasi.linkedin.navigator import LinkedInNavigator
from releasi.safety.delays import DelayGenerator

logger = structlog.get_logger()


class BrowsingNoise:
    """Generate natural browsing behavior to blend in with real users."""

    def __init__(self, page: Page):
        self.page = page
        self.navigator = LinkedInNavigator(page)
        self.delay = DelayGenerator()

    async def view_random_profile(self) -> bool:
        """
        Visit a suggested profile and browse it briefly.
        Returns True if successful.
        """
        try:
            # Go to My Network page for suggested connections
            await self.page.goto(
                selectors.MY_NETWORK_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await self.delay.page_delay()

            # Scroll down a bit to load suggestions
            await self.page.mouse.wheel(0, random.randint(200, 500))
            await asyncio.sleep(random.uniform(1, 3))

            # Find profile links
            profile_links = await self.page.locator("a[href*='/in/']").all()
            if not profile_links:
                logger.debug("noise.no_profiles_found")
                return False

            # Pick a random profile
            link = random.choice(profile_links[:20])  # From first 20
            href = await link.get_attribute("href")
            if not href:
                return False

            # Navigate to the profile
            await link.click()
            await self.delay.page_delay()

            # Simulate reading the profile (5-15 seconds)
            read_time = random.uniform(5, 15)
            # Scroll down while "reading"
            scroll_steps = random.randint(1, 3)
            per_step = read_time / scroll_steps
            for _ in range(scroll_steps):
                if random.random() < 0.15:
                    await self.page.mouse.wheel(0, -random.randint(60, 150))
                else:
                    await self.page.mouse.wheel(0, random.randint(150, 400))
                await asyncio.sleep(per_step)

            logger.info("noise.profile_viewed", url=href)
            return True

        except (PlaywrightTimeout, Exception) as e:
            logger.debug("noise.profile_view_failed", error=str(e))
            return False

    async def like_feed_post(self) -> bool:
        """
        Scroll the feed and like one post.
        Returns True if a post was liked.
        """
        try:
            nav = await self.navigator.go_to_feed()
            if not nav.success:
                return False

            # Scroll to load some posts
            for _ in range(random.randint(2, 4)):
                await self.page.mouse.wheel(0, random.randint(300, 700))
                await asyncio.sleep(random.uniform(1.5, 4))

            # Find like buttons that haven't been pressed yet
            for sel in selectors.FEED_LIKE_BUTTON:
                try:
                    buttons = await self.page.locator(sel).all()
                    if buttons:
                        # Pick a random one from visible buttons
                        btn = random.choice(buttons[:10])
                        # Check if already liked (aria-pressed="true")
                        pressed = await btn.get_attribute("aria-pressed")
                        if pressed == "true":
                            continue
                        await btn.hover()
                        await asyncio.sleep(random.uniform(0.08, 0.3))
                        await btn.click()
                        await self.delay.micro_delay(0.5, 1.5)
                        logger.info("noise.post_liked")
                        return True
                except (PlaywrightTimeout, Exception):
                    continue

            logger.debug("noise.no_likeable_post_found")
            return False

        except (PlaywrightTimeout, Exception) as e:
            logger.debug("noise.like_failed", error=str(e))
            return False

    async def scroll_feed(self, duration_seconds: Optional[float] = None) -> None:
        """
        Just scroll the feed naturally for a given duration.
        Simulates a user casually browsing their feed.
        """
        if duration_seconds is None:
            duration_seconds = random.uniform(10, 30)

        try:
            nav = await self.navigator.go_to_feed()
            if not nav.success:
                return

            elapsed = 0
            while elapsed < duration_seconds:
                if random.random() < 0.15:
                    # Occasional back-scroll (human-like)
                    await self.page.mouse.wheel(0, -random.randint(100, 250))
                else:
                    scroll_amount = random.randint(200, 600)
                    delta_x = random.randint(-3, 3)
                    if random.random() < 0.25:
                        # PageDown instead of mouse wheel (~25% of forward scrolls)
                        await self.page.keyboard.press("PageDown")
                    else:
                        await self.page.mouse.wheel(delta_x, scroll_amount)
                pause = random.uniform(2, 6)
                await asyncio.sleep(pause)
                elapsed += pause

            logger.debug("noise.feed_scrolled", duration=round(duration_seconds, 1))

        except (PlaywrightTimeout, Exception) as e:
            logger.debug("noise.scroll_failed", error=str(e))
