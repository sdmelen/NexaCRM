"""Create an API client for the configured model provider."""

from __future__ import annotations

import os

from openai import OpenAI

from nexacrm.config import AppConfig


def create_api_client(config: AppConfig, *, timeout: float) -> OpenAI:
    if config.api_provider == "proxyapi":
        return OpenAI(
            api_key=os.environ["PROXY_API_KEY"],
            base_url="https://api.proxyapi.ru/v1",
            timeout=timeout,
            max_retries=2,
        )
    return OpenAI(timeout=timeout, max_retries=2)
