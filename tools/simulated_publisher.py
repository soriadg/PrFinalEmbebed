from __future__ import annotations

import argparse
import json
import random
import time

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:
    raise SystemExit("Instala paho-mqtt para usar el publicador simulado.") from exc


def build_payload(device_id: str, sequence: int, compartment_id: int, is_open: bool) -> dict:
    return {
        "device_id": device_id,
        "sequence": sequence,
        "timestamp": int(time.time() * 1000),
        "compartment_id": compartment_id,
        "open": is_open,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulador MQTT para el monitor de 7 compartimentos.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic-root", default="escom/iot/equipo7/reed-monitor")
    parser.add_argument("--device-id", default="reed-monitor-sim")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="reed-simulator")
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    states = {index: False for index in range(1, 8)}
    sequence = 0

    client.publish(
        f"{args.topic_root}/publisher/status",
        json.dumps(
            {
                "device_id": args.device_id,
                "status": "online",
                "timestamp": int(time.time() * 1000),
            }
        ),
        qos=1,
        retain=True,
    )

    try:
        while True:
            compartment_id = random.randint(1, 7)
            states[compartment_id] = not states[compartment_id]
            sequence += 1

            event_payload = build_payload(args.device_id, sequence, compartment_id, states[compartment_id])
            client.publish(
                f"{args.topic_root}/sensor/compartment/{compartment_id}",
                json.dumps(event_payload),
                qos=1,
                retain=False,
            )

            summary_payload = {
                "device_id": args.device_id,
                "sequence": sequence,
                "timestamp": int(time.time() * 1000),
                "open_count": sum(1 for value in states.values() if value),
                "compartments": [
                    {"compartment_id": index, "open": value}
                    for index, value in states.items()
                ],
            }
            client.publish(
                f"{args.topic_root}/sensor/summary",
                json.dumps(summary_payload),
                qos=1,
                retain=False,
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(
            f"{args.topic_root}/publisher/status",
            json.dumps(
                {
                    "device_id": args.device_id,
                    "status": "offline",
                    "timestamp": int(time.time() * 1000),
                }
            ),
            qos=1,
            retain=True,
        )
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

