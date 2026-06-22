"""
test_core.py — Pruebas unitarias del módulo subscriber.core.

Valida la lógica de negocio (mutación de estado, alarma, eventos)
sin requerir red, broker MQTT ni servidor HTTP.

Ejecución:
    pytest tests/test_core.py -v
"""

import time

import pytest

from subscriber.core import (
    COMPARTMENT_COUNT,
    SystemState,
    acknowledge_alarm,
    apply_compartment_event,
    apply_heartbeat,
    apply_publisher_status,
    apply_summary,
    set_monitoring,
    state_to_dict,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def state() -> SystemState:
    """Estado limpio para cada test."""
    return SystemState()


# ─────────────────────────────────────────────────────────────────────────────
#  Estado inicial
# ─────────────────────────────────────────────────────────────────────────────

class TestInitialState:
    def test_all_compartments_closed(self, state: SystemState) -> None:
        assert all(not c.open for c in state.compartments)

    def test_compartment_count(self, state: SystemState) -> None:
        assert len(state.compartments) == COMPARTMENT_COUNT

    def test_alarm_inactive(self, state: SystemState) -> None:
        assert not state.alarm_active

    def test_monitoring_enabled(self, state: SystemState) -> None:
        assert state.monitoring_enabled

    def test_publisher_offline(self, state: SystemState) -> None:
        assert not state.publisher_online


# ─────────────────────────────────────────────────────────────────────────────
#  apply_compartment_event
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyCompartmentEvent:
    def test_open_compartment_1(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        assert state.compartments[0].open is True

    def test_close_compartment_1(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        apply_compartment_event(state, {"compartment_id": 1, "open": False, "sequence": 2})
        assert state.compartments[0].open is False

    def test_alarm_activates_on_open(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 3, "open": True, "sequence": 1})
        assert state.alarm_active is True

    def test_alarm_deactivates_when_all_closed(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 3, "open": True, "sequence": 1})
        apply_compartment_event(state, {"compartment_id": 3, "open": False, "sequence": 2})
        assert state.alarm_active is False

    def test_invalid_compartment_id_ignored(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 99, "open": True, "sequence": 1})
        assert all(not c.open for c in state.compartments)

    def test_zero_compartment_id_ignored(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 0, "open": True, "sequence": 1})
        assert all(not c.open for c in state.compartments)

    def test_sequence_number_updated(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 2, "open": True, "sequence": 42})
        assert state.last_sequence == 42

    def test_event_pushed_on_change(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        kinds = [e["kind"] for e in state.events]
        assert "compartment_change" in kinds

    def test_no_event_if_no_change(self, state: SystemState) -> None:
        # compartimento ya cerrado; enviar cerrado de nuevo no genera evento
        apply_compartment_event(state, {"compartment_id": 1, "open": False, "sequence": 1})
        comp_events = [e for e in state.events if e["kind"] == "compartment_change"]
        assert len(comp_events) == 0

    def test_all_seven_compartments_can_open(self, state: SystemState) -> None:
        for i in range(1, COMPARTMENT_COUNT + 1):
            apply_compartment_event(state, {"compartment_id": i, "open": True, "sequence": i})
        assert all(c.open for c in state.compartments)

    def test_compartment_7_boundary(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 7, "open": True, "sequence": 1})
        assert state.compartments[6].open is True

    def test_compartment_8_out_of_range(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 8, "open": True, "sequence": 1})
        assert all(not c.open for c in state.compartments)


# ─────────────────────────────────────────────────────────────────────────────
#  apply_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestApplySummary:
    def _make_summary(self, opens: list[bool], monitoring: bool = True, seq: int = 0) -> dict:
        return {
            "compartments": [
                {"compartment_id": i + 1, "open": opens[i]}
                for i in range(COMPARTMENT_COUNT)
            ],
            "monitoring_enabled": monitoring,
            "sequence": seq,
        }

    def test_reconciles_all_compartments(self, state: SystemState) -> None:
        opens = [True, False, True, False, False, True, False]
        apply_summary(state, self._make_summary(opens))
        for i, expected in enumerate(opens):
            assert state.compartments[i].open is expected

    def test_alarm_reflects_summary(self, state: SystemState) -> None:
        opens = [False] * COMPARTMENT_COUNT
        opens[4] = True
        apply_summary(state, self._make_summary(opens))
        assert state.alarm_active is True

    def test_monitoring_flag_propagated(self, state: SystemState) -> None:
        apply_summary(state, self._make_summary([False] * 7, monitoring=False))
        assert state.monitoring_enabled is False

    def test_alarm_inhibited_when_monitoring_disabled(self, state: SystemState) -> None:
        opens = [True] * COMPARTMENT_COUNT
        apply_summary(state, self._make_summary(opens, monitoring=False))
        assert state.alarm_active is False

    def test_sequence_updated(self, state: SystemState) -> None:
        apply_summary(state, self._make_summary([False] * 7, seq=100))
        assert state.last_sequence == 100


