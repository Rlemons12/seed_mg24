#include <Arduino.h>
#include <Wire.h>
#include <LSM6DS3.h>
#include <SilabsMicrophoneAnalog.h>
#include "sensor_config.h"
#include "sensor_channel.h"
#include "telemetry_buffer.h"
#include "telemetry_encoder.h"
#include "configuration_store.h"
#if defined(ARDUINO_SILABS_STACK_BLE_SILABS)
#include "sl_bluetooth.h"
#define BLE_SUPPORTED 1
#else
#define BLE_SUPPORTED 0
#endif

#define SERIAL_BAUD 115200
#define BLE_NAME "XIAO-MG24-Sense"
#define IMU_POWER_PIN PD5
#define BATTERY_ENABLE_PIN PD3
#define BATTERY_ADC_PIN PD4
#define MIC_DATA_PIN PC9
#define MIC_PWR_PIN PC8
#define MIC_SAMPLES 128
#define MIC_VALUE_MIN 735
#define MIC_VALUE_MAX 900
#define ENABLE_MIC 1
#define ENABLE_IMU 1
#define ENABLE_ANALOG 1
#define ENABLE_BATTERY 1

LSM6DS3 imu(I2C_MODE, 0x6A);
MicrophoneAnalog mic(MIC_DATA_PIN, MIC_PWR_PIN);

uint32_t mic_buffer[MIC_SAMPLES];
uint32_t mic_buffer_local[MIC_SAMPLES];
volatile bool mic_ready = false;

uint32_t sample_interval_ms = 100;
uint32_t last_sample_ms = 0;
uint32_t last_heartbeat_ms = 0;
uint32_t telemetry_sequence = 0;
uint32_t processing_error_count = 0;
uint32_t sensor_error_count = 0;
uint32_t mic_level = 0;
int led_brightness = 0;
bool imu_ok = false;
bool mic_ok = false;
bool ble_enabled = false;
bool ble_connected = false;
bool ble_notify_enabled = false;
#if BLE_SUPPORTED
uint8_t ble_connection_handle = 0xff;
uint8_t ble_advertising_set_handle = 0xff;
uint16_t ble_telemetry_characteristic_handle = 0;
uint16_t ble_command_characteristic_handle = 0;
uint16_t ble_metadata_characteristic_handle = 0;
uint16_t ble_capabilities_characteristic_handle = 0;
#endif
char telemetry_json[512];
char ble_json[244];
char metadata_json[384];
char capabilities_json[1280];
ChannelConfig microphone_runtime_config = MICROPHONE_CHANNEL_CONFIG;
SensorChannel microphone_channel(&microphone_runtime_config);
TelemetryBuffer offline_buffer;
VolatileConfigurationStore runtime_configuration_store;

const uint8_t analog_pins[] = {
  D0, D1, D2, D3, D4, D5
};

void mic_samples_ready_cb() {
  memcpy(mic_buffer_local, mic_buffer, MIC_SAMPLES * sizeof(uint32_t));
  mic_ready = true;
}

void update_led() {
  int value = led_brightness;
  if (LED_BUILTIN_ACTIVE == LOW) {
    value = 255 - value;
  }
  analogWrite(LED_BUILTIN, value);
}

