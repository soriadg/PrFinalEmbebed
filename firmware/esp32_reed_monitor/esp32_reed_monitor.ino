#include <WiFi.h>
#define MQTT_VERSION MQTT_VERSION_3_1
#include <PubSubClient.h>
#include <cstring>

// Configuracion principal.
// Si quieres, aqui mismo puedes cambiar SSID, password e IP del broker.
#define WIFI_SSID "TU_RED_WIFI"
#define WIFI_PASSWORD "TU_PASSWORD_WIFI"
#define MQTT_HOST "192.168.1.85"
#define MQTT_PORT 1883
#define MQTT_USERNAME ""
#define MQTT_PASSWORD ""
#define MQTT_TOPIC_ROOT "escom/iot/reed-monitor"
#define DEVICE_ID "reed-monitor-esp32"

constexpr size_t COMPARTMENT_COUNT = 7;
constexpr uint8_t REED_PINS[COMPARTMENT_COUNT] = {34, 35, 32, 33, 25, 26, 27};
constexpr bool REQUIRES_EXTERNAL_PULLUP[COMPARTMENT_COUNT] = {true, true, false, false, false, false, false};
constexpr uint8_t STATUS_LED_PIN = 2;
constexpr bool OPEN_WHEN_HIGH = true;
constexpr uint16_t MQTT_BUFFER_SIZE = 1024;
constexpr unsigned long DEBOUNCE_MS = 60;
constexpr unsigned long HEARTBEAT_INTERVAL_MS = 5000;
constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 10000;
constexpr unsigned long MQTT_RETRY_INTERVAL_MS = 5000;

struct ReedState {
  bool stableOpen;
  bool lastReadOpen;
  unsigned long lastBounceAt;
};

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
ReedState reedStates[COMPARTMENT_COUNT];

unsigned long lastHeartbeatAt = 0;
unsigned long lastWifiAttemptAt = 0;
unsigned long lastMqttAttemptAt = 0;
unsigned long sequenceNumber = 0;
bool monitoringEnabled = true;

void setupPins();
void setupWifi();
void ensureWifi();
void ensureMqtt();
void publishAvailability(const char* status);
void publishCompartmentState(size_t index, bool retained);
void publishSummary(bool retained);
void publishHeartbeat();
void publishAllStates();
void updateStatusLed();
void handleCommands(char* topic, byte* payload, unsigned int length);
bool readOpenFromPin(uint8_t pin);
bool anyCompartmentOpen();
size_t countOpenCompartments();
void buildClientId(char* buffer, size_t bufferSize);
void logCompartmentState(size_t index);
void logStartupPins();
bool testBrokerTcpConnection();

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("Iniciando monitor MQTT de 7 compartimentos...");
  setupPins();
  setupWifi();

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(handleCommands);
  mqttClient.setBufferSize(MQTT_BUFFER_SIZE);
  mqttClient.setKeepAlive(30);
  logStartupPins();
}

void loop() {
  ensureWifi();
  ensureMqtt();

  if (mqttClient.connected()) {
    mqttClient.loop();
  }

  unsigned long now = millis();
  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    bool currentOpen = readOpenFromPin(REED_PINS[index]);
    if (currentOpen != reedStates[index].lastReadOpen) {
      reedStates[index].lastReadOpen = currentOpen;
      reedStates[index].lastBounceAt = now;
    }

    if ((now - reedStates[index].lastBounceAt) >= DEBOUNCE_MS &&
        currentOpen != reedStates[index].stableOpen) {
      reedStates[index].stableOpen = currentOpen;
      sequenceNumber++;
      publishCompartmentState(index, false);
      publishSummary(false);
      updateStatusLed();
    }
  }

  if (mqttClient.connected() && (now - lastHeartbeatAt) >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatAt = now;
    publishHeartbeat();
  }

  updateStatusLed();
}

void setupPins() {
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);

  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    if (REQUIRES_EXTERNAL_PULLUP[index]) {
      pinMode(REED_PINS[index], INPUT);
    } else {
      pinMode(REED_PINS[index], INPUT_PULLUP);
    }
    bool currentOpen = readOpenFromPin(REED_PINS[index]);
    reedStates[index].stableOpen = currentOpen;
    reedStates[index].lastReadOpen = currentOpen;
    reedStates[index].lastBounceAt = 0;
  }
}

