# Monitor de Compartimentos IoT — Reed Switch + ESP32 + MQTT

Proyecto final **Internet de las Cosas / Embedded Systems**  
**ESCOM — Instituto Politécnico Nacional** | Grupo 6CM3

Sistema IoT para monitorear la apertura y cierre de siete compartimentos físicos mediante sensores magnéticos reed switch. La arquitectura sigue un modelo publicación/suscripción MQTT con tres roles bien separados: un nodo publicador en ESP32, un broker Eclipse Mosquitto y un dashboard web Python que actúa como subscriber y actuador digital.

## Integrantes

| Nombre |
| Daniel Soria González |
| Gustavo Valverde Rojas |

**Grupo:** 6CM3  
**Fecha de entrega:** 20 de junio de 2026

---

## Arquitectura general

```
┌─────────────────────────────────────────────────────────────────┐
│  Capa de hardware          Capa MQTT           Capa de aplicación│
│                                                                  │
│  Reed switch 1 ──┐                                               │
│  Reed switch 2 ──┤                        ┌─────────────────┐   │
│  Reed switch 3 ──┤   publish QoS 1        │ Dashboard Python│   │
│  Reed switch 4 ──┼─► ESP32 ──────────────►│ subscriber +    │──►│ Navegador
│  Reed switch 5 ──┤   C++ / Arduino        │ actuador digital│   │ operador
│  Reed switch 6 ──┤                   ▲    └─────────────────┘   │
│  Reed switch 7 ──┘                   │    command/request-sync   │
│                           Mosquitto  │    command/set-monitoring  │
│  LED indicador ◄── ESP32  Broker     └────────────────────────── │
└─────────────────────────────────────────────────────────────────┘
```

---

## Árbol de tópicos MQTT

Raíz: `escom/iot/reed-monitor`

```
escom/iot/reed-monitor/
├── publisher/status          ← disponibilidad del ESP32 (retenido)
├── sensor/
│   ├── compartment/1..7      ← estado individual por compartimento (retenido)
│   ├── summary               ← snapshot global de los 7 compartimentos (retenido)
│   └── heartbeat             ← RSSI, uptime, conteo de abiertos (cada 5 s)
├── command/
│   ├── request-sync          ← solicita republicar todos los estados
│   └── set-monitoring        ← habilita/deshabilita la alarma remotamente
├── actuator/alarm            ← estado de la alarma digital (retenido)
└── subscriber/status         ← disponibilidad del dashboard (retenido)
```

---

## Estructura del repositorio

```
.
├── firmware/
│   └── esp32_reed_monitor/
│       ├── src/
│       │   └── main.cpp          # Firmware principal del ESP32
│       ├── include/
│       │   ├── project_config.h  # Pines, intervalos, topic root
│       │   └── secrets.h         # WiFi y broker (NO versionar, ver .gitignore)
│       ├── secrets_example.h     # Plantilla de credenciales
│       └── platformio.ini        # Configuración PlatformIO
├── subscriber/
│   ├── core.py                   # Lógica de negocio pura (testeable)
│   ├── app.py                    # MQTT + servidor HTTP / dashboard SSE
│   ├── requirements.txt
│   └── Dockerfile
├── mosquitto/
│   └── config/
│       └── mosquitto.conf        # Configuración del broker
├── tests/
│   └── test_core.py              # 43 pruebas unitarias del subscriber
├── docker-compose.yml            # Levanta broker + dashboard en un comando
└── README.md
```

---

## Requisitos

### Hardware
- ESP32 (cualquier variante con WiFi)
- 7 sensores reed switch
- Resistencias pull-up externas de 10 kΩ para GPIO 34 y 35
- LED indicador conectado a GPIO 2

### Asignación de pines

| Compartimento | GPIO | Observación |
|:---:|:---:|---|
| 1 | 34 | Requiere pull-up externo |
| 2 | 35 | Requiere pull-up externo |
| 3 | 32 | Pull-up interno (INPUT_PULLUP) |
| 4 | 33 | Pull-up interno |
| 5 | 25 | Pull-up interno |
| 6 | 26 | Pull-up interno |
| 7 | 27 | Pull-up interno |