#if BLE_SUPPORTED
static const uint8_t ble_advertised_name[] = BLE_NAME;
static const uuid_128 telemetry_service_uuid = {
  .data = { 0xef, 0xbe, 0x24, 0x00, 0x24, 0x47, 0x4d, 0x2d, 0x80, 0x24, 0x24, 0x47, 0x4d, 0x00, 0x00, 0x01 }
};
static const uuid_128 telemetry_characteristic_uuid = {
  .data = { 0xef, 0xbe, 0x24, 0x00, 0x24, 0x47, 0x4d, 0x2d, 0x80, 0x24, 0x24, 0x47, 0x4d, 0x00, 0x00, 0x02 }
};
static const uuid_128 command_characteristic_uuid = {
  .data = { 0xef, 0xbe, 0x24, 0x00, 0x24, 0x47, 0x4d, 0x2d, 0x80, 0x24, 0x24, 0x47, 0x4d, 0x00, 0x00, 0x03 }
};
static const uuid_128 metadata_characteristic_uuid = {
  .data = { 0xef, 0xbe, 0x24, 0x00, 0x24, 0x47, 0x4d, 0x2d, 0x80, 0x24, 0x24, 0x47, 0x4d, 0x00, 0x00, 0x04 }
};
static const uuid_128 capabilities_characteristic_uuid = {
  .data = { 0xef, 0xbe, 0x24, 0x00, 0x24, 0x47, 0x4d, 0x2d, 0x80, 0x24, 0x24, 0x47, 0x4d, 0x00, 0x00, 0x05 }
};

void ble_initialize_gatt_db();
void ble_start_advertising();
void ble_update_telemetry(float batt, float ax, float ay, float az, float gx, float gy, float gz, int mic_pct, const char* analog_json);
bool ble_send_payload(const char* payload);
#endif

bool parse_bounded_uint(const String& text, uint32_t minimum, uint32_t maximum, uint32_t* output) {
  if (!output || text.length() == 0) return false;
  for (size_t i = 0; i < text.length(); ++i) if (!isDigit(text[i])) return false;
  unsigned long value = strtoul(text.c_str(), nullptr, 10);
  if (value < minimum || value > maximum) return false;
  *output = (uint32_t)value;
  return true;
}

void command_result(bool accepted, const char* code) {
  char response[120];
  snprintf(response, sizeof(response), "{\"t\":\"%s\",\"v\":1,\"id\":\"%s\",\"s\":%lu,\"ms\":%lu,\"code\":\"%s\"}",
           accepted ? "ca" : "ce", DEVICE_ID, (unsigned long)telemetry_sequence++, (unsigned long)millis(), code);
  Serial.println(response);
#if BLE_SUPPORTED
  if (ble_connected) ble_send_payload(response);
#endif
}

StoredChannelConfiguration current_stored_configuration() {
  StoredChannelConfiguration value = {};
  value.sample_interval_ms = microphone_runtime_config.sample_interval_ms;
  value.processing_interval_ms = microphone_runtime_config.processing_interval_ms;
  value.report_interval_ms = microphone_runtime_config.report_interval_ms;
  value.heartbeat_interval_ms = microphone_runtime_config.heartbeat_interval_ms;
  value.filter_type = static_cast<uint8_t>(microphone_runtime_config.filter_type);
  value.filter_window = microphone_runtime_config.filter_window;
  value.enabled = microphone_runtime_config.enabled ? 1 : 0;
  return value;
}

void report_runtime_configuration() {
  char response[244];
  snprintf(response, sizeof(response),
           "{\"t\":\"ca\",\"v\":1,\"id\":\"%s\",\"s\":%lu,\"ms\":%lu,\"code\":\"readback\","
           "\"sample\":%lu,\"process\":%lu,\"report\":%lu,\"heartbeat\":%lu,\"filter\":%u,\"window\":%u,\"enabled\":%u}",
           DEVICE_ID, (unsigned long)telemetry_sequence++, (unsigned long)millis(),
           (unsigned long)microphone_runtime_config.sample_interval_ms,
           (unsigned long)microphone_runtime_config.processing_interval_ms,
           (unsigned long)microphone_runtime_config.report_interval_ms,
           (unsigned long)microphone_runtime_config.heartbeat_interval_ms,
           (unsigned)microphone_runtime_config.filter_type, microphone_runtime_config.filter_window,
           microphone_runtime_config.enabled ? 1 : 0);
  Serial.println(response);
#if BLE_SUPPORTED
  if (ble_connected) ble_send_payload(response);
#endif
}

