"""Runtime settings store (work_plan.md §1.7)."""

import json
import os
from pathlib import Path


class SettingsStore:
    def __init__(self, db_path: str, starting_retry_count: int, starting_risk_threshold: float, starting_lookback_window_days: int):
        self._settings_path = Path(f"{db_path}.settings.json")

        if self._settings_path.exists():
            self._values = json.loads(self._settings_path.read_text(encoding="utf-8"))
        else:
            self._values = {
                "retry_count": starting_retry_count,
                "risk_threshold": starting_risk_threshold,
                "lookback_window_days": starting_lookback_window_days,
            }
            self._write()

    def get_retry_count(self) -> int:
        return self._values["retry_count"]

    def get_risk_threshold(self) -> float:
        return self._values["risk_threshold"]

    def get_lookback_window_days(self) -> int:
        return self._values["lookback_window_days"]

    def set_retry_count(self, value: int) -> None:
        self._values["retry_count"] = value
        self._write()

    def set_risk_threshold(self, value: float) -> None:
        self._values["risk_threshold"] = value
        self._write()

    def set_lookback_window_days(self, value: int) -> None:
        self._values["lookback_window_days"] = value
        self._write()

    def _write(self) -> None:
        tmp_path = self._settings_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(self._values), encoding="utf-8")
        os.replace(tmp_path, self._settings_path)
