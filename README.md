# Proyecto Integrador MQTT para 7 Compartimentos

Sistema IoT completo para monitorear la apertura de siete compartimentos con `reed switch`, usando un `ESP32` como publisher, `Eclipse Mosquitto` como broker MQTT y un dashboard web como subscriber/actuador digital.

## Arquitectura

```mermaid
flowchart LR
  A[ESP32 Publisher<br/>7 reed switch] -->|MQTT| B[Mosquitto Broker]
  B --> C[Dashboard Subscriber]
  C -->|Comandos MQTT| B
  B -->|request-sync / monitoring| A
```

## Árbol de tópicos MQTT

```text
escom/iot/equipo7/reed-monitor/
├── publisher/status
├── sensor/summary
├── sensor/heartbeat
├── sensor/compartment/1
├── sensor/compartment/2
├── sensor/compartment/3
├── sensor/compartment/4
├── sensor/compartment/5
├── sensor/compartment/6
├── sensor/compartment/7
├── command/request-sync
├── command/set-monitoring
├── actuator/alarm
└── subscriber/status
```

## Estructura

- `firmware/esp32_reed_monitor`: firmware Arduino para el `ESP32`.
- `mosquitto/config/mosquitto.conf`: configuración del broker.
- `subscriber`: suscriptor HTTP + dashboard en Python.
- `tools/simulated_publisher.py`: simulador de aperturas para pruebas sin hardware.
- `docs/reporte-tecnico.md`: base del reporte para exportar a PDF.
- `tests/test_core.py`: pruebas unitarias de la lógica de alarmas.

## Hardware sugerido

- `ESP32`
- `7` sensores `reed switch`
- `7` resistencias no son necesarias si usas `INPUT_PULLUP`
- magneto por compartimento
- LED integrado del ESP32 como indicador local

### Cableado recomendado

Cada `reed switch` se conecta entre el pin GPIO y `GND`. El firmware usa `INPUT_PULLUP`, por lo que:

- compartimento cerrado con imán cercano: circuito cerrado, lectura `LOW`
- compartimento abierto: circuito abierto, lectura `HIGH`

GPIO usados por defecto:

| Compartimento | GPIO |
|---|---:|
| 1 | 34 |
| 2 | 35 |
| 3 | 32 |
| 4 | 33 |
| 5 | 25 |
| 6 | 26 |
| 7 | 27 |

Importante:

- `GPIO34` y `GPIO35` en ESP32 no tienen `pull-up` interno.
- Para los compartimentos `1` y `2` necesitas resistor externo de `10kΩ` a `3.3V`.
- Los compartimentos `3` a `7` sí pueden usar `INPUT_PULLUP` interno como está en el firmware.

## Firmware ESP32

Archivo principal: [esp32_reed_monitor.ino](/home/soriadg/Documentos/ProyectoFinalEmbebed/firmware/esp32_reed_monitor/esp32_reed_monitor.ino)

Dependencias de Arduino:

- `PubSubClient`
- Librería `WiFi` incluida con el core de ESP32

Configuración actual:

- WiFi SSID: `TU_RED_WIFI`
- WiFi Password: `TU_PASSWORD_WIFI`
- Broker MQTT por defecto: `192.168.1.50`

Antes de flashear:

1. Edita [secrets.h](/home/soriadg/Documentos/ProyectoFinalEmbebed/firmware/esp32_reed_monitor/include/secrets.h) y reemplaza `MQTT_HOST` por la IP real del broker.
2. Ajusta `REED_PINS` si tu cableado final cambia.
3. Instala `PubSubClient` desde el Library Manager del Arduino IDE.
4. Coloca `pull-up` externo en `GPIO34` y `GPIO35`.

### Comportamiento

- Publica cambios individuales por compartimento.
- Publica un resumen con el estado de los 7 compartimentos.
- Publica heartbeat con RSSI y uptime.
- Se suscribe a comandos para re-sincronización y activación/desactivación del monitoreo.

## Broker Mosquitto con Docker

Archivo principal: [docker-compose.yml](/home/soriadg/Documentos/ProyectoFinalEmbebed/docker-compose.yml)

Levantar broker y dashboard:

```bash
docker compose up --build
```

Puertos:

- `1883`: MQTT
- `9001`: MQTT WebSocket
- `8080`: dashboard web

## Dashboard Subscriber

Arranque local sin Docker:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r subscriber/requirements.txt
python -m subscriber.app
```

Variables útiles:

- `MQTT_HOST`
- `MQTT_PORT`
- `MQTT_TOPIC_ROOT`
- `HTTP_PORT`

Funciones del dashboard:

- visualización en tiempo real de los 7 compartimentos
- alarma digital cuando exista al menos un compartimento abierto
- reconocimiento manual de alarma
- petición de sincronización al publisher
- activación/desactivación remota del monitoreo

## Simulación sin hardware

Con broker y dashboard levantados:

```bash
python tools/simulated_publisher.py --host 127.0.0.1
```

## Validación y pruebas

Pruebas unitarias:

```bash
python3 -m unittest discover -s tests -v
```

Verificación de sintaxis Python:

```bash
python3 -m py_compile subscriber/*.py tools/*.py tests/*.py
```

Pruebas de conectividad sugeridas en laboratorio:

```bash
ping -c 4 <ip-del-broker>
mosquitto_sub -h <ip-del-broker> -t 'escom/iot/equipo7/reed-monitor/#' -v
```

## Despliegue en Raspberry Pi o BeagleBone

Si el broker corre en una SBC del laboratorio:

1. Conecta la placa a la misma red del broker MQTT.
2. Instala `Docker` o `Mosquitto`.
3. Levanta el broker con este repositorio.
4. Obtén la IP del broker con `hostname -I`.
5. Configura esa IP en `firmware/esp32_reed_monitor/include/secrets.h`.

La contraseña que compartiste para la SBC puede usarse sólo en laboratorio; no la publiques si este repositorio se sube a GitHub.

## Entregables cubiertos

- firmware del embebido
- broker MQTT configurable
- suscriptor con interfaz digital
- `docker-compose.yml` para puntos extra
- `README.md` replicable
- base de reporte técnico para convertir a PDF
