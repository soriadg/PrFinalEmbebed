from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from subscriber.core import DashboardState, utc_now_iso


STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class Settings:
    mqtt_host: str = os.getenv("MQTT_HOST", "127.0.0.1")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_username: str = os.getenv("MQTT_USERNAME", "")
    mqtt_password: str = os.getenv("MQTT_PASSWORD", "")
    mqtt_topic_root: str = os.getenv("MQTT_TOPIC_ROOT", "escom/iot/equipo7/reed-monitor")
    http_host: str = os.getenv("HTTP_HOST", "0.0.0.0")
    http_port: int = int(os.getenv("HTTP_PORT", "8080"))
    publisher_status_topic: str = ""
    subscriber_status_topic: str = ""
    sensor_summary_topic: str = ""
    sensor_compartment_prefix: str = ""
    command_sync_topic: str = ""
    command_monitoring_topic: str = ""
    actuator_alarm_topic: str = ""

    def __post_init__(self) -> None:
        root = self.mqtt_topic_root.rstrip("/")
        self.publisher_status_topic = f"{root}/publisher/status"
        self.subscriber_status_topic = f"{root}/subscriber/status"
        self.sensor_summary_topic = f"{root}/sensor/summary"
        self.sensor_compartment_prefix = f"{root}/sensor/compartment/"
        self.command_sync_topic = f"{root}/command/request-sync"
        self.command_monitoring_topic = f"{root}/command/set-monitoring"
        self.actuator_alarm_topic = f"{root}/actuator/alarm"


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: set[queue.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        listener: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=32)
        with self._lock:
            self._listeners.add(listener)
        return listener

    def unsubscribe(self, listener: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._listeners.discard(listener)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener.put_nowait(event)
            except queue.Full:
                try:
                    listener.get_nowait()
                except queue.Empty:
                    pass
                listener.put_nowait(event)


class MqttBridge:
    def __init__(self, settings: Settings, state: DashboardState, bus: EventBus) -> None:
        self.settings = settings
        self.state = state
        self.bus = bus
        self.client = None
        self._lock = threading.Lock()

    def start(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("paho-mqtt no esta instalado; el dashboard iniciara sin puente MQTT.", file=sys.stderr)
            return

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="reed-dashboard",
            protocol=mqtt.MQTTv31,
        )
        if self.settings.mqtt_username:
            client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)

        lwt_payload = json.dumps(
            {
                "device_id": "reed-dashboard",
                "status": "offline",
                "timestamp": utc_now_iso(),
            }
        )
        client.will_set(self.settings.subscriber_status_topic, lwt_payload, qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
        client.loop_start()
        self.client = client

    def stop(self) -> None:
        if self.client is None:
            return
        try:
            self.publish(
                self.settings.subscriber_status_topic,
                {
                    "device_id": "reed-dashboard",
                    "status": "offline",
                    "timestamp": utc_now_iso(),
                },
                retain=True,
            )
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def publish(self, topic: str, payload: dict[str, Any], retain: bool = False) -> None:
        if self.client is None:
            return
        self.client.publish(topic, json.dumps(payload), qos=1, retain=retain)

    def publish_actuator_state(self) -> None:
        self.publish(self.settings.actuator_alarm_topic, self.state.actuator_payload(), retain=True)

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code != 0:
            print(f"No se pudo conectar al broker MQTT: codigo {reason_code}", file=sys.stderr)
            return

        client.subscribe(self.settings.publisher_status_topic, qos=1)
        client.subscribe(self.settings.sensor_summary_topic, qos=1)
        client.subscribe(f"{self.settings.sensor_compartment_prefix}+", qos=1)
        self.publish(
            self.settings.subscriber_status_topic,
            {
                "device_id": "reed-dashboard",
                "status": "online",
                "timestamp": utc_now_iso(),
            },
            retain=True,
        )

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code != 0:
            print(f"MQTT desconectado inesperadamente: {reason_code}", file=sys.stderr)

    def _on_message(self, _client, _userdata, message) -> None:
        payload = self._decode_payload(message.payload)
        topic = message.topic
        events: list[dict[str, Any]] = []

        if topic == self.settings.publisher_status_topic:
            online = payload.get("status") == "online"
            events = self.state.set_publisher_online(online, payload.get("timestamp"))
        elif topic == self.settings.sensor_summary_topic:
            compartments = payload.get("compartments", [])
            normalized = [
                {
                    "compartment_id": int(item["compartment_id"]),
                    "open": bool(item["open"]),
                }
                for item in compartments
            ]
            events = self.state.update_from_snapshot(
                normalized,
                sequence=payload.get("sequence"),
                source_timestamp=payload.get("timestamp"),
            )
        elif topic.startswith(self.settings.sensor_compartment_prefix):
            compartment_id = int(topic.split("/")[-1])
            events = self.state.update_compartment(
                compartment_id=compartment_id,
                is_open=bool(payload.get("open")),
                source_timestamp=payload.get("timestamp"),
                sequence=payload.get("sequence"),
            )

        if events:
            self.publish_actuator_state()
            self.bus.publish(
                {
                    "type": "state",
                    "state": self.state.snapshot(),
                }
            )

    @staticmethod
    def _decode_payload(raw_payload: bytes) -> dict[str, Any]:
        try:
            return json.loads(raw_payload.decode("utf-8"))
        except json.JSONDecodeError:
            return {"raw": raw_payload.decode("utf-8", errors="replace")}


class Application:
    def __init__(self) -> None:
        self.settings = Settings()
        self.state = DashboardState()
        self.bus = EventBus()
        self.mqtt = MqttBridge(self.settings, self.state, self.bus)
        self.httpd: ThreadingHTTPServer | None = None

    def run(self) -> None:
        self.mqtt.start()
        handler = self._build_handler()
        server = ThreadingHTTPServer((self.settings.http_host, self.settings.http_port), handler)
        self.httpd = server
        print(f"Dashboard disponible en http://{self.settings.http_host}:{self.settings.http_port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
        self.mqtt.stop()

    def _build_handler(self):
        app = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                route = urlparse(self.path).path
                if route == "/":
                    self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                    return
                if route == "/app.js":
                    self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
                    return
                if route == "/styles.css":
                    self._serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
                    return
                if route == "/api/state":
                    self._write_json(app.state.snapshot())
                    return
                if route == "/api/events":
                    self._stream_events()
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                route = urlparse(self.path).path
                body = self._read_json_body()

                if route == "/api/acknowledge":
                    actor = str(body.get("actor", "operador"))
                    events = app.state.acknowledge_alarm(actor)
                    app.mqtt.publish_actuator_state()
                    app.bus.publish({"type": "state", "state": app.state.snapshot()})
                    self._write_json({"ok": True, "events": events})
                    return

                if route == "/api/request-sync":
                    payload = {
                        "device_id": "reed-dashboard",
                        "timestamp": utc_now_iso(),
                        "request": "sync",
                    }
                    app.mqtt.publish(app.settings.command_sync_topic, payload)
                    self._write_json({"ok": True, "published": payload})
                    return

                if route == "/api/monitoring":
                    enabled = bool(body.get("enabled", True))
                    events = app.state.set_monitoring_enabled(enabled)
                    payload = {
                        "device_id": "reed-dashboard",
                        "timestamp": utc_now_iso(),
                        "enabled": enabled,
                    }
                    app.mqtt.publish(app.settings.command_monitoring_topic, payload, retain=True)
                    app.mqtt.publish_actuator_state()
                    app.bus.publish({"type": "state", "state": app.state.snapshot()})
                    self._write_json({"ok": True, "events": events, "published": payload})
                    return

                self.send_error(HTTPStatus.NOT_FOUND)

            def _read_json_body(self) -> dict[str, Any]:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length == 0:
                    return {}
                raw_body = self.rfile.read(content_length).decode("utf-8")
                if not raw_body.strip():
                    return {}
                return json.loads(raw_body)

            def _stream_events(self) -> None:
                listener = app.bus.subscribe()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                def write_event(data: dict[str, Any]) -> None:
                    payload = json.dumps(data, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()

                try:
                    write_event({"type": "state", "state": app.state.snapshot()})
                    while True:
                        try:
                            event = listener.get(timeout=15)
                            write_event(event)
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                finally:
                    app.bus.unsubscribe(listener)

            def _serve_file(self, path: Path, content_type: str) -> None:
                if not path.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                content = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _write_json(self, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: Any) -> None:
                sys.stdout.write(f"{self.address_string()} - {format % args}\n")

        return RequestHandler


def main() -> None:
    Application().run()


if __name__ == "__main__":
    main()