bool handle_config_command(const String& command) {
  // Versioned bounded form: CFG 1 microphone_raw FIELD VALUE
  if (!command.startsWith("CFG ")) return false;
  int p1 = command.indexOf(' ', 4); int p2 = p1 < 0 ? -1 : command.indexOf(' ', p1 + 1);
  int p3 = p2 < 0 ? -1 : command.indexOf(' ', p2 + 1);
  if (p1 < 0 || p2 < 0) { command_result(false, "invalid_format"); return true; }
  String version = command.substring(4, p1); String channel = command.substring(p1 + 1, p2);
  String field = p3 < 0 ? command.substring(p2 + 1) : command.substring(p2 + 1, p3);
  String value = p3 < 0 ? "" : command.substring(p3 + 1);
  if (version != "1") { command_result(false, "unsupported_version"); return true; }
  if (channel != "MICROPHONE_RAW") { command_result(false, "unknown_channel"); return true; }
  if (field == "RESTORE" && value.length() == 0) {
    microphone_runtime_config = MICROPHONE_CHANNEL_CONFIG; sample_interval_ms = microphone_runtime_config.report_interval_ms;
    microphone_channel.reconfigure(&microphone_runtime_config); command_result(true, "restored"); return true;
  }
  uint32_t numeric = 0;
  if (field == "SAMPLE" && parse_bounded_uint(value, 10, 5000, &numeric)) microphone_runtime_config.sample_interval_ms = numeric;
  else if (field == "PROCESS" && parse_bounded_uint(value, 10, 5000, &numeric)) microphone_runtime_config.processing_interval_ms = numeric;
  else if (field == "REPORT" && parse_bounded_uint(value, 50, 5000, &numeric)) { microphone_runtime_config.report_interval_ms = numeric; sample_interval_ms = numeric; }
  else if (field == "HEARTBEAT" && parse_bounded_uint(value, 1000, 3600000, &numeric)) microphone_runtime_config.heartbeat_interval_ms = numeric;
  else if (field == "FILTER" && value == "NONE") microphone_runtime_config.filter_type = FilterType::None;
  else if (field == "FILTER" && value == "MOVING_AVERAGE") microphone_runtime_config.filter_type = FilterType::MovingAverage;
  else if (field == "FILTER" && value == "EXPONENTIAL") microphone_runtime_config.filter_type = FilterType::Exponential;
  else if (field == "FILTER" && value == "MEDIAN") microphone_runtime_config.filter_type = FilterType::Median;
  else if (field == "ENABLE" && (value == "0" || value == "1")) microphone_runtime_config.enabled = value == "1";
  else { command_result(false, "invalid_or_unsafe_setting"); return true; }
  microphone_channel.reconfigure(&microphone_runtime_config);
  runtime_configuration_store.write(current_stored_configuration(), millis());
  command_result(true, "applied_volatile");
  return true;
}

void handle_command(String command) {
  command.trim();
  command.toUpperCase();

  if (command == "CFGGET 1 MICROPHONE_RAW") {
    report_runtime_configuration();
    return;
  }
  if (handle_config_command(command)) return;

  if (command == "LED ON") {
    led_brightness = 255;
    update_led();
  } else if (command == "LED OFF") {
    led_brightness = 0;
    update_led();
  } else if (command.startsWith("LED ")) {
    uint32_t value;
    if (parse_bounded_uint(command.substring(4), 0, 255, &value)) {
      led_brightness = (int)value;
      update_led();
    } else command_result(false, "invalid_led_value");
  } else if (command.startsWith("RATE ")) {
    uint32_t value;
    if (parse_bounded_uint(command.substring(5), 50, 5000, &value)) {
      sample_interval_ms = value;
      microphone_runtime_config.report_interval_ms = value;
    } else command_result(false, "invalid_rate");
  } else if (command == "BLE START") {
    ble_enabled = true;
#if BLE_SUPPORTED
    ble_start_advertising();
#endif
  } else if (command == "BLE STOP") {
    ble_enabled = false;
  } else if (command == "PING") {
    Serial.println("{\"type\":\"pong\"}");
  } else {
    command_result(false, "unknown_command");
  }
}

