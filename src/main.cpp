#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <cstring>

#include "project_config.h"

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
unsigned long lastRawPinLogAt = 0;
unsigned long sequenceNumber = 0;
bool monitoringEnabled = true;
wl_status_t lastReportedWifiStatus = WL_IDLE_STATUS;

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
void logRawPinStates();
const char* wifiStatusToString(wl_status_t status);
void logWifiStatusIfChanged();
void scanAndLogNetworks();
const char* mqttStateToString(int state);
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
  lastReportedWifiStatus = WiFi.status();
}

void loop() {
  ensureWifi();
  ensureMqtt();
  logWifiStatusIfChanged();

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

  if ((now - lastRawPinLogAt) >= HEARTBEAT_INTERVAL_MS) {
    lastRawPinLogAt = now;
    logRawPinStates();
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
  WiFi.setSleep(false);
  Serial.printf("Conectando a WiFi SSID: %s\n", WIFI_SSID);
  scanAndLogNetworks();
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
  Serial.printf(
    "Reconectando WiFi... estado actual=%d (%s)\n",
    static_cast<int>(WiFi.status()),
    wifiStatusToString(WiFi.status())
  );
  scanAndLogNetworks();
  WiFi.disconnect();
  delay(250);
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
    Serial.printf(
      "Fallo MQTT, estado=%d (%s). Broker=%s Puerto=%u\n",
      mqttClient.state(),
      mqttStateToString(mqttClient.state()),
      MQTT_HOST,
      MQTT_PORT
    );
    return;
  }

  Serial.printf("MQTT conectado a %s:%u\n", MQTT_HOST, MQTT_PORT);
  mqttClient.subscribe((String(MQTT_TOPIC_ROOT) + "/command/request-sync").c_str(), 1);
  mqttClient.subscribe((String(MQTT_TOPIC_ROOT) + "/command/set-monitoring").c_str(), 1);
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
  bool ok = mqttClient.publish((String(MQTT_TOPIC_ROOT) + "/publisher/status").c_str(), payload, true);
  Serial.printf("publisher/status => %s\n", ok ? "OK" : "ERROR");
}

void publishCompartmentState(size_t index, bool retained) {
  if (!mqttClient.connected()) {
    return;
  }

  String topic = String(MQTT_TOPIC_ROOT) + "/sensor/compartment/" + String(index + 1);

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

  bool ok = mqttClient.publish(topic.c_str(), payload, retained);
  Serial.printf("%s => %s\n", topic.c_str(), ok ? "OK" : "ERROR");
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

  bool ok = mqttClient.publish((String(MQTT_TOPIC_ROOT) + "/sensor/summary").c_str(), payload, retained);
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
  bool ok = mqttClient.publish((String(MQTT_TOPIC_ROOT) + "/sensor/heartbeat").c_str(), payload, false);
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

  String syncTopic = String(MQTT_TOPIC_ROOT) + "/command/request-sync";
  String monitoringTopic = String(MQTT_TOPIC_ROOT) + "/command/set-monitoring";

  if (strcmp(topic, syncTopic.c_str()) == 0) {
    Serial.println("Comando recibido: request-sync");
    publishAllStates();
    publishSummary(true);
    publishHeartbeat();
    return;
  }

  if (strcmp(topic, monitoringTopic.c_str()) == 0) {
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

  logRawPinStates();
}

void logRawPinStates() {
  Serial.print("Lectura cruda GPIO: ");
  for (size_t index = 0; index < COMPARTMENT_COUNT; ++index) {
    int level = digitalRead(REED_PINS[index]);
    Serial.printf(
      "[C%u GPIO%u=%s]%s",
      static_cast<unsigned>(index + 1),
      static_cast<unsigned>(REED_PINS[index]),
      level == HIGH ? "HIGH" : "LOW",
      index + 1 == COMPARTMENT_COUNT ? "\n" : " "
    );
  }
}

const char* wifiStatusToString(wl_status_t status) {
  switch (status) {
    case WL_IDLE_STATUS:
      return "IDLE";
    case WL_NO_SSID_AVAIL:
      return "NO_SSID";
    case WL_SCAN_COMPLETED:
      return "SCAN_COMPLETED";
    case WL_CONNECTED:
      return "CONNECTED";
    case WL_CONNECT_FAILED:
      return "CONNECT_FAILED";
    case WL_CONNECTION_LOST:
      return "CONNECTION_LOST";
    case WL_DISCONNECTED:
      return "DISCONNECTED";
    default:
      return "UNKNOWN";
  }
}

void logWifiStatusIfChanged() {
  wl_status_t currentStatus = WiFi.status();
  if (currentStatus == lastReportedWifiStatus) {
    return;
  }

  lastReportedWifiStatus = currentStatus;
  Serial.printf(
    "WiFi status=%d (%s)\n",
    static_cast<int>(currentStatus),
    wifiStatusToString(currentStatus)
  );

  if (currentStatus == WL_CONNECTED) {
    Serial.printf(
      "WiFi conectado. IP=%s RSSI=%d dBm\n",
      WiFi.localIP().toString().c_str(),
      WiFi.RSSI()
    );
  }
}

void scanAndLogNetworks() {
  Serial.println("Escaneando redes WiFi visibles...");
  int networkCount = WiFi.scanNetworks(false, true);

  if (networkCount <= 0) {
    Serial.println("No se detectaron redes en el escaneo.");
    return;
  }

  bool foundTarget = false;
  for (int index = 0; index < networkCount; ++index) {
    String ssid = WiFi.SSID(index);
    int32_t rssi = WiFi.RSSI(index);
    int32_t channel = WiFi.channel(index);
    wifi_auth_mode_t encryption = WiFi.encryptionType(index);

    Serial.printf(
      "  [%d] SSID='%s' RSSI=%d dBm Canal=%d Cifrado=%d\n",
      index + 1,
      ssid.c_str(),
      rssi,
      channel,
      static_cast<int>(encryption)
    );

    if (ssid == WIFI_SSID) {
      foundTarget = true;
    }
  }

  if (!foundTarget) {
    Serial.printf("La red objetivo '%s' no aparecio en el escaneo.\n", WIFI_SSID);
  }

  WiFi.scanDelete();
}

const char* mqttStateToString(int state) {
  switch (state) {
    case -4:
      return "MQTT_CONNECTION_TIMEOUT";
    case -3:
      return "MQTT_CONNECTION_LOST";
    case -2:
      return "MQTT_CONNECT_FAILED";
    case -1:
      return "MQTT_DISCONNECTED";
    case 0:
      return "MQTT_CONNECTED";
    case 1:
      return "MQTT_CONNECT_BAD_PROTOCOL";
    case 2:
      return "MQTT_CONNECT_BAD_CLIENT_ID";
    case 3:
      return "MQTT_CONNECT_UNAVAILABLE";
    case 4:
      return "MQTT_CONNECT_BAD_CREDENTIALS";
    case 5:
      return "MQTT_CONNECT_UNAUTHORIZED";
    default:
      return "MQTT_UNKNOWN";
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
