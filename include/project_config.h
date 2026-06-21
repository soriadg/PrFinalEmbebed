#pragma once

#include <Arduino.h>

// Red WiFi del laboratorio
static const char* WIFI_SSID = "TU_RED_WIFI";
static const char* WIFI_PASSWORD = "TU_PASSWORD_WIFI";

// IP actual de la BeagleBone Black en la red local
static const char* MQTT_HOST = "192.168.1.85";
static const uint16_t MQTT_PORT = 1883;
static const char* MQTT_USERNAME = "";
static const char* MQTT_PASSWORD = "";

static const char* MQTT_TOPIC_ROOT = "escom/iot/reed-monitor";
static const char* DEVICE_ID = "reed-monitor-esp32";

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