void update_microphone() {
  if (!mic_ok) {
    return;
  }

  if (!mic_ready) {
    return;
  }

  mic_ready = false;
  mic.stopSampling();

  uint32_t average = (uint32_t)mic.getAverage(mic_buffer_local, MIC_SAMPLES);
  average = constrain(average, MIC_VALUE_MIN, MIC_VALUE_MAX);
  mic_level = (average + mic_level) / 2;
  const uint32_t now = millis();
  if (microphone_channel.sample_due(now)) {
    microphone_channel.accept_raw((float)mic_level, true, now);
  }
  if (microphone_channel.processing_due(now)) {
    microphone_channel.process(now);
  }

  if (led_brightness < 0) {
    led_brightness = map(mic_level, MIC_VALUE_MIN, MIC_VALUE_MAX, 0, 255);
    update_led();
  }

  mic.startSampling(mic_samples_ready_cb);
}

float battery_voltage() {
  if (!ENABLE_BATTERY) {
    return 0.0f;
  }

  int raw = analogRead(BATTERY_ADC_PIN);
  return raw * (2.0f * 3.3f / 4095.0f);
}

void print_telemetry() {
  float ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;

  if (imu_ok) {
    ax = imu.readFloatAccelX();
    ay = imu.readFloatAccelY();
    az = imu.readFloatAccelZ();
    gx = imu.readFloatGyroX();
    gy = imu.readFloatGyroY();
    gz = imu.readFloatGyroZ();
  }

  int mic_pct = map(constrain(mic_level, MIC_VALUE_MIN, MIC_VALUE_MAX), MIC_VALUE_MIN, MIC_VALUE_MAX, 0, 100);
  float batt = battery_voltage();
  char analog_json[48] = "";

  if (ENABLE_ANALOG) {
    for (size_t i = 0; i < sizeof(analog_pins); i++) {
      if (i > 0) {
        strlcat(analog_json, ",", sizeof(analog_json));
      }
      char value[8];
      snprintf(value, sizeof(value), "%d", analogRead(analog_pins[i]));
      strlcat(analog_json, value, sizeof(analog_json));
    }
  }

  snprintf(telemetry_json, sizeof(telemetry_json),
           "{\"type\":\"telemetry\",\"ms\":%lu,\"mic\":%lu,\"mic_pct\":%d,\"battery_v\":%.3f,\"led\":%d,"
           "\"ble_supported\":%s,\"ble_enabled\":%s,\"ble_connected\":%s,\"ble_name\":\"%s\","
           "\"wifi_supported\":false,\"imu_ok\":%s,\"mic_ok\":%s,"
           "\"accel\":{\"x\":%.4f,\"y\":%.4f,\"z\":%.4f},"
           "\"gyro\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},\"analog\":[%s]}",
           millis(), mic_level, mic_pct, batt, led_brightness,
           BLE_SUPPORTED ? "true" : "false",
           ble_enabled ? "true" : "false",
           ble_connected ? "true" : "false",
           BLE_NAME,
           imu_ok ? "true" : "false",
           mic_ok ? "true" : "false",
           ax, ay, az, gx, gy, gz, analog_json);

  Serial.println(telemetry_json);
#if BLE_SUPPORTED
  ble_update_telemetry(batt, ax, ay, az, gx, gy, gz, mic_pct, analog_json);
#endif
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  uint32_t serial_wait_start = millis();
  while (!Serial && millis() - serial_wait_start < 1500) {
    delay(10);
  }
  Serial.println("{\"type\":\"boot\",\"step\":\"serial\"}");

  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(IMU_POWER_PIN, OUTPUT);
  pinMode(BATTERY_ENABLE_PIN, OUTPUT);

  digitalWrite(IMU_POWER_PIN, HIGH);
  digitalWrite(BATTERY_ENABLE_PIN, HIGH);
  delay(300);
  Serial.println("{\"type\":\"boot\",\"step\":\"power\"}");

  imu_ok = false;
  if (ENABLE_IMU) {
    imu_ok = (imu.begin() == 0);
  }
  Serial.print("{\"type\":\"boot\",\"step\":\"imu\",\"ok\":");
  Serial.print(imu_ok ? "true" : "false");
  Serial.println("}");

  if (ENABLE_MIC) {
    mic.begin(mic_buffer, MIC_SAMPLES);
    mic_ok = true;
  }
  Serial.print("{\"type\":\"boot\",\"step\":\"mic\",\"ok\":");
  Serial.print(mic_ok ? "true" : "false");
  Serial.println("}");
  if (mic_ok) {
    mic.startSampling(mic_samples_ready_cb);
  }

  ble_enabled = BLE_SUPPORTED;
#if BLE_SUPPORTED
  ble_start_advertising();
#endif
  Serial.print("{\"type\":\"boot\",\"step\":\"ble\",\"supported\":");
  Serial.print(BLE_SUPPORTED ? "true" : "false");
  Serial.print(",\"enabled\":");
  Serial.print(ble_enabled ? "true" : "false");
  Serial.println("}");

  led_brightness = 0;
  update_led();
  runtime_configuration_store.write(current_stored_configuration(), millis(), true);

  Serial.println("{\"type\":\"hello\",\"board\":\"Seeed Studio XIAO MG24 Sense\",\"baud\":115200}");
}

