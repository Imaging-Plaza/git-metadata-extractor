from __future__ import annotations

import logging

from src.v2.observability.context import RunContext

UNKNOWN_RUN_ID = "n/a"


class RunIdLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        existing_run_id = getattr(record, "run_id", None)
        if isinstance(existing_run_id, str) and existing_run_id:
            return True
        run_id = RunContext.get_run_id()
        record.run_id = run_id if run_id else UNKNOWN_RUN_ID
        return True
