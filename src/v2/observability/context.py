from __future__ import annotations

from contextvars import ContextVar, Token

RUN_ID_DEFAULT = ""


class RunContext:
    run_id: ContextVar[str] = ContextVar("v2_run_id", default=RUN_ID_DEFAULT)

    @classmethod
    def set_run_id(cls, run_id: str) -> Token[str]:
        return cls.run_id.set(run_id)

    @classmethod
    def get_run_id(cls) -> str:
        return cls.run_id.get()

    @classmethod
    def reset(cls, token: Token[str]) -> None:
        cls.run_id.reset(token)
