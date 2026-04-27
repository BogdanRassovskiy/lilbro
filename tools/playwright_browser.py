"""Запуск браузера Playwright: Chromium (предпочтительно) и запасной Firefox."""

from __future__ import annotations

import os
import sys
from typing import Any


def chromium_launch_kwargs():
    """
    Аргументы для chromium.launch(**kwargs).

    channel=\"chromium\" — полный Chrome-for-Testing (не chrome-headless-shell).

    PLAYWRIGHT_CHROMIUM_CHANNEL, PLAYWRIGHT_HEADLESS_SHELL — см. раньше в проекте.
    """
    args = [
        "--disable-dev-shm-usage",
        "--disable-crashpad",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-first-run",
    ]
    if sys.platform.startswith("linux"):
        args.extend(
            [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-background-networking",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
            ]
        )

    kwargs: dict[str, Any] = {"headless": True, "args": args}

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


def firefox_launch_kwargs():
    """Минимальные опции для Firefox в headless на сервере."""
    return {"headless": True}


def sync_launch_browser(p) -> tuple[Any, str]:
    """
    Запускает браузер для sync_playwright().

    По умолчанию: сначала Chromium, при ошибке — Firefox (нужен ``playwright install firefox``).

    PLAYWRIGHT_BROWSER=chromium | firefox — использовать только указанный движок (без fallback).
    """
    pref = (os.environ.get("PLAYWRIGHT_BROWSER") or "").strip().lower()

    engines: list[tuple[str, Any]] = []
    if pref == "firefox":
        engines = [("firefox", lambda: p.firefox.launch(**firefox_launch_kwargs()))]
    elif pref == "chromium":
        engines = [("chromium", lambda: p.chromium.launch(**chromium_launch_kwargs()))]
    else:
        engines = [
            ("chromium", lambda: p.chromium.launch(**chromium_launch_kwargs())),
            ("firefox", lambda: p.firefox.launch(**firefox_launch_kwargs())),
        ]

    last_err: BaseException | None = None
    for name, factory in engines:
        try:
            browser = factory()
            return browser, name
        except BaseException as e:
            last_err = e
            continue

    msg = (
        "Не удалось запустить браузер Playwright (ни Chromium, ни Firefox). "
        "Установите: python -m playwright install chromium firefox && "
        "sudo python -m playwright install-deps chromium "
        "(и при необходимости install-deps firefox). "
        "Или задайте PLAYWRIGHT_BROWSER=firefox и поставьте только Firefox."
    )
    raise RuntimeError(msg) from last_err
