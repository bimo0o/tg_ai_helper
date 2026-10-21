import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlsplit

import aiohttp

from app.billing import decimal_value

logger = logging.getLogger(__name__)
TEMPERATURES = (0.0, 0.3, 0.7, 1.0)


@dataclass(frozen=True)
class Generation:
    ok: bool
    text: str = ""
    error_kind: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    citations: tuple[str, ...] = ()
    cost_usd: Decimal | None = None


def parse_response(data: object) -> Generation:
    usage = {}
    cost = None
    try:
        if not isinstance(data, dict):
            raise ValueError
        raw_usage = data.get("usage") or {}
        if isinstance(raw_usage, dict):
            usage = {
                key: raw_usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if type(raw_usage.get(key)) is int and raw_usage[key] >= 0
            }
            cost = decimal_value(raw_usage.get("cost"))
            if cost is not None and cost < 0:
                cost = None
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            return Generation(False, error_kind="incomplete", usage=usage, cost_usd=cost)
        message = choice["message"]
        text = message["content"]
        if not isinstance(text, str):
            raise ValueError
        if not text.strip():
            return Generation(False, error_kind="empty", usage=usage, cost_usd=cost)
        citations = []
        for annotation in message.get("annotations") or []:
            if not isinstance(annotation, dict):
                continue
            citation = annotation.get("url_citation")
            url = citation.get("url") if isinstance(citation, dict) else None
            if not isinstance(url, str):
                continue
            parsed = urlsplit(url)
            if (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not any(char.isspace() for char in url)
                and url not in citations
            ):
                citations.append(url)
        return Generation(True, text=text, usage=usage, citations=tuple(citations), cost_usd=cost)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return Generation(False, error_kind="invalid", usage=usage, cost_usd=cost)


class LLMClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str, timeout=30):
        self.session = session
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.timeout = timeout

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        *,
        web=False,
        max_output_tokens=1500,
    ) -> Generation:
        if type(temperature) not in {float, int} or temperature not in TEMPERATURES:
            return Generation(False, error_kind="settings")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
            return Generation(False, error_kind="settings")
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        logger.info("LLM start request_id=%s model=%s", request_id, model)
        result = Generation(False, error_kind="service")
        try:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
                "max_tokens": max_output_tokens,
                "provider": {"require_parameters": True},
            }
            if urlsplit(self.url).hostname == "openrouter.ai":
                payload["provider"]["sort"] = "throughput"
                if model in {"deepseek/deepseek-v3.2", "deepseek/deepseek-v4.1-flash"}:
                    payload["reasoning"] = {"enabled": False}
            if web:
                payload["plugins"] = [{"id": "web", "engine": "exa", "max_results": 3}]
            async with asyncio.timeout(self.timeout):
                async with self.session.post(
                    self.url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    allow_redirects=False,
                ) as response:
                    if response.status == 200:
                        try:
                            result = parse_response(await response.json())
                        except (ValueError, aiohttp.ContentTypeError):
                            result = Generation(False, error_kind="invalid")
                    else:
                        result = Generation(False, error_kind="http")
        except TimeoutError:
            result = Generation(False, error_kind="timeout")
        except (aiohttp.ClientError, OSError):
            result = Generation(False, error_kind="service")
        finally:
            logger.info(
                "LLM end request_id=%s model=%s duration_ms=%d result=%s",
                request_id,
                model,
                int((time.monotonic() - started) * 1000),
                "ok" if result.ok else result.error_kind,
            )
        return result
