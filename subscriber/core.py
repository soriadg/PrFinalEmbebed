"""
core.py — Lógica de negocio del subscriber, sin dependencias de red ni I/O.

Expone SystemState y las funciones que lo mutan. Al mantener la lógica
separada del transporte MQTT y del servidor HTTP, el módulo es completamente
testeable en aislamiento (ver tests/test_core.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
#  Constantes
# ─────────────────────────────────────────────────────────────────────────────

COMPARTMENT_COUNT = 7
MAX_EVENTS = 50          # historial de eventos que conserva el dashboard


# ─────────────────────────────────────────────────────────────────────────────
#  Estado del sistema
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompartmentSnapshot:
    compartment_id: int
    open: bool
    last_changed_at: float = field(default_factory=time.time)


@dataclass
class SystemState:
    """Estado interno completo del subscriber/dashboard."""

    # Estado de cada compartimento (índice 0 = compartimento 1)
    compartments: list[CompartmentSnapshot] = field(
        default_factory=lambda: [
            CompartmentSnapshot(compartment_id=i + 1, open=False)
            for i in range(COMPARTMENT_COUNT)
        ]
    )

    # Alarma digital: activa cuando monitoreo habilitado Y alguno abierto
    alarm_active: bool = False
    alarm_acknowledged: bool = False

    # Control de monitoreo remoto
    monitoring_enabled: bool = True

    # Conectividad
    publisher_online: bool = False
    publisher_ip: str = ""
    subscriber_online: bool = True

    # Diagnóstico del publicador
    last_heartbeat_at: float = 0.0
    last_heartbeat_rssi: int = 0
    last_heartbeat_uptime_ms: int = 0

    # Historial de eventos para el dashboard
    events: list[dict[str, Any]] = field(default_factory=list)

    # Último número de secuencia recibido del ESP32
    last_sequence: int = 0


# ─────────────────────────────────────────────────────────────────────────────
#  Funciones de mutación
# ─────────────────────────────────────────────────────────────────────────────

def apply_compartment_event(state: SystemState, payload: dict[str, Any]) -> None:
    """Actualiza el estado de un compartimento individual."""
    cid: int = int(payload.get("compartment_id", 0))
    if not (1 <= cid <= COMPARTMENT_COUNT):
        return

    is_open: bool = bool(payload.get("open", False))
    seq: int = int(payload.get("sequence", 0))

    snapshot = state.compartments[cid - 1]
    changed = snapshot.open != is_open
    snapshot.open = is_open
    snapshot.last_changed_at = time.time()

    state.last_sequence = seq

    if changed:
        _push_event(
            state,
            kind="compartment_change",
            compartment_id=cid,
            open=is_open,
        )

    _recalculate_alarm(state)


def apply_summary(state: SystemState, payload: dict[str, Any]) -> None:
    """Reconcilia el estado completo desde un mensaje sensor/summary."""
    for item in payload.get("compartments", []):
        cid = int(item.get("compartment_id", 0))
        if 1 <= cid <= COMPARTMENT_COUNT:
            state.compartments[cid - 1].open = bool(item.get("open", False))

    monitoring = payload.get("monitoring_enabled")
    if monitoring is not None:
        state.monitoring_enabled = bool(monitoring)

    seq = payload.get("sequence")
    if seq is not None:
        state.last_sequence = int(seq)

    _recalculate_alarm(state)


def apply_heartbeat(state: SystemState, payload: dict[str, Any]) -> None:
    """Registra datos de diagnóstico del heartbeat."""
    state.last_heartbeat_at = time.time()
    state.last_heartbeat_rssi = int(payload.get("rssi", 0))
    state.last_heartbeat_uptime_ms = int(payload.get("uptime_ms", 0))


def apply_publisher_status(state: SystemState, payload: dict[str, Any]) -> None:
    """Actualiza disponibilidad del publicador."""
    status = payload.get("status", "")
    state.publisher_online = status == "online"
    state.publisher_ip = payload.get("ip", "")

    _push_event(
        state,
        kind="publisher_status",
        status=status,
        ip=state.publisher_ip,
    )


def set_monitoring(state: SystemState, enabled: bool) -> None:
    """Habilita o deshabilita el monitoreo (comando set-monitoring)."""
    state.monitoring_enabled = enabled
    _recalculate_alarm(state)
    _push_event(state, kind="monitoring_change", enabled=enabled)


def acknowledge_alarm(state: SystemState) -> None:
    """El operador reconoce la alarma activa."""
    state.alarm_acknowledged = True
    _push_event(state, kind="alarm_acknowledged")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _recalculate_alarm(state: SystemState) -> None:
    any_open = any(c.open for c in state.compartments)
    was_active = state.alarm_active
    state.alarm_active = state.monitoring_enabled and any_open

    # Limpiar reconocimiento si la condición de alarma ya no existe
    if not state.alarm_active:
        state.alarm_acknowledged = False

    # Emitir evento solo cuando cambia la activación
    if state.alarm_active != was_active:
        _push_event(state, kind="alarm_change", active=state.alarm_active)


def _push_event(state: SystemState, *, kind: str, **kwargs: Any) -> None:
    event = {"kind": kind, "ts": time.time(), **kwargs}
    state.events.append(event)
    if len(state.events) > MAX_EVENTS:
        state.events.pop(0)


# ─────────────────────────────────────────────────────────────────────────────
#  Serialización para la API HTTP del dashboard
# ─────────────────────────────────────────────────────────────────────────────

def state_to_dict(state: SystemState) -> dict[str, Any]:
    """Convierte el estado a un dict serializable a JSON."""
    return {
        "compartments": [
            {
                "compartment_id": c.compartment_id,
                "open": c.open,
                "last_changed_at": c.last_changed_at,
            }
            for c in state.compartments
        ],
        "open_count": sum(1 for c in state.compartments if c.open),
        "alarm_active": state.alarm_active,
        "alarm_acknowledged": state.alarm_acknowledged,
        "monitoring_enabled": state.monitoring_enabled,
        "publisher_online": state.publisher_online,
        "publisher_ip": state.publisher_ip,
        "last_heartbeat_at": state.last_heartbeat_at,
        "last_heartbeat_rssi": state.last_heartbeat_rssi,
        "last_heartbeat_uptime_ms": state.last_heartbeat_uptime_ms,
        "last_sequence": state.last_sequence,
        "events": list(reversed(state.events)),   # más reciente primero
    }
