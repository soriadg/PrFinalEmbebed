from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CompartmentState:
    compartment_id: int
    name: str
    is_open: bool = False
    total_openings: int = 0
    last_change_at: str = ""
    last_source_timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "compartment_id": self.compartment_id,
            "name": self.name,
            "is_open": self.is_open,
            "total_openings": self.total_openings,
            "last_change_at": self.last_change_at,
            "last_source_timestamp": self.last_source_timestamp,
        }


class DashboardState:
    def __init__(self, device_id: str = "reed-monitor-esp32", compartment_count: int = 7) -> None:
        self._lock = threading.RLock()
        self.device_id = device_id
        self.compartment_count = compartment_count
        self.monitoring_enabled = True
        self.publisher_online = False
        self.last_sequence: int | None = None
        self.last_snapshot_at = ""
        self.last_acknowledged_at = ""
        self.last_acknowledged_by = ""
        self.alarm_active = False
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=200)
        self.compartments = {
            index: CompartmentState(
                compartment_id=index,
                name=f"Compartimento {index}",
            )
            for index in range(1, compartment_count + 1)
        }

    def set_publisher_online(self, online: bool, source_timestamp: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if self.publisher_online == online:
                return []
            self.publisher_online = online
            event = self._append_event(
                "publisher_online" if online else "publisher_offline",
                {
                    "publisher_online": online,
                    "source_timestamp": source_timestamp or "",
                },
            )
            return [event]

    def update_from_snapshot(
        self,
        compartments: list[dict[str, Any]],
        sequence: int | None = None,
        source_timestamp: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            events: list[dict[str, Any]] = []
            for item in compartments:
                compartment_id = int(item["compartment_id"])
                events.extend(
                    self._set_compartment_state(
                        compartment_id=compartment_id,
                        is_open=bool(item["open"]),
                        source_timestamp=source_timestamp or "",
                    )
                )

            self.last_sequence = sequence
            self.last_snapshot_at = utc_now_iso()
            return events

    def update_compartment(
        self,
        compartment_id: int,
        is_open: bool,
        source_timestamp: str | None = None,
        sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            events = self._set_compartment_state(
                compartment_id=compartment_id,
                is_open=is_open,
                source_timestamp=source_timestamp or "",
            )
            self.last_sequence = sequence
            self.last_snapshot_at = utc_now_iso()
            return events

    def set_monitoring_enabled(self, enabled: bool, actor: str = "dashboard") -> list[dict[str, Any]]:
        with self._lock:
            if self.monitoring_enabled == enabled:
                return []
            self.monitoring_enabled = enabled
            events = [
                self._append_event(
                    "monitoring_enabled" if enabled else "monitoring_disabled",
                    {
                        "actor": actor,
                    },
                )
            ]
            events.extend(self._refresh_alarm_state())
            return events

    def acknowledge_alarm(self, actor: str) -> list[dict[str, Any]]:
        with self._lock:
            self.last_acknowledged_at = utc_now_iso()
            self.last_acknowledged_by = actor
            return [
                self._append_event(
                    "alarm_acknowledged",
                    {
                        "actor": actor,
                    },
                )
            ]

    def actuator_payload(self) -> dict[str, Any]:
        with self._lock:
            open_ids = [item.compartment_id for item in self.compartments.values() if item.is_open]
            return {
                "device_id": self.device_id,
                "timestamp": utc_now_iso(),
                "monitoring_enabled": self.monitoring_enabled,
                "publisher_online": self.publisher_online,
                "alarm_active": self.alarm_active,
                "open_count": len(open_ids),
                "open_compartments": open_ids,
                "last_acknowledged_at": self.last_acknowledged_at,
                "last_acknowledged_by": self.last_acknowledged_by,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            open_ids = [item.compartment_id for item in self.compartments.values() if item.is_open]
            return {
                "device_id": self.device_id,
                "publisher_online": self.publisher_online,
                "monitoring_enabled": self.monitoring_enabled,
                "alarm_active": self.alarm_active,
                "open_count": len(open_ids),
                "open_compartments": open_ids,
                "last_sequence": self.last_sequence,
                "last_snapshot_at": self.last_snapshot_at,
                "last_acknowledged_at": self.last_acknowledged_at,
                "last_acknowledged_by": self.last_acknowledged_by,
                "compartments": [item.to_dict() for item in self.compartments.values()],
                "recent_events": list(self.recent_events),
            }

    def _set_compartment_state(
        self,
        compartment_id: int,
        is_open: bool,
        source_timestamp: str,
    ) -> list[dict[str, Any]]:
        if compartment_id not in self.compartments:
            raise ValueError(f"Compartimento no soportado: {compartment_id}")

        compartment = self.compartments[compartment_id]
        if compartment.is_open == is_open:
            return []

        compartment.is_open = is_open
        compartment.last_change_at = utc_now_iso()
        compartment.last_source_timestamp = source_timestamp
        if is_open:
            compartment.total_openings += 1

        events = [
            self._append_event(
                "compartment_opened" if is_open else "compartment_closed",
                {
                    "compartment_id": compartment_id,
                    "compartment_name": compartment.name,
                    "source_timestamp": source_timestamp,
                },
            )
        ]
        events.extend(self._refresh_alarm_state())
        return events

    def _refresh_alarm_state(self) -> list[dict[str, Any]]:
        should_alarm = self.monitoring_enabled and any(
            item.is_open for item in self.compartments.values()
        )
        if should_alarm == self.alarm_active:
            return []

        self.alarm_active = should_alarm
        return [
            self._append_event(
                "alarm_raised" if should_alarm else "alarm_cleared",
                {
                    "open_compartments": [
                        item.compartment_id
                        for item in self.compartments.values()
                        if item.is_open
                    ],
                },
            )
        ]

    def _append_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = {
            "type": event_type,
            "timestamp": utc_now_iso(),
            **payload,
        }
        self.recent_events.appendleft(event)
        return event