void setupWifi() {
  WiFi.mode(WIFI_STA);
  Serial.printf("Conectando a WiFi SSID: %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  unsigned long now = millis();
  if ((now - lastWifiAttemptAt) < WIFI_RETRY_INTERVAL_MS) {
    return;
  }

  lastWifiAttemptAt = now;
  Serial.println("Reconectando WiFi...");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void ensureMqtt() {
  if (WiFi.status() != WL_CONNECTED || mqttClient.connected()) {
    return;
  }

  unsigned long now = millis();
  if ((now - lastMqttAttemptAt) < MQTT_RETRY_INTERVAL_MS) {
    return;
  }

  lastMqttAttemptAt = now;

  bool tcpReachable = testBrokerTcpConnection();
  if (!tcpReachable) {
    Serial.printf("TCP al broker %s:%u fallo antes de MQTT.\n", MQTT_HOST, MQTT_PORT);
    return;
  }

  char clientId[32];
  buildClientId(clientId, sizeof(clientId));

  bool connected;
  if (strlen(MQTT_USERNAME) > 0) {
    connected = mqttClient.connect(clientId, MQTT_USERNAME, MQTT_PASSWORD);
  } else {
    connected = mqttClient.connect(clientId);
  }

  if (!connected) {
    Serial.printf("Fallo MQTT, estado=%d\n", mqttClient.state());
    return;
  }

  Serial.printf("MQTT conectado a %s:%d\n", MQTT_HOST, MQTT_PORT);
  mqttClient.subscribe(MQTT_TOPIC_ROOT "/command/request-sync", 1);
  mqttClient.subscribe(MQTT_TOPIC_ROOT "/command/set-monitoring", 1);
  publishAvailability("online");
  publishAllStates();
  publishSummary(true);
  publishHeartbeat();
}

void publishAvailability(const char* status) {
  char payload[192];
  snprintf(
    payload,
    sizeof(payload),
    "{\"device_id\":\"%s\",\"status\":\"%s\",\"ip\":\"%s\"}",
    DEVICE_ID,
    status,
    WiFi.localIP().toString().c_str()
  );
  bool ok = mqttClient.publish(MQTT_TOPIC_ROOT "/publisher/status", payload, true);
  Serial.printf("publisher/status => %s\n", ok ? "OK" : "ERROR");
}

void publishCompartmentState(size_t index, bool retained) {
  if (!mqttClient.connected()) {
    return;
  }

  char topic[128];
  snprintf(topic, sizeof(topic), MQTT_TOPIC_ROOT "/sensor/compartment/%u", static_cast<unsigned>(index + 1));

  char payload[256];
  snprintf(
    payload,
    sizeof(payload),
    "{\"device_id\":\"%s\",\"sequence\":%lu,\"timestamp\":%lu,\"compartment_id\":%u,\"open\":%s}",
    DEVICE_ID,
    sequenceNumber,
    millis(),
    static_cast<unsigned>(index + 1),
    reedStates[index].stableOpen ? "true" : "false"
  );

  bool ok = mqttClient.publish(topic, payload, retained);
  Serial.printf("%s => %s\n", topic, ok ? "OK" : "ERROR");
  logCompartmentState(index);
}

void publishSummary(bool retained) {
  if (!mqttClient.connected()) {
    return;
  }

  char compartments[512];
  compartments[0] = '\0';

  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    char fragment[64];
    snprintf(
      fragment,
      sizeof(fragment),
      "%s{\"compartment_id\":%u,\"open\":%s}",
      index == 0 ? "" : ",",
      static_cast<unsigned>(index + 1),
      reedStates[index].stableOpen ? "true" : "false"
    );
    strncat(compartments, fragment, sizeof(compartments) - strlen(compartments) - 1);
  }

  char payload[768];
  snprintf(
    payload,
    sizeof(payload),
    "{\"device_id\":\"%s\",\"sequence\":%lu,\"timestamp\":%lu,\"open_count\":%u,\"monitoring_enabled\":%s,\"compartments\":[%s]}",
    DEVICE_ID,
    sequenceNumber,
    millis(),
    static_cast<unsigned>(countOpenCompartments()),
    monitoringEnabled ? "true" : "false",
    compartments
  );

  bool ok = mqttClient.publish(MQTT_TOPIC_ROOT "/sensor/summary", payload, retained);
  Serial.printf("sensor/summary (%u bytes) => %s\n", static_cast<unsigned>(strlen(payload)), ok ? "OK" : "ERROR");
}

void publishHeartbeat() {
  if (!mqttClient.connected()) {
    return;
  }

  char payload[224];
  snprintf(
    payload,
    sizeof(payload),
    "{\"device_id\":\"%s\",\"timestamp\":%lu,\"rssi\":%d,\"uptime_ms\":%lu,\"open_count\":%u}",
    DEVICE_ID,
    millis(),
    WiFi.RSSI(),
    millis(),
    static_cast<unsigned>(countOpenCompartments())
  );
  bool ok = mqttClient.publish(MQTT_TOPIC_ROOT "/sensor/heartbeat", payload, false);
  Serial.printf("sensor/heartbeat => %s\n", ok ? "OK" : "ERROR");
}

void publishAllStates() {
  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    publishCompartmentState(index, true);
  }
}

