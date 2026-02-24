from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from src.v2.config import V2Config
from src.v2.observability import bootstrap


@dataclass
class _FakeLogfire:
    configure_calls: list[dict[str, Any]] = field(default_factory=list)
    fastapi_calls: list[FastAPI] = field(default_factory=list)
    pydantic_ai_calls: int = 0

    def configure(self, **kwargs: Any) -> None:
        self.configure_calls.append(kwargs)

    def instrument_fastapi(self, app: FastAPI) -> None:
        self.fastapi_calls.append(app)

    def instrument_pydantic_ai(self) -> None:
        self.pydantic_ai_calls += 1


def _config(*, enabled: bool, token: str | None = None) -> V2Config:
    return V2Config(
        V2_ENABLE_LOGFIRE=enabled,
        LOGFIRE_TOKEN=token,
    )


def test_initialize_logfire_enabled_without_token_uses_if_token_present(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    app = FastAPI()
    monkeypatch.setattr(bootstrap, "_get_logfire_module", lambda: fake_logfire)

    initialized = bootstrap.initialize_logfire(_config(enabled=True), app=app)

    assert initialized is True
    assert fake_logfire.configure_calls == [{"send_to_logfire": "if-token-present"}]
    assert fake_logfire.fastapi_calls == [app]
    assert fake_logfire.pydantic_ai_calls == 1


def test_initialize_logfire_disabled_skips_configuration(
    monkeypatch,
) -> None:
    getter_calls = 0

    def _getter() -> _FakeLogfire:
        nonlocal getter_calls
        getter_calls += 1
        return _FakeLogfire()

    monkeypatch.setattr(bootstrap, "_get_logfire_module", _getter)

    initialized = bootstrap.initialize_logfire(_config(enabled=False))

    assert initialized is False
    assert getter_calls == 0


def test_initialize_logfire_enabled_with_token_passes_token(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(bootstrap, "_get_logfire_module", lambda: fake_logfire)

    token_value = "abc123"  # noqa: S105
    initialized = bootstrap.initialize_logfire(_config(enabled=True, token=token_value))

    assert initialized is True
    assert fake_logfire.configure_calls == [
        {
            "send_to_logfire": "if-token-present",
            "token": token_value,
        },
    ]
    assert fake_logfire.fastapi_calls == []
    assert fake_logfire.pydantic_ai_calls == 1


def test_initialize_logfire_missing_dependency_does_not_raise(
    monkeypatch,
) -> None:
    monkeypatch.setattr(bootstrap, "_get_logfire_module", lambda: None)

    initialized = bootstrap.initialize_logfire(_config(enabled=True))

    assert initialized is False


def test_initialize_logfire_handles_runtime_errors_without_crashing(
    monkeypatch,
) -> None:
    class _BrokenLogfire(_FakeLogfire):
        def configure(self, **kwargs: Any) -> None:  # noqa: ARG002
            error_message = "boom"
            raise RuntimeError(error_message)

    monkeypatch.setattr(bootstrap, "_get_logfire_module", lambda: _BrokenLogfire())

    initialized = bootstrap.initialize_logfire(_config(enabled=True), app=FastAPI())

    assert initialized is False
