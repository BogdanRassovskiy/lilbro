"""Общие настройки запуска Chromium для Playwright на сервере/VPS/Docker."""

from __future__ import annotations

import os
import sys


def chromium_launch_kwargs():
    """
    Аргументы для sync_playwright().chromium.launch(...).

    На Linux без настоящего дисплея процесс часто падает без --no-sandbox /
    --disable-dev-shm-usage (ошибка «browser has been closed» сразу после launch).

    PLAYWRIGHT_CHROMIUM_CHANNEL — опционально, например \"chrome\" если установлен системный Chrome.
    """
    args = [
        "--disable-dev-shm-usage",
        "--disable-crashpad",
    ]
    if sys.platform.startswith("linux"):
        args.extend(
            [
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ]
        )

    kwargs = {"headless": True, "args": args}
    channel = (os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or "").strip()
    if channel:
        kwargs["channel"] = channel
    return kwargs