void updateStatusLed() {
  bool ledOn = monitoringEnabled && anyCompartmentOpen();
  digitalWrite(STATUS_LED_PIN, ledOn ? HIGH : LOW);
}

void handleCommands(char* topic, byte* payload, unsigned int length) {
  char message[128];
  unsigned int copyLength = length < sizeof(message) - 1 ? length : sizeof(message) - 1;
  memcpy(message, payload, copyLength);
  message[copyLength] = '\0';

  if (strcmp(topic, MQTT_TOPIC_ROOT "/command/request-sync") == 0) {
    Serial.println("Comando recibido: request-sync");
    publishAllStates();
    publishSummary(true);
    publishHeartbeat();
    return;
  }

  if (strcmp(topic, MQTT_TOPIC_ROOT "/command/set-monitoring") == 0) {
    monitoringEnabled = strstr(message, "true") != nullptr;
    Serial.printf("Comando recibido: monitoring=%s\n", monitoringEnabled ? "true" : "false");
    publishSummary(true);
    updateStatusLed();
  }
}

bool readOpenFromPin(uint8_t pin) {
  bool levelHigh = digitalRead(pin) == HIGH;
  return OPEN_WHEN_HIGH ? levelHigh : !levelHigh;
}

bool anyCompartmentOpen() {
  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    if (reedStates[index].stableOpen) {
      return true;
    }
  }
  return false;
}

size_t countOpenCompartments() {
  size_t count = 0;
  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    if (reedStates[index].stableOpen) {
      count++;
    }
  }
  return count;
}

void buildClientId(char* buffer, size_t bufferSize) {
  uint64_t chipId = ESP.getEfuseMac();
  snprintf(buffer, bufferSize, "esp32-%04X", static_cast<unsigned>(chipId & 0xFFFF));
}

void logCompartmentState(size_t index) {
  Serial.printf(
    "Compartimento %u GPIO %u => %s\n",
    static_cast<unsigned>(index + 1),
    static_cast<unsigned>(REED_PINS[index]),
    reedStates[index].stableOpen ? "ABIERTO" : "CERRADO"
  );
}

void logStartupPins() {
  Serial.println("Mapa de pines configurado:");
  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    Serial.printf(
      "  Compartimento %u -> GPIO %u%s\n",
      static_cast<unsigned>(index + 1),
      static_cast<unsigned>(REED_PINS[index]),
      REQUIRES_EXTERNAL_PULLUP[index] ? " (requiere pull-up externo)" : ""
    );
  }
}

bool testBrokerTcpConnection() {
  WiFiClient probeClient;
  Serial.printf("Probando TCP a %s:%u...\n", MQTT_HOST, MQTT_PORT);
  bool connected = probeClient.connect(MQTT_HOST, MQTT_PORT);
  if (connected) {
    Serial.println("TCP conectado correctamente al broker.");
    probeClient.stop();
    return true;
  }

  Serial.println("TCP no pudo abrir socket con el broker.");
  probeClient.stop();
  return false;
}