### Software
- [PlatformIO](https://platformio.org/) (para el firmware)
- Docker y Docker Compose (para broker + dashboard)
- Python 3.11+ (opcional, para correr el dashboard sin Docker)

---

## Configuración rápida

### 1. Clonar el repositorio

```bash
git clone <url-del-repo>
cd <nombre-del-repo>
```

### 2. Configurar credenciales del firmware

Copia la plantilla y edita con tus datos reales:

```bash
cp firmware/esp32_reed_monitor/secrets_example.h \
   firmware/esp32_reed_monitor/include/secrets.h
```

Abre `secrets.h` y ajusta:

```c
#define WIFI_SSID     "nombre_de_tu_red"
#define WIFI_PASSWORD "contraseña_wifi"
#define MQTT_HOST     "192.168.1.XX"   // IP del equipo donde corre Mosquitto
#define MQTT_PORT     1883
```

> `secrets.h` está en `.gitignore` y nunca debe subirse al repositorio.

### 3. Flashear el firmware

Con PlatformIO:

```bash
cd firmware/esp32_reed_monitor
pio run --target upload
pio device monitor          # monitor serie a 115200 baud
```

### 4. Levantar broker y dashboard con Docker

```bash
docker compose up --build
```

Esto inicia:
- **Mosquitto** en puertos `1883` (MQTT) y `9001` (WebSocket)
- **Dashboard web** en `http://localhost:8080`

Para detener:

```bash
docker compose down
```

### 5. Dashboard sin Docker (entorno local)

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r subscriber/requirements.txt

# Variables de entorno opcionales (valores por defecto entre paréntesis)
export MQTT_HOST=localhost      # (localhost)
export MQTT_PORT=1883           # (1883)
export DASHBOARD_PORT=8080      # (8080)

python -m subscriber.app
```

---

## Pruebas unitarias

Las pruebas cubren la lógica de negocio del subscriber sin requerir red ni broker:

```bash
pip install pytest
pytest tests/test_core.py -v
```

Salida esperada: **43 passed**.

Las pruebas validan, entre otras cosas:
- Estado inicial correcto de los 7 compartimentos
- Activación y desactivación de la alarma
- Inhibición de alarma cuando el monitoreo está deshabilitado
- Reconciliación de estado por mensaje `sensor/summary`
- Reconocimiento de alarma por el operador
- Límites de índice (compartimento 0, 8, 99 son ignorados)

---

## Validación del sistema en laboratorio

### Verificar conectividad básica

```bash
# Confirmar que el broker es alcanzable
ping <ip-del-broker>

# Escuchar todo el tráfico del sistema
mosquitto_sub -h <ip-del-broker> -t "escom/iot/reed-monitor/#" -v
```

### Matriz de pruebas sugerida

| Prueba | Resultado esperado |
|---|---|
| Ping al broker | Respuesta estable desde el equipo de pruebas |
| Conexión MQTT del ESP32 | Mensaje `publisher/status` con `"status":"online"` |
| Apertura de compartimento | Evento en `sensor/compartment/N` y dashboard marca abierto |
| Cierre de compartimento | Evento de cierre, `open_count` disminuye |
| Solicitud de sync | Republicación de todos los estados y `sensor/summary` |
| Deshabilitar monitoreo | Alarma inhibida aunque haya compartimentos abiertos |
| Reconocer alarma | Badge de alarma desaparece en el dashboard |

---

## Selección de QoS

| Mensaje | QoS | Justificación |
|---|:---:|---|
| `publisher/status`, `subscriber/status` | 1 + retenido | La disponibilidad debe conocerse aunque el cliente se conecte después |
| `sensor/compartment/N` | 1 + retenido | Un cambio físico no debe perderse |
| `sensor/summary` | 1 + retenido | Permite reconstruir estado completo al reconectar |
| `command/request-sync`, `command/set-monitoring` | 1 | El comando debe llegar al nodo al menos una vez |
| `sensor/heartbeat` | 1 | Diagnóstico periódico; su valor envejece rápido |
| `actuator/alarm` | 1 + retenido | Estado de alarma debe persistir para nuevos suscriptores |

---

## Consideraciones de seguridad

La configuración actual usa `allow_anonymous true` en Mosquitto, apropiada para el entorno de laboratorio. Para un despliegue real se recomienda:

- Crear usuarios y contraseñas MQTT (`mosquitto_passwd`)
- Separar permisos de publicación y suscripción por tópico (`acl_file`)
- Habilitar TLS en el puerto 8883
- Cambiar `allow_anonymous false`

---