#if BLE_SUPPORTED
void sl_bt_on_event(sl_bt_msg_t *evt) {
  switch (SL_BT_MSG_ID(evt->header)) {
    case sl_bt_evt_system_boot_id:
      ble_initialize_gatt_db();
      ble_start_advertising();
      break;
    case sl_bt_evt_connection_opened_id:
      ble_connection_handle = evt->data.evt_connection_opened.connection;
      ble_connected = true;
      break;
    case sl_bt_evt_connection_closed_id:
      ble_connection_handle = 0xff;
      ble_connected = false;
      ble_notify_enabled = false;
      ble_start_advertising();
      break;
    case sl_bt_evt_gatt_server_characteristic_status_id:
      if (evt->data.evt_gatt_server_characteristic_status.characteristic == ble_telemetry_characteristic_handle) {
        ble_notify_enabled = evt->data.evt_gatt_server_characteristic_status.client_config_flags & sl_bt_gatt_notification;
      }
      break;
    case sl_bt_evt_gatt_server_attribute_value_id:
      if (evt->data.evt_gatt_server_attribute_value.attribute == ble_command_characteristic_handle) {
        char command[64];
        uint8_t len = evt->data.evt_gatt_server_attribute_value.value.len;
        if (len >= sizeof(command)) {
          len = sizeof(command) - 1;
        }
        memcpy(command, evt->data.evt_gatt_server_attribute_value.value.data, len);
        command[len] = '\0';
        handle_command(String(command));
      }
      break;
    default:
      break;
  }
}

