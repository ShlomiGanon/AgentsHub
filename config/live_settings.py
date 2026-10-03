"""Persistent settings that can change while the system is running."""

import json
import os
from pathlib import Path


class SettingsStore:
    """Live retry, risk, lookback, and safe-mode values persisted next to the DB."""
    def __init__(
        self,
        db_path: str,
        starting_retry_count: int,
        starting_risk_threshold: float,
        starting_lookback_window_days: int,
        starting_safe_mode: bool = False,
        starting_rich_reports_enabled: bool = True,
    ):
        """Init."""

        self._settings_path = Path(f"{db_path}.settings.json")

        if self._settings_path.exists():
            self._values = json.loads(self._settings_path.read_text(encoding="utf-8"))
            changed = False
            if not isinstance(self._values.get("safe_mode"), bool):
                self._values["safe_mode"] = bool(starting_safe_mode)
                changed = True
            if not isinstance(self._values.get("rich_reports_enabled"), bool):
                self._values["rich_reports_enabled"] = bool(starting_rich_reports_enabled)
                changed = True
            # Unresolved-hold reminder/escalation/expiry (item 8): fixed defaults, not a
            # per-profile starting value like retry_count/risk_threshold above, since every
            # profile wants the same sender-facing SLA unless a commander changes it live.
            if not isinstance(self._values.get("hold_reminder_minutes"), (int, float)):
                self._values["hold_reminder_minutes"] = 10
                changed = True
            if not isinstance(self._values.get("hold_escalation_minutes"), (int, float)):
                self._values["hold_escalation_minutes"] = 30
                changed = True
            if not isinstance(self._values.get("hold_expiry_hours"), (int, float)):
                self._values["hold_expiry_hours"] = 2
                changed = True
            if changed:
                self._write()
        else:
            self._values = {
                "retry_count": starting_retry_count,
                "risk_threshold": starting_risk_threshold,
                "lookback_window_days": starting_lookback_window_days,
                "safe_mode": bool(starting_safe_mode),
                "rich_reports_enabled": bool(starting_rich_reports_enabled),
                "hold_reminder_minutes": 10,
                "hold_escalation_minutes": 30,
                "hold_expiry_hours": 2,
            }
            self._write()

    def get_retry_count(self) -> int:
        """Return the retry count."""

        return self._values["retry_count"]

    def get_risk_threshold(self) -> float:
        """Return the risk threshold."""

        return self._values["risk_threshold"]

    def get_lookback_window_days(self) -> int:
        """Return the lookback window days."""

        return self._values["lookback_window_days"]

    def get_safe_mode(self) -> bool:
        """Return the safe mode."""

        return bool(self._values["safe_mode"])

    def get_rich_reports_enabled(self) -> bool:
        """Return the rich reports enabled."""

        return bool(self._values["rich_reports_enabled"])

    def get_hold_reminder_minutes(self) -> float:
        """Return the hold reminder minutes."""

        return self._values["hold_reminder_minutes"]

    def get_hold_escalation_minutes(self) -> float:
        """Return the hold escalation minutes."""

        return self._values["hold_escalation_minutes"]

    def get_hold_expiry_hours(self) -> float:
        """Return the hold expiry hours."""

        return self._values["hold_expiry_hours"]

    def set_retry_count(self, value: int) -> None:
        """Persist the retry count."""

        self._values["retry_count"] = value
        self._write()

    def set_risk_threshold(self, value: float) -> None:
        """Persist the risk threshold."""

        self._values["risk_threshold"] = value
        self._write()

    def set_lookback_window_days(self, value: int) -> None:
        """Persist the lookback window days."""

        self._values["lookback_window_days"] = value
        self._write()

    def set_safe_mode(self, value: bool) -> None:
        """Persist the safe mode."""

        if not isinstance(value, bool):
            raise TypeError("safe_mode must be a bool")
        self._values["safe_mode"] = value
        self._write()

    def set_rich_reports_enabled(self, value: bool) -> None:
        """Persist the rich reports enabled."""

        if not isinstance(value, bool):
            raise TypeError("rich_reports_enabled must be a bool")
        self._values["rich_reports_enabled"] = value
        self._write()

    def set_hold_reminder_minutes(self, value: float) -> None:
        """Persist the hold reminder minutes."""

        if value <= 0:
            raise ValueError("hold_reminder_minutes must be positive")
        self._values["hold_reminder_minutes"] = value
        self._write()

    def set_hold_escalation_minutes(self, value: float) -> None:
        """Persist the hold escalation minutes."""

        if value <= 0:
            raise ValueError("hold_escalation_minutes must be positive")
        self._values["hold_escalation_minutes"] = value
        self._write()

    def set_hold_expiry_hours(self, value: float) -> None:
        """Persist the hold expiry hours."""

        if value <= 0:
            raise ValueError("hold_expiry_hours must be positive")
        self._values["hold_expiry_hours"] = value
        self._write()

    def _write(self) -> None:
        """Write."""

        tmp_path = self._settings_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(self._values), encoding="utf-8")
        os.replace(tmp_path, self._settings_path)
