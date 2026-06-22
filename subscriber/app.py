"""
app.py — Punto de entrada del subscriber/dashboard.

Conecta con el broker MQTT, procesa mensajes del ESP32 y sirve
un dashboard web en el puerto 8080 con Server-Sent Events (SSE)
para actualizaciones en tiempo real sin frameworks adicionales.

Ejecución local:
    python -m subscriber.app

Ejecución con Docker Compose:
    docker compose up --build
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import paho.mqtt.client as mqtt

from subscriber.core import (
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
#  Configuración (sobreescribible con variables de entorno)
# ─────────────────────────────────────────────────────────────────────────────

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "escom/iot/reed-monitor")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
SUBSCRIBER_ID = "dashboard-subscriber"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("subscriber")

# ─────────────────────────────────────────────────────────────────────────────
#  Estado global compartido entre hilos
# ─────────────────────────────────────────────────────────────────────────────

state = SystemState()
state_lock = threading.Lock()

# Cola de eventos para Server-Sent Events; cada ítem es un JSON str
sse_queue: queue.Queue[str] = queue.Queue(maxsize=200)


def _notify_sse() -> None:
    """Empuja el estado actual a la cola SSE."""
    try:
        with state_lock:
            data = state_to_dict(state)
        sse_queue.put_nowait(json.dumps(data))
    except queue.Full:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Cliente MQTT
# ─────────────────────────────────────────────────────────────────────────────

def _on_connect(client: mqtt.Client, _userdata: Any, _flags: Any, rc: int) -> None:
    if rc != 0:
        log.error("MQTT conexión rechazada, rc=%d", rc)
        return

    log.info("MQTT conectado a %s:%d", MQTT_HOST, MQTT_PORT)

    root = MQTT_TOPIC_ROOT
    subscriptions = [
        (f"{root}/publisher/status", 1),
        (f"{root}/sensor/compartment/+", 1),
        (f"{root}/sensor/summary", 1),
        (f"{root}/sensor/heartbeat", 1),
    ]
    client.subscribe(subscriptions)

    # Anunciar disponibilidad del subscriber
    payload = json.dumps({"device_id": SUBSCRIBER_ID, "status": "online"})
    client.publish(f"{root}/subscriber/status", payload, qos=1, retain=True)

    with state_lock:
        state.publisher_online = False  # se actualizará con publisher/status

    _notify_sse()


def _on_disconnect(client: mqtt.Client, _userdata: Any, rc: int) -> None:
    log.warning("MQTT desconectado, rc=%d — reconectando…", rc)


def _on_message(client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
    topic: str = msg.topic
    root = MQTT_TOPIC_ROOT

    try:
        payload: dict[str, Any] = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Payload inválido en %s", topic)
        return

    with state_lock:
        if topic == f"{root}/publisher/status":
            apply_publisher_status(state, payload)

        elif topic.startswith(f"{root}/sensor/compartment/"):
            apply_compartment_event(state, payload)
            # Publicar estado de alarma actualizado
            _publish_alarm_status(client)

        elif topic == f"{root}/sensor/summary":
            apply_summary(state, payload)
            _publish_alarm_status(client)

        elif topic == f"{root}/sensor/heartbeat":
            apply_heartbeat(state, payload)

    _notify_sse()


def _publish_alarm_status(client: mqtt.Client) -> None:
    """Publica el estado actual de la alarma (actuator/alarm)."""
    alarm_on = state.alarm_active and not state.alarm_acknowledged
    payload = json.dumps({
        "device_id": SUBSCRIBER_ID,
        "alarm": alarm_on,
        "monitoring_enabled": state.monitoring_enabled,
        "ts": time.time(),
    })
    client.publish(
        f"{MQTT_TOPIC_ROOT}/actuator/alarm",
        payload,
        qos=1,
        retain=True,
    )


def start_mqtt() -> mqtt.Client:
    client = mqtt.Client(client_id=SUBSCRIBER_ID, clean_session=True)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message

    # LWT: si el dashboard muere, el broker avisa
    lwt_payload = json.dumps({"device_id": SUBSCRIBER_ID, "status": "offline"})
    client.will_set(
        f"{MQTT_TOPIC_ROOT}/subscriber/status",
        lwt_payload,
        qos=1,
        retain=True,
    )

    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    return client


# ─────────────────────────────────────────────────────────────────────────────
#  Servidor HTTP / dashboard
# ─────────────────────────────────────────────────────────────────────────────

# HTML del dashboard (cadena embebida para no requerir archivos estáticos)
DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor Reed Switch — ESCOM IoT</title>
<style>
  :root { --ok: #22c55e; --warn: #ef4444; --muted: #64748b; --bg: #0f172a; --card: #1e293b; --accent: #1d4ed8; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: #e2e8f0; font-family: system-ui, sans-serif; padding: 1.5rem; }
  h1 { font-size: 1.25rem; margin-bottom: 1rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: .75rem; margin-bottom: 1.5rem; }
  .card { background: var(--card); border-radius: .5rem; padding: 1rem; text-align: center; }
  .card .label { font-size: .75rem; color: var(--muted); margin-bottom: .4rem; }
  .card .state { font-size: 1.1rem; font-weight: 700; }
  .open  { color: var(--warn); }
  .closed{ color: var(--ok); }
  .alarm-bar { background: var(--warn); color: #fff; padding: .6rem 1rem; border-radius: .4rem;
               margin-bottom: 1rem; display: none; align-items: center; justify-content: space-between; }
  .alarm-bar.active { display: flex; }
  .btn { background: var(--accent); color: #fff; border: none; padding: .4rem .9rem;
         border-radius: .3rem; cursor: pointer; font-size: .85rem; }
  .btn:hover { opacity: .85; }
  .controls { display: flex; gap: .5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .status-row { font-size: .8rem; color: var(--muted); margin-bottom: 1rem; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
  .dot.on  { background: var(--ok); }
  .dot.off { background: var(--warn); }
  .events { background: var(--card); border-radius: .5rem; padding: 1rem; max-height: 260px; overflow-y: auto; }
  .events h2 { font-size: .9rem; margin-bottom: .6rem; }
  .event-row { font-size: .75rem; color: var(--muted); border-bottom: 1px solid #334155; padding: .3rem 0; }
  .event-row span { color: #94a3b8; }
</style>
</head>
<body>
<h1>Monitor de Compartimentos — Reed Switch IoT</h1>

<div id="alarm-bar" class="alarm-bar">
  <span>⚠ ALARMA: compartimento(s) abierto(s)</span>
  <button class="btn" onclick="ackAlarm()">Reconocer</button>
</div>

<div class="status-row" id="status-row">Conectando…</div>

<div class="grid" id="grid"></div>

<div class="controls">
  <button class="btn" onclick="requestSync()">↺ Solicitar sync</button>
  <button class="btn" id="btn-monitoring" onclick="toggleMonitoring()">Deshabilitar monitoreo</button>
</div>

<div class="events">
  <h2>Eventos recientes</h2>
  <div id="events-list"></div>
</div>

<script>
let monitoringEnabled = true;

function fmt(ts){ return new Date(ts*1000).toLocaleTimeString(); }

function render(data){
  // grid de compartimentos
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  data.compartments.forEach(c => {
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = \`<div class="label">Compartimento \${c.compartment_id}</div>
      <div class="state \${c.open ? 'open' : 'closed'}">\${c.open ? '🔓 ABIERTO' : '🔒 CERRADO'}</div>\`;
    grid.appendChild(div);
  });

  // alarma
  const bar = document.getElementById('alarm-bar');
  bar.classList.toggle('active', data.alarm_active && !data.alarm_acknowledged);

  // estado de conexión
  const pub = data.publisher_online;
  document.getElementById('status-row').innerHTML =
    \`<span class="dot \${pub?'on':'off'}"></span>Publisher \${pub?'online':'offline'}\${data.publisher_ip?' ('+data.publisher_ip+')':''} &nbsp;|&nbsp; \` +
    \`RSSI \${data.last_heartbeat_rssi} dBm &nbsp;|&nbsp; Abiertos: \${data.open_count}/7\`;

  // botón monitoreo
  monitoringEnabled = data.monitoring_enabled;
  document.getElementById('btn-monitoring').textContent =
    monitoringEnabled ? 'Deshabilitar monitoreo' : 'Habilitar monitoreo';

  // eventos
  const list = document.getElementById('events-list');
  list.innerHTML = data.events.slice(0,20).map(e =>
    \`<div class="event-row"><span>\${fmt(e.ts)}</span> \${JSON.stringify(e)}</div>\`
  ).join('');
}

// Server-Sent Events para actualizaciones en tiempo real
const evtSource = new EventSource('/events');
evtSource.onmessage = e => render(JSON.parse(e.data));
evtSource.onerror   = () => document.getElementById('status-row').textContent = 'Reconectando SSE…';

function requestSync(){
  fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({command:'request-sync'})});
}
function toggleMonitoring(){
  fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({command:'set-monitoring', value: !monitoringEnabled})});
}
function ackAlarm(){
  fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({command:'ack-alarm'})});
}
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    """Handler HTTP minimalista: sirve el dashboard y la API REST simple."""

    mqtt_client: mqtt.Client  # inyectado antes de arrancar el servidor

    def log_message(self, fmt_str: str, *args: Any) -> None:  # silenciar logs verbosos
        if self.path not in ("/events", "/api/state"):
            log.debug("HTTP %s %s", self.command, self.path)

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/state":
            self._serve_state()
        elif self.path == "/events":
            self._serve_sse()
        else:
            self.send_error(404)

    def _serve_html(self) -> None:
        body = DASHBOARD_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_state(self) -> None:
        with state_lock:
            data = state_to_dict(state)
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Enviar estado inicial inmediatamente
        with state_lock:
            initial = json.dumps(state_to_dict(state))
        self._write_sse(initial)

        # Luego escuchar la cola
        while True:
            try:
                data = sse_queue.get(timeout=25)
                self._write_sse(data)
            except queue.Empty:
                # keepalive comment
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except BrokenPipeError:
                    break
            except BrokenPipeError:
                break

    def _write_sse(self, data: str) -> None:
        try:
            self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.flush()
        except BrokenPipeError:
            pass

    # ── POST /api/command ────────────────────────────────────────────────────

    def do_POST(self) -> None:
        if self.path != "/api/command":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body: dict[str, Any] = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self.send_error(400, "JSON inválido")
            return

        command = body.get("command", "")
        root = MQTT_TOPIC_ROOT

        if command == "request-sync":
            self.mqtt_client.publish(f"{root}/command/request-sync", "true", qos=1)

        elif command == "set-monitoring":
            value = bool(body.get("value", True))
            self.mqtt_client.publish(
                f"{root}/command/set-monitoring",
                "true" if value else "false",
                qos=1,
            )
            with state_lock:
                set_monitoring(state, value)
            _notify_sse()

        elif command == "ack-alarm":
            with state_lock:
                acknowledge_alarm(state)
            _notify_sse()

        else:
            self.send_error(400, f"Comando desconocido: {command}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


# ─────────────────────────────────────────────────────────────────────────────
#  Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Iniciando subscriber MQTT → %s:%d", MQTT_HOST, MQTT_PORT)
    mqtt_client = start_mqtt()

    # Inyectar cliente MQTT en el handler
    DashboardHandler.mqtt_client = mqtt_client

    server = HTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
    log.info("Dashboard disponible en http://0.0.0.0:%d", DASHBOARD_PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Deteniendo…")
    finally:
        payload = json.dumps({"device_id": SUBSCRIBER_ID, "status": "offline"})
        mqtt_client.publish(
            f"{MQTT_TOPIC_ROOT}/subscriber/status", payload, qos=1, retain=True
        )
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()