void ble_initialize_gatt_db() {
  sl_status_t sc;
  uint16_t session_id;
  uint16_t generic_access_service_handle;
  uint16_t device_name_characteristic_handle;
  uint16_t telemetry_service_handle;

  sc = sl_bt_gattdb_new_session(&session_id);
  app_assert_status(sc);

  const uint8_t generic_access_service_uuid[] = { 0x00, 0x18 };
  sc = sl_bt_gattdb_add_service(session_id, sl_bt_gattdb_primary_service, SL_BT_GATTDB_ADVERTISED_SERVICE,
                                sizeof(generic_access_service_uuid), generic_access_service_uuid,
                                &generic_access_service_handle);
  app_assert_status(sc);

  const sl_bt_uuid_16_t device_name_characteristic_uuid = { .data = { 0x00, 0x2A } };
  sc = sl_bt_gattdb_add_uuid16_characteristic(session_id, generic_access_service_handle,
                                             SL_BT_GATTDB_CHARACTERISTIC_READ, 0x00, 0x00,
                                             device_name_characteristic_uuid, sl_bt_gattdb_fixed_length_value,
                                             sizeof(ble_advertised_name) - 1, sizeof(ble_advertised_name) - 1,
                                             ble_advertised_name, &device_name_characteristic_handle);
  app_assert_status(sc);

  sc = sl_bt_gattdb_start_service(session_id, generic_access_service_handle);
  app_assert_status(sc);

  sc = sl_bt_gattdb_add_service(session_id, sl_bt_gattdb_primary_service, SL_BT_GATTDB_ADVERTISED_SERVICE,
                                sizeof(telemetry_service_uuid.data), telemetry_service_uuid.data,
                                &telemetry_service_handle);
  app_assert_status(sc);

  const uint8_t empty_value = 0;
  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_READ | SL_BT_GATTDB_CHARACTERISTIC_NOTIFY,
                                              0x00, 0x00, telemetry_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, sizeof(ble_json),
                                              1, &empty_value, &ble_telemetry_characteristic_handle);
  app_assert_status(sc);

  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_WRITE,
                                              0x00, 0x00, command_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, 64,
                                              1, &empty_value, &ble_command_characteristic_handle);
  app_assert_status(sc);

  snprintf(capabilities_json, sizeof(capabilities_json),
           "{\"schema_version\":1,\"node_id\":\"%s\",\"firmware_version\":\"%s\","
           "\"interfaces\":[{\"interface_id\":\"MIC\",\"type\":\"built_in\",\"capabilities\":[\"built_in_microphone\"]},"
           "{\"interface_id\":\"IMU0\",\"type\":\"built_in\",\"capabilities\":[\"built_in_imu_accelerometer\",\"built_in_imu_gyroscope\"]},"
           "{\"interface_id\":\"VBAT\",\"type\":\"built_in\",\"capabilities\":[\"built_in_battery\"]},"
           "{\"interface_id\":\"D0\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D1\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D2\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D3\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D4\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D5\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]}],"
           "\"processing\":{\"filters\":[\"none\",\"ema\",\"moving_average\",\"median\",\"digital_debounce\"],"
           "\"reporting_modes\":[\"periodic\",\"change\",\"event\",\"heartbeat\"]},"
           "\"configuration\":{\"persistence\":\"none\",\"readback\":false}}",
           DEVICE_ID, FIRMWARE_VERSION);
  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_READ,
                                              0x00, 0x00, capabilities_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, sizeof(capabilities_json),
                                              strlen(capabilities_json), (const uint8_t*)capabilities_json,
                                              &ble_capabilities_characteristic_handle);
  app_assert_status(sc);

  snprintf(metadata_json, sizeof(metadata_json),
           "{\"node_id\":\"%s\",\"sensor_package_version\":\"%s\",\"firmware_version\":\"%s\","
           "\"protocol_version\":\"%s\",\"configuration_schema_version\":%d,\"build_identifier\":\"%s\","
           "\"git_commit\":\"%s\"}", DEVICE_ID, SENSOR_PACKAGE_VERSION, FIRMWARE_VERSION,
           PROTOCOL_VERSION, CONFIGURATION_SCHEMA_VERSION, BUILD_IDENTIFIER, FIRMWARE_GIT_COMMIT);
  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_READ,
                                              0x00, 0x00, metadata_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, sizeof(metadata_json),
                                              strlen(metadata_json), (const uint8_t*)metadata_json,
                                              &ble_metadata_characteristic_handle);
  app_assert_status(sc);

  sc = sl_bt_gattdb_start_service(session_id, telemetry_service_handle);
  app_assert_status(sc);
  sc = sl_bt_gattdb_commit(session_id);
  app_assert_status(sc);
}

