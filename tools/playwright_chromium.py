"""Общие настройки запуска Chromium для Playwright на сервере/VPS/Docker."""

from __future__ import annotations

import os
import sys


def chromium_launch_kwargs():
    """
    Аргументы для sync_playwright().chromium.launch(...).

    По умолчанию Playwright 1.57+ в headless поднимает отдельный бинарник
    chrome-headless-shell — на части Linux/VPS он сразу завершается («browser has been closed»).
    Явный channel=\"chromium\" включает полный Chrome-for-Testing (новый headless), обычно стабильнее.

    Переменные окружения:
    - PLAYWRIGHT_CHROMIUM_CHANNEL — переопределить канал (например chrome / msedge).
    - PLAYWRIGHT_HEADLESS_SHELL=1 — не задавать channel (оставить дефолт Playwright = shell).

    На Linux без дисплея часто нужны --no-sandbox и --disable-dev-shm-usage.
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

    use_shell = os.environ.get("PLAYWRIGHT_HEADLESS_SHELL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    channel = (os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or "").strip()

    if channel:
        kwargs["channel"] = channel
    elif not use_shell:
        kwargs["channel"] = "chromium"

    return kwargs
