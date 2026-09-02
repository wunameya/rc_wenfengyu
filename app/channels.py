from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


VARIABLE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")
SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key"}


class ChannelError(ValueError):
    pass


@dataclass(frozen=True)
class SecretHeader:
    env: str
    prefix: str = ""


@dataclass(frozen=True)
class Channel:
    name: str
    method: str
    url: str
    headers: dict[str, str]
    secret_headers: dict[str, SecretHeader]
    body: Any
    timeout_seconds: float
    max_attempts: int
    max_concurrency: int
    base_retry_seconds: float
    max_retry_seconds: float

    def secret_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for header, secret in self.secret_headers.items():
            value = os.getenv(secret.env)
            if not value:
                raise ChannelError(f"渠道 {self.name} 缺少环境变量 {secret.env}")
            values[header] = f"{secret.prefix}{value}"
        return values


class ChannelRegistry:
    def __init__(self, channels: dict[str, Channel]):
        self._channels = channels

    @classmethod
    def from_file(cls, path: Path) -> "ChannelRegistry":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ChannelError(f"渠道配置不存在: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ChannelError(f"渠道配置不是合法 JSON: {exc}") from exc

        channels: dict[str, Channel] = {}
        for name, raw in data.get("channels", {}).items():
            parsed = urlparse(raw["url"])
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ChannelError(f"渠道 {name} 的 URL 无效")
            secret_headers = {
                header: SecretHeader(env=value["env"], prefix=value.get("prefix", ""))
                for header, value in raw.get("secret_headers", {}).items()
            }
            if "max_retries" in raw:
                max_retries = int(raw["max_retries"])
                if not 0 <= max_retries <= 10:
                    raise ChannelError(f"渠道 {name} 的 max_retries 必须在 0 到 10 之间")
                max_attempts = max_retries + 1
            else:
                max_attempts = int(raw.get("max_attempts", 11))
                if not 1 <= max_attempts <= 11:
                    raise ChannelError(f"渠道 {name} 的 max_attempts 必须在 1 到 11 之间")
            channels[name] = Channel(
                name=name,
                method=raw.get("method", "POST").upper(),
                url=raw["url"],
                headers=raw.get("headers", {"Content-Type": "application/json"}),
                secret_headers=secret_headers,
                body=raw.get("body", {}),
                timeout_seconds=float(raw.get("timeout_seconds", 5)),
                max_attempts=max_attempts,
                max_concurrency=int(raw.get("max_concurrency", 5)),
                base_retry_seconds=float(raw.get("base_retry_seconds", 10)),
                max_retry_seconds=float(raw.get("max_retry_seconds", 86400)),
            )
        if not channels:
            raise ChannelError("至少需要配置一个通知渠道")
        return cls(channels)

    def get(self, name: str) -> Channel:
        try:
            return self._channels[name]
        except KeyError as exc:
            raise ChannelError(f"未知渠道: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._channels)


def render_channel(channel: Channel, variables: dict[str, Any]) -> tuple[str, dict, Any]:
    url = _render(channel.url, variables)
    configured_url = urlparse(channel.url)
    rendered_url = urlparse(url)
    if (rendered_url.scheme, rendered_url.netloc) != (configured_url.scheme, configured_url.netloc):
        raise ChannelError("模板变量不能改变渠道 URL 的协议或主机")
    headers = _render(channel.headers, variables)
    for header in channel.secret_headers:
        headers[header] = "***REDACTED***"
    return url, redact_headers(headers), _render(channel.body, variables)


def _render(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, variables) for item in value]
    if not isinstance(value, str):
        return value

    full_match = VARIABLE_PATTERN.fullmatch(value)
    if full_match:
        return _lookup(variables, full_match.group(1))

    def replace(match: re.Match[str]) -> str:
        return str(_lookup(variables, match.group(1)))

    return VARIABLE_PATTERN.sub(replace, value)


def _lookup(variables: dict[str, Any], path: str) -> Any:
    current: Any = variables
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ChannelError(f"模板变量缺失: {path}")
        current = current[part]
    return current


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "***REDACTED***" if key.lower() in SENSITIVE_HEADERS else value
        for key, value in headers.items()
    }