# ─────────────────────────────────────────────────────────────────────────────
#  Alarma
# ─────────────────────────────────────────────────────────────────────────────

class TestAlarm:
    def test_alarm_not_active_when_monitoring_disabled(self, state: SystemState) -> None:
        set_monitoring(state, False)
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        assert state.alarm_active is False

    def test_alarm_reactivates_when_monitoring_reenabled(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        set_monitoring(state, False)
        assert state.alarm_active is False
        set_monitoring(state, True)
        assert state.alarm_active is True

    def test_acknowledge_clears_flag(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 2, "open": True, "sequence": 1})
        acknowledge_alarm(state)
        assert state.alarm_acknowledged is True

    def test_acknowledge_resets_on_close(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 2, "open": True, "sequence": 1})
        acknowledge_alarm(state)
        apply_compartment_event(state, {"compartment_id": 2, "open": False, "sequence": 2})
        assert state.alarm_acknowledged is False

    def test_alarm_event_pushed_on_activation(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        alarm_events = [e for e in state.events if e["kind"] == "alarm_change"]
        assert len(alarm_events) == 1
        assert alarm_events[0]["active"] is True

    def test_alarm_event_pushed_on_deactivation(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        apply_compartment_event(state, {"compartment_id": 1, "open": False, "sequence": 2})
        alarm_events = [e for e in state.events if e["kind"] == "alarm_change"]
        assert len(alarm_events) == 2
        assert alarm_events[-1]["active"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  set_monitoring
# ─────────────────────────────────────────────────────────────────────────────

class TestSetMonitoring:
    def test_disable(self, state: SystemState) -> None:
        set_monitoring(state, False)
        assert state.monitoring_enabled is False

    def test_enable(self, state: SystemState) -> None:
        set_monitoring(state, False)
        set_monitoring(state, True)
        assert state.monitoring_enabled is True

    def test_event_pushed(self, state: SystemState) -> None:
        set_monitoring(state, False)
        kinds = [e["kind"] for e in state.events]
        assert "monitoring_change" in kinds


# ─────────────────────────────────────────────────────────────────────────────
#  apply_publisher_status
# ─────────────────────────────────────────────────────────────────────────────

class TestPublisherStatus:
    def test_online(self, state: SystemState) -> None:
        apply_publisher_status(state, {"status": "online", "ip": "192.168.1.50"})
        assert state.publisher_online is True
        assert state.publisher_ip == "192.168.1.50"

    def test_offline(self, state: SystemState) -> None:
        apply_publisher_status(state, {"status": "online", "ip": "192.168.1.50"})
        apply_publisher_status(state, {"status": "offline", "ip": "192.168.1.50"})
        assert state.publisher_online is False

    def test_event_pushed(self, state: SystemState) -> None:
        apply_publisher_status(state, {"status": "online", "ip": "10.0.0.1"})
        kinds = [e["kind"] for e in state.events]
        assert "publisher_status" in kinds


# ─────────────────────────────────────────────────────────────────────────────
#  apply_heartbeat
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyHeartbeat:
    def test_rssi_stored(self, state: SystemState) -> None:
        apply_heartbeat(state, {"rssi": -65, "uptime_ms": 12000, "open_count": 0})
        assert state.last_heartbeat_rssi == -65

    def test_uptime_stored(self, state: SystemState) -> None:
        apply_heartbeat(state, {"rssi": -70, "uptime_ms": 99000, "open_count": 0})
        assert state.last_heartbeat_uptime_ms == 99000

    def test_timestamp_updated(self, state: SystemState) -> None:
        before = time.time()
        apply_heartbeat(state, {"rssi": -60, "uptime_ms": 5000, "open_count": 0})
        assert state.last_heartbeat_at >= before


# ─────────────────────────────────────────────────────────────────────────────
#  state_to_dict
# ─────────────────────────────────────────────────────────────────────────────

class TestStateToDict:
    def test_returns_dict(self, state: SystemState) -> None:
        result = state_to_dict(state)
        assert isinstance(result, dict)

    def test_compartments_present(self, state: SystemState) -> None:
        result = state_to_dict(state)
        assert len(result["compartments"]) == COMPARTMENT_COUNT

    def test_open_count_correct(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        apply_compartment_event(state, {"compartment_id": 3, "open": True, "sequence": 2})
        result = state_to_dict(state)
        assert result["open_count"] == 2

    def test_events_reversed(self, state: SystemState) -> None:
        apply_compartment_event(state, {"compartment_id": 1, "open": True, "sequence": 1})
        apply_compartment_event(state, {"compartment_id": 2, "open": True, "sequence": 2})
        result = state_to_dict(state)
        # El evento más reciente debe aparecer primero
        assert result["events"][0]["ts"] >= result["events"][-1]["ts"]

    def test_alarm_fields_present(self, state: SystemState) -> None:
        result = state_to_dict(state)
        assert "alarm_active" in result
        assert "alarm_acknowledged" in result

    def test_monitoring_field_present(self, state: SystemState) -> None:
        result = state_to_dict(state)
        assert "monitoring_enabled" in result