void ble_start_advertising() {
  if (!ble_enabled) {
    return;
  }

  sl_status_t sc;
  if (ble_advertising_set_handle == 0xff) {
    sc = sl_bt_advertiser_create_set(&ble_advertising_set_handle);
    app_assert_status(sc);
    sc = sl_bt_advertiser_set_timing(ble_advertising_set_handle, 160, 160, 0, 0);
    app_assert_status(sc);
  }

  sc = sl_bt_legacy_advertiser_generate_data(ble_advertising_set_handle, sl_bt_advertiser_general_discoverable);
  app_assert_status(sc);
  sc = sl_bt_legacy_advertiser_start(ble_advertising_set_handle, sl_bt_advertiser_connectable_scannable);
  app_assert_status(sc);
}

void ble_update_telemetry(float batt, float ax, float ay, float az, float gx, float gy, float gz, int mic_pct, const char* analog_json) {
  if (!ble_enabled || ble_telemetry_characteristic_handle == 0) {
    return;
  }

  snprintf(ble_json, sizeof(ble_json),
           "{\"t\":\"tele\",\"v\":1,\"s\":%lu,\"ms\":%lu,\"m\":%lu,\"mp\":%d,\"bv\":%.3f,\"l\":%d,"
           "\"bs\":1,\"be\":%d,\"bc\":%d,\"imu\":%d,\"mk\":%d,"
           "\"a\":[%.3f,%.3f,%.3f],\"g\":[%.2f,%.2f,%.2f],\"n\":[%s]}",
           (unsigned long)telemetry_sequence++, millis(), mic_level, mic_pct, batt, led_brightness,
           ble_enabled ? 1 : 0, ble_connected ? 1 : 0, imu_ok ? 1 : 0, mic_ok ? 1 : 0,
           ax, ay, az, gx, gy, gz, analog_json);
  char current_telemetry[sizeof(ble_json)];
  strlcpy(current_telemetry, ble_json, sizeof(current_telemetry));

  if (!ble_connected) {
    TelemetryRecord record = {};
    record.type = RecordType::Measurement; record.priority = RecordPriority::Routine;
    record.sequence_number = telemetry_sequence - 1; record.uptime_ms = millis();
    strlcpy(record.channel_id, "microphone_raw", sizeof(record.channel_id));
    record.raw_value = mic_level; record.normalized_value = 0; record.has_normalized_value = false;
    record.quality = MeasurementQuality::Uncalibrated; record.state = MonitoringState::Normal; record.delayed = false;
    offline_buffer.push(record);
  } else {
    TelemetryRecord delayed;
    if (offline_buffer.pop(&delayed) && encode_record(delayed, DEVICE_ID, ble_json, sizeof(ble_json))) {
      ble_send_payload(ble_json);
    }
    ble_send_payload(current_telemetry);
  }
}

bool ble_send_payload(const char* payload) {
  size_t len = strlen(payload);
  if (len == 0 || len >= sizeof(ble_json)) {
    processing_error_count++;
    return false;
  }
  sl_bt_gatt_server_write_attribute_value(ble_telemetry_characteristic_handle, 0, len, (const uint8_t*)payload);
  if (ble_connected && ble_notify_enabled) {
    return sl_bt_gatt_server_notify_all(ble_telemetry_characteristic_handle, len, (const uint8_t*)payload) == SL_STATUS_OK;
  }
  return true;
}
#endif

void loop() {
  while (Serial.available()) {
    handle_command(Serial.readStringUntil('\n'));
  }

  update_microphone();

  const uint32_t now = millis();
  if (elapsed_since(now, last_sample_ms, microphone_runtime_config.report_interval_ms)) {
    last_sample_ms = now;
    print_telemetry();
  }
#if BLE_SUPPORTED
  if (ble_connected && elapsed_since(now, last_heartbeat_ms, microphone_runtime_config.heartbeat_interval_ms)) {
    last_heartbeat_ms = now;
    if (encode_heartbeat(telemetry_sequence++, now, battery_voltage(), offline_buffer.size(),
                         offline_buffer.dropped_count(), processing_error_count, sensor_error_count,
                         DEVICE_ID, ble_json, sizeof(ble_json))) {
      ble_send_payload(ble_json);
    } else {
      processing_error_count++;
    }
  }
#endif
}
