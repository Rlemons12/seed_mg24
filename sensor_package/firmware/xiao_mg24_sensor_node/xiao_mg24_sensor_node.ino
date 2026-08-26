#include <Arduino.h>
#include <Wire.h>
#include <LSM6DS3.h>
#include <ArduinoLowPower.h>
#define ENABLE_MIC 0
#if ENABLE_MIC
#include <SilabsMicrophoneAnalog.h>
#endif
#include "sensor_config.h"
#include "sensor_channel.h"
#include "telemetry_buffer.h"
#include "telemetry_encoder.h"
#include "configuration_store.h"
#include "factory_reset.h"
#include "node_identity_store.h"
#include "nvm_backend.h"
#include "usb_bootstrap.h"
#include "silabs_additional.h"
#include "vibration_service.h"
#include <vibration_summary.h>
#if defined(ARDUINO_SILABS_STACK_BLE_SILABS)
#include "sl_bluetooth.h"
#include "sha256_minimal.h"
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
#define ENABLE_IMU 1
#define ENABLE_ANALOG 1
#define ENABLE_BATTERY 1
#define EDGE_SUMMARY_INTERVAL_MS 60000UL
#define EDGE_HEARTBEAT_INTERVAL_MS 300000UL
#define LOW_POWER_REPORT_INTERVAL_MS 300000UL
#define LOW_POWER_SLEEP_SLICE_MS 1000UL
#define IMU_RETRY_INTERVAL_MS 30000UL
#define IMU_MAX_INITIALIZATION_ATTEMPTS 5

LSM6DS3 imu(I2C_MODE, 0x6A);
ProductionVibrationService vibration_service(imu, Wire1);
#if ENABLE_MIC
MicrophoneAnalog mic(MIC_DATA_PIN, MIC_PWR_PIN);
uint32_t mic_buffer[MIC_SAMPLES];
uint32_t mic_buffer_local[MIC_SAMPLES];
volatile bool mic_ready = false;
#endif

uint32_t sample_interval_ms = 100;
uint32_t last_sample_ms = 0;
uint32_t last_heartbeat_ms = 0;
uint32_t telemetry_sequence = 0;
char telemetry_boot_id[17] = "0000000000000000";
uint32_t last_serial_telemetry_ms = 0;
enum EdgeTelemetryMode : uint8_t { EDGE_SUMMARY_MODE = 0, LIVE_MODE = 1, LOW_POWER_MODE = 2 };
EdgeTelemetryMode reporting_mode = EDGE_SUMMARY_MODE;
uint32_t last_edge_sample_ms = 0, last_edge_report_ms = 0, last_edge_vibration_ms = 0;
uint32_t last_low_power_report_ms = 0;
bool low_power_rails_suspended = false;
volatile bool low_power_exit_pending = false;
struct EdgeAccumulator { double battery, mic, accel[3], gyro[3], analog[6]; uint32_t count; } edge = {};
uint32_t processing_error_count = 0;
uint32_t sensor_error_count = 0;
uint32_t mic_level = 0;
int led_brightness = 0;
bool imu_ok = false;
bool mic_ok = false;
bool ble_enabled = BLE_SUPPORTED;
bool ble_connected = false;
bool ble_notify_enabled = false;
bool ble_vibration_notify_enabled = false;
volatile bool ble_command_pending = false;
volatile bool ble_system_booted = false;
bool application_setup_complete = false;
bool ble_database_initialized = false;
bool vibration_initialized = false;
uint8_t imu_initialization_attempts = 0;
uint8_t vibration_initialization_attempts = 0;
uint32_t last_imu_initialization_ms = 0;
uint32_t last_vibration_initialization_ms = 0;
int imu_initialization_status = -1;
uint8_t imu_who_am_i = 0;
char pending_ble_command[192] = {};
#if BLE_SUPPORTED
uint8_t ble_connection_handle = 0xff;
uint8_t ble_advertising_set_handle = 0xff;
uint16_t ble_telemetry_characteristic_handle = 0;
uint16_t ble_command_characteristic_handle = 0;
uint16_t ble_metadata_characteristic_handle = 0;
uint16_t ble_capabilities_characteristic_handle = 0;
uint16_t ble_onboarding_identity_characteristic_handle = 0;
uint16_t ble_vibration_characteristic_handle = 0;
#endif
char telemetry_json[512];
char ble_json[244];
char vibration_json[seed_mg24::kVibrationSummaryMaximumBytes];
uint32_t last_vibration_summary_sequence = 0;
char metadata_json[384];
char capabilities_json[1280];
char onboarding_identity_json[192];
ChannelConfig microphone_runtime_config = MICROPHONE_CHANNEL_CONFIG;
SensorChannel microphone_channel(&microphone_runtime_config);
TelemetryBuffer offline_buffer;
SiliconLabsNvm3Backend application_nvm;
NodeIdentityStore node_identity_store(application_nvm);
PersistentConfigurationStore runtime_configuration_store(application_nvm);
FactoryResetController factory_reset_controller(application_nvm);
UsbBootstrapProtocol usb_bootstrap(node_identity_store, runtime_configuration_store, factory_reset_controller);
NodeIdentity active_identity = {};
StoreStatus identity_store_status = StoreStatus::Unprovisioned;
StoreStatus configuration_store_status = StoreStatus::NotFound;
StoreStatus reset_recovery_status = StoreStatus::Ok;
bool bootstrap_only = true;
bool ble_stack_ready = false;

bool application_ble_stack_ready() { return ble_stack_ready; }

bool initialize_imu();
void enter_low_power_mode();
void exit_low_power_mode();
void publish_low_power_snapshot();
void print_imu_status();

const char* runtime_node_id() {
  return (identity_store_status == StoreStatus::Ok || identity_store_status == StoreStatus::RecoveredFromPrevious)
    ? active_identity.node_id : "UNASSIGNED-MG24";
}

const uint8_t analog_pins[] = {
  D0, D1, D2, D3, D4, D5
};

void mic_samples_ready_cb() {
#if ENABLE_MIC
  memcpy(mic_buffer_local, mic_buffer, MIC_SAMPLES * sizeof(uint32_t));
  mic_ready = true;
#endif
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
static const uuid_128 onboarding_identity_characteristic_uuid = {
  .data = { 0xef, 0xbe, 0x24, 0x00, 0x24, 0x47, 0x4d, 0x2d, 0x80, 0x24, 0x24, 0x47, 0x4d, 0x00, 0x00, 0x06 }
};
static const uuid_128 vibration_characteristic_uuid = {
  .data = { 0xef, 0xbe, 0x24, 0x00, 0x24, 0x47, 0x4d, 0x2d, 0x80, 0x24, 0x24, 0x47, 0x4d, 0x00, 0x00, 0x07 }
};

void ble_initialize_gatt_db();
void ble_start_advertising();
void ble_write_attribute_chunks(uint16_t characteristic, const uint8_t* value, size_t length);
void ble_update_telemetry(float batt, float ax, float ay, float az, float gx, float gy, float gz, uint32_t mic_raw, int mic_pct, const char* analog_json, uint32_t sample_count = 1);
bool ble_send_payload(const char* payload);
bool ble_send_oldest_buffered();
void generate_telemetry_boot_id();
void ble_publish_vibration();
void ble_initialize_when_ready();
void ble_refresh_onboarding_identity();
#endif

bool parse_bounded_uint(const String& text, uint32_t minimum, uint32_t maximum, uint32_t* output) {
  if (!output || text.length() == 0) return false;
  for (size_t i = 0; i < text.length(); ++i) if (!isDigit(text[i])) return false;
  unsigned long value = strtoul(text.c_str(), nullptr, 10);
  if (value < minimum || value > maximum) return false;
  *output = (uint32_t)value;
  return true;
}

static bool next_command_token(const String& command, int* offset, String* token) {
  if (!offset || !token || *offset < 0 || *offset >= (int)command.length()) return false;
  int end = command.indexOf(' ', *offset);
  if (end < 0) end = command.length();
  *token = command.substring(*offset, end);
  *offset = end + 1;
  return token->length() > 0;
}

void command_result(bool accepted, const char* code) {
  char response[120];
  snprintf(response, sizeof(response), "{\"t\":\"%s\",\"v\":1,\"id\":\"%s\",\"s\":%lu,\"ms\":%lu,\"code\":\"%s\"}",
           accepted ? "ca" : "ce", runtime_node_id(), (unsigned long)telemetry_sequence++, (unsigned long)millis(), code);
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
           runtime_node_id(), (unsigned long)telemetry_sequence++, (unsigned long)millis(),
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

void report_provisioning_state(const char* transaction_id, const char* code) {
  char response[244];
  snprintf(response, sizeof(response),
           "{\"t\":\"ca\",\"v\":1,\"id\":\"%s\",\"tx\":\"%s\",\"code\":\"%s\","
           "\"sample\":%lu,\"process\":%lu,\"report\":%lu,\"heartbeat\":%lu,\"filter\":%u,\"window\":%u,\"enabled\":%u}",
           runtime_node_id(), transaction_id ? transaction_id : "", code,
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

bool handle_provision_command(const String& command) {
  // Bounded versioned transaction. Identity is written last and is the durable commit marker.
  if (factory_reset_controller.busy()) { command_result(false, "persistent_operation_busy"); return true; }
  if (!command.startsWith("PROV ") && !command.startsWith("PROVGET ")) return false;
  int offset = command.startsWith("PROVGET ") ? 8 : 5;
  String version, transaction_id;
  if (!next_command_token(command, &offset, &version) || !next_command_token(command, &offset, &transaction_id) ||
      version != "1" || transaction_id.length() < 8 || transaction_id.length() > 24) {
    command_result(false, "invalid_provisioning_header"); return true;
  }
  if (command.startsWith("PROVGET ")) {
    if (offset > (int)command.length()) { report_provisioning_state(transaction_id.c_str(), "readback"); return true; }
    command_result(false, "invalid_format"); return true;
  }
  String node_id, sample, process, report, heartbeat, filter, window, enabled;
  if (!next_command_token(command, &offset, &node_id) || !next_command_token(command, &offset, &sample) ||
      !next_command_token(command, &offset, &process) || !next_command_token(command, &offset, &report) ||
      !next_command_token(command, &offset, &heartbeat) || !next_command_token(command, &offset, &filter) ||
      !next_command_token(command, &offset, &window) || !next_command_token(command, &offset, &enabled) ||
      offset <= (int)command.length()) {
    command_result(false, "invalid_format"); return true;
  }
  if (!NodeIdentityStore::valid_node_id(node_id.c_str())) { command_result(false, "invalid_node_id"); return true; }
  NodeIdentity existing = {};
  StoreStatus existing_status = node_identity_store.load(&existing);
  if (existing_status == StoreStatus::Ok || existing_status == StoreStatus::RecoveredFromPrevious) {
    if (strcmp(existing.node_id, node_id.c_str()) == 0) {
      active_identity = existing; identity_store_status = existing_status;
      report_provisioning_state(transaction_id.c_str(), "already_committed");
    } else command_result(false, "identity_already_provisioned");
    return true;
  }
  if (existing_status != StoreStatus::Unprovisioned) { command_result(false, store_status_name(existing_status)); return true; }
  uint32_t sample_value, process_value, report_value, heartbeat_value, filter_value, window_value, enabled_value;
  if (!parse_bounded_uint(sample, 50, 5000, &sample_value) ||
      !parse_bounded_uint(process, 50, 5000, &process_value) ||
      !parse_bounded_uint(report, 50, 5000, &report_value) ||
      !parse_bounded_uint(heartbeat, 1000, 3600000, &heartbeat_value) ||
      !parse_bounded_uint(filter, 0, 3, &filter_value) ||
      !parse_bounded_uint(window, 1, MAX_FILTER_WINDOW, &window_value) ||
      !parse_bounded_uint(enabled, 0, 1, &enabled_value)) {
    command_result(false, "invalid_configuration"); return true;
  }
  StoredChannelConfiguration staged = {};
  staged.sample_interval_ms = sample_value; staged.processing_interval_ms = process_value;
  staged.report_interval_ms = report_value; staged.heartbeat_interval_ms = heartbeat_value;
  staged.filter_type = (uint8_t)filter_value; staged.filter_window = (uint8_t)window_value;
  staged.enabled = (uint8_t)enabled_value;
  StoredChannelConfiguration verified = {};
  StoreStatus stored = runtime_configuration_store.write(staged, &verified);
  if (stored != StoreStatus::Ok) { command_result(false, store_status_name(stored)); return true; }
  NodeIdentity provisioned = {};
  stored = node_identity_store.provision(node_id.c_str(), &provisioned);
  if (stored != StoreStatus::Ok) { command_result(false, store_status_name(stored)); return true; }
  active_identity = provisioned; identity_store_status = StoreStatus::Ok;
  bootstrap_only = false;
  microphone_runtime_config.sample_interval_ms = verified.sample_interval_ms;
  microphone_runtime_config.processing_interval_ms = verified.processing_interval_ms;
  microphone_runtime_config.report_interval_ms = verified.report_interval_ms;
  microphone_runtime_config.heartbeat_interval_ms = verified.heartbeat_interval_ms;
  microphone_runtime_config.filter_type = (FilterType)verified.filter_type;
  microphone_runtime_config.filter_window = verified.filter_window;
  microphone_runtime_config.enabled = verified.enabled != 0;
  sample_interval_ms = verified.sample_interval_ms;
  microphone_channel.reconfigure(&microphone_runtime_config);
#if BLE_SUPPORTED
  ble_refresh_onboarding_identity();
  snprintf(metadata_json, sizeof(metadata_json),
           "{\"node_id\":\"%s\",\"sensor_package_version\":\"%s\",\"firmware_version\":\"%s\","
           "\"protocol_version\":\"%s\",\"configuration_schema_version\":%d,\"build_identifier\":\"%s\","
           "\"git_commit\":\"%s\"}", runtime_node_id(), SENSOR_PACKAGE_VERSION, FIRMWARE_VERSION,
           PROTOCOL_VERSION, CONFIGURATION_SCHEMA_VERSION, BUILD_IDENTIFIER, FIRMWARE_GIT_COMMIT);
  ble_write_attribute_chunks(ble_metadata_characteristic_handle, (const uint8_t*)metadata_json, strlen(metadata_json));
  // The only identity occurrence in capabilities is the leading node_id value.
  char* identity_end = strstr(capabilities_json, "\",\"firmware_version\"");
  if (identity_end) {
    char tail[sizeof(capabilities_json)];
    strncpy(tail, identity_end, sizeof(tail) - 1); tail[sizeof(tail) - 1] = '\0';
    snprintf(capabilities_json, sizeof(capabilities_json), "{\"schema_version\":1,\"node_id\":\"%s%s", runtime_node_id(), tail);
    ble_write_attribute_chunks(ble_capabilities_characteristic_handle, (const uint8_t*)capabilities_json, strlen(capabilities_json));
  }
#endif
  report_provisioning_state(transaction_id.c_str(), "provisioned");
  return true;
}

bool handle_configuration_transaction(const String& command) {
  if (factory_reset_controller.busy() || bootstrap_only) { command_result(false, "bootstrap_only"); return true; }
  // Atomic device-level persistent configuration. Permanent node identity is never changed here.
  if (!command.startsWith("CFGSET ")) return false;
  int offset = 7;
  String version, transaction_id, sample, process, report, heartbeat, filter, window, enabled;
  if (!next_command_token(command, &offset, &version) || !next_command_token(command, &offset, &transaction_id) ||
      !next_command_token(command, &offset, &sample) || !next_command_token(command, &offset, &process) ||
      !next_command_token(command, &offset, &report) || !next_command_token(command, &offset, &heartbeat) ||
      !next_command_token(command, &offset, &filter) || !next_command_token(command, &offset, &window) ||
      !next_command_token(command, &offset, &enabled) || offset <= (int)command.length() || version != "1" ||
      transaction_id.length() < 8 || transaction_id.length() > 24) {
    command_result(false, "invalid_configuration_header"); return true;
  }
  NodeIdentity existing = {};
  StoreStatus existing_status = node_identity_store.load(&existing);
  if (existing_status != StoreStatus::Ok && existing_status != StoreStatus::RecoveredFromPrevious) {
    command_result(false, "identity_unassigned"); return true;
  }
  uint32_t sample_value, process_value, report_value, heartbeat_value, filter_value, window_value, enabled_value;
  if (!parse_bounded_uint(sample, 50, 5000, &sample_value) ||
      !parse_bounded_uint(process, 50, 5000, &process_value) ||
      !parse_bounded_uint(report, 50, 5000, &report_value) ||
      !parse_bounded_uint(heartbeat, 1000, 3600000, &heartbeat_value) ||
      !parse_bounded_uint(filter, 0, 3, &filter_value) ||
      !parse_bounded_uint(window, 1, MAX_FILTER_WINDOW, &window_value) ||
      !parse_bounded_uint(enabled, 0, 1, &enabled_value)) {
    command_result(false, "invalid_configuration"); return true;
  }
  StoredChannelConfiguration staged = {};
  staged.sample_interval_ms = sample_value; staged.processing_interval_ms = process_value;
  staged.report_interval_ms = report_value; staged.heartbeat_interval_ms = heartbeat_value;
  staged.filter_type = (uint8_t)filter_value; staged.filter_window = (uint8_t)window_value;
  staged.enabled = (uint8_t)enabled_value;
  StoredChannelConfiguration verified = {};
  StoreStatus stored = runtime_configuration_store.write(staged, &verified);
  if (stored != StoreStatus::Ok) { command_result(false, store_status_name(stored)); return true; }
  microphone_runtime_config.sample_interval_ms = verified.sample_interval_ms;
  microphone_runtime_config.processing_interval_ms = verified.processing_interval_ms;
  microphone_runtime_config.report_interval_ms = verified.report_interval_ms;
  microphone_runtime_config.heartbeat_interval_ms = verified.heartbeat_interval_ms;
  microphone_runtime_config.filter_type = (FilterType)verified.filter_type;
  microphone_runtime_config.filter_window = verified.filter_window;
  microphone_runtime_config.enabled = verified.enabled != 0;
  sample_interval_ms = verified.sample_interval_ms;
  microphone_channel.reconfigure(&microphone_runtime_config);
  report_provisioning_state(transaction_id.c_str(), "configured");
  return true;
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
    microphone_runtime_config = MICROPHONE_CHANNEL_CONFIG; sample_interval_ms = microphone_runtime_config.sample_interval_ms;
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
  StoredChannelConfiguration verified = {};
  StoreStatus stored = runtime_configuration_store.write(current_stored_configuration(), &verified);
  command_result(stored == StoreStatus::Ok, stored == StoreStatus::Ok ? "applied_persistent" : store_status_name(stored));
  return true;
}

void handle_command(String command) {
  command.trim();
  if (command.startsWith(kBootstrapPrefix)) {
    command_result(false, "usb_only_command");
    return;
  }
  command.toUpperCase();

  if (command.startsWith("TACK 2 ")) {
    int separator = command.indexOf(' ', 7);
    if (separator < 0) { command_result(false, "invalid_ack"); return; }
    String acknowledged_boot = command.substring(7, separator);
    String acknowledged_sequence = command.substring(separator + 1);
    uint32_t sequence = 0;
    if (!acknowledged_boot.equalsIgnoreCase(telemetry_boot_id) ||
        !parse_bounded_uint(acknowledged_sequence, 0, 0xFFFFFFFFUL, &sequence)) {
      command_result(false, "invalid_ack"); return;
    }
    offline_buffer.acknowledge_through(sequence);
#if BLE_SUPPORTED
    ble_send_oldest_buffered();
#endif
    return;
  }

  if (bootstrap_only && !command.startsWith("PROV ") && !command.startsWith("PROVGET ")) {
    command_result(false, "bootstrap_only");
    return;
  }

  if (handle_provision_command(command)) return;
  if (handle_configuration_transaction(command)) return;

  if (command == "VIBRATION STATUS" || command == "VIBRATION_STATUS") {
    print_imu_status();
    vibration_service.printHealth(Serial);
    return;
  }
  if (command == "IMU STATUS" || command == "IMU_STATUS") {
    print_imu_status();
    return;
  }

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
  } else if (command == "MODE LIVE") {
    exit_low_power_mode();
    reporting_mode = LIVE_MODE;
    memset(&edge, 0, sizeof(edge));
    command_result(true, "mode_live");
  } else if (command == "MODE EDGE_SUMMARY") {
    exit_low_power_mode();
    reporting_mode = EDGE_SUMMARY_MODE;
    memset(&edge, 0, sizeof(edge));
    last_edge_sample_ms = millis();
    last_edge_report_ms = millis();
    command_result(true, "mode_edge_summary");
  } else if (command == "MODE LOW_POWER") {
    reporting_mode = LOW_POWER_MODE;
    enter_low_power_mode();
    command_result(true, "mode_low_power");
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
#if ENABLE_MIC
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
#endif
}

bool initialize_imu() {
  if (!ENABLE_IMU || imu_initialization_attempts >= IMU_MAX_INITIALIZATION_ATTEMPTS) return false;
  if (imu_initialization_attempts > 0) {
    // A failed WHO_AM_I read can leave the sensor rail or bus peripheral in a
    // bad startup state. Cycle only the IMU rail; identity, NVM, BLE, battery
    // tracking, and the MCU remain running.
    digitalWrite(IMU_POWER_PIN, LOW);
    delay(50);
    digitalWrite(IMU_POWER_PIN, HIGH);
    delay(300);
  }
  imu_initialization_attempts++;
  last_imu_initialization_ms = millis();
  imu.settings.accelRange = 16;
  imu.settings.accelSampleRate = 416;
  imu.settings.accelBandWidth = 100;
  imu.settings.gyroRange = 2000;
  imu.settings.gyroSampleRate = 416;
  imu_initialization_status = (int)imu.begin();
  Wire1.setClock(400000);
  imu_who_am_i = 0;
  imu.readRegister(&imu_who_am_i, LSM6DS3_ACC_GYRO_WHO_AM_I_REG);
  imu_ok = imu_initialization_status == IMU_SUCCESS &&
      (imu_who_am_i == LSM6DS3_ACC_GYRO_WHO_AM_I || imu_who_am_i == LSM6DS3_C_ACC_GYRO_WHO_AM_I);
  Serial.print("{\"type\":\"imu_initialization\",\"attempt\":"); Serial.print(imu_initialization_attempts);
  Serial.print(",\"status\":"); Serial.print(imu_initialization_status);
  Serial.print(",\"who_am_i\":"); Serial.print(imu_who_am_i);
  Serial.print(",\"ok\":"); Serial.print(imu_ok ? "true" : "false"); Serial.println("}");
  if (!imu_ok) sensor_error_count++;
  return imu_ok;
}

void enter_low_power_mode() {
  memset(&edge, 0, sizeof(edge));
  led_brightness = 0;
  update_led();
  digitalWrite(IMU_POWER_PIN, LOW);
  digitalWrite(BATTERY_ENABLE_PIN, LOW);
  low_power_rails_suspended = true;
  last_low_power_report_ms = millis();
  last_heartbeat_ms = last_low_power_report_ms;
}

void exit_low_power_mode() {
  if (!low_power_rails_suspended) return;
  digitalWrite(BATTERY_ENABLE_PIN, HIGH);
  digitalWrite(IMU_POWER_PIN, HIGH);
  delay(300);
  imu_initialization_attempts = 0;
  vibration_initialization_attempts = 0;
  vibration_initialized = false;
  initialize_imu();
  low_power_rails_suspended = false;
}

void publish_low_power_snapshot() {
  digitalWrite(BATTERY_ENABLE_PIN, HIGH);
  digitalWrite(IMU_POWER_PIN, HIGH);
  delay(300);

  imu.settings.accelRange = 16;
  imu.settings.accelSampleRate = 416;
  imu.settings.accelBandWidth = 100;
  imu.settings.gyroRange = 2000;
  imu.settings.gyroSampleRate = 416;
  const int status = (int)imu.begin();
  Wire1.setClock(400000);
  uint8_t who_am_i = 0;
  imu.readRegister(&who_am_i, LSM6DS3_ACC_GYRO_WHO_AM_I_REG);
  const bool snapshot_imu_ok = status == IMU_SUCCESS &&
      (who_am_i == LSM6DS3_ACC_GYRO_WHO_AM_I || who_am_i == LSM6DS3_C_ACC_GYRO_WHO_AM_I);
  delay(20);

  const float ax = snapshot_imu_ok ? imu.readFloatAccelX() : 0.0f;
  const float ay = snapshot_imu_ok ? imu.readFloatAccelY() : 0.0f;
  const float az = snapshot_imu_ok ? imu.readFloatAccelZ() : 0.0f;
  const float gx = snapshot_imu_ok ? imu.readFloatGyroX() : 0.0f;
  const float gy = snapshot_imu_ok ? imu.readFloatGyroY() : 0.0f;
  const float gz = snapshot_imu_ok ? imu.readFloatGyroZ() : 0.0f;
  const float batt = battery_voltage();
  char analog_json[48] = "";
  if (ENABLE_ANALOG) for (size_t i = 0; i < sizeof(analog_pins); ++i) {
    if (i) strlcat(analog_json, ",", sizeof(analog_json));
    char value[8]; snprintf(value, sizeof(value), "%d", analogRead(analog_pins[i]));
    strlcat(analog_json, value, sizeof(analog_json));
  }
  imu_ok = snapshot_imu_ok;
#if BLE_SUPPORTED
  ble_update_telemetry(batt, ax, ay, az, gx, gy, gz, 0, 0, analog_json, 1);
#endif
  last_heartbeat_ms = millis();
  if (!snapshot_imu_ok) sensor_error_count++;
  digitalWrite(IMU_POWER_PIN, LOW);
  digitalWrite(BATTERY_ENABLE_PIN, LOW);
  low_power_rails_suspended = true;
}

void print_imu_status() {
  Serial.print("{\"type\":\"imu_status\",\"ok\":"); Serial.print(imu_ok ? "true" : "false");
  Serial.print(",\"initialization_status\":"); Serial.print(imu_initialization_status);
  Serial.print(",\"who_am_i\":"); Serial.print(imu_who_am_i);
  Serial.print(",\"initialization_attempts\":"); Serial.print(imu_initialization_attempts);
  Serial.print(",\"maximum_attempts\":"); Serial.print(IMU_MAX_INITIALIZATION_ATTEMPTS); Serial.println("}");
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
    seed_mg24::ImuRawSample sample = {};
    if (vibration_service.latestRawSample(&sample)) {
      constexpr float accel_g_per_count = 16.0f / 32768.0f;
      constexpr float gyro_dps_per_count = 2000.0f / 32768.0f;
      ax = sample.accel_x * accel_g_per_count;
      ay = sample.accel_y * accel_g_per_count;
      az = sample.accel_z * accel_g_per_count;
      gx = sample.gyro_x * gyro_dps_per_count;
      gy = sample.gyro_y * gyro_dps_per_count;
      gz = sample.gyro_z * gyro_dps_per_count;
    }
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

  // The debugger UART is 115200 baud: mirroring this ~350-byte JSON at the
  // 10 Hz BLE cadence consumes roughly one third of the CPU in blocking UART
  // writes. Preserve the same serial record and fields at a bounded 1 Hz while
  // BLE retains the configured report cadence.
  const uint32_t telemetry_now = millis();
  if (elapsed_since(telemetry_now, last_serial_telemetry_ms, 1000)) {
    last_serial_telemetry_ms = telemetry_now;
    Serial.println(telemetry_json);
  }
#if BLE_SUPPORTED
  ble_update_telemetry(batt, ax, ay, az, gx, gy, gz, mic_level, mic_pct, analog_json, 1);
#endif
}

void capture_edge_sample() {
  float ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
  if (imu_ok) {
    seed_mg24::ImuRawSample sample = {};
    if (vibration_service.latestRawSample(&sample)) {
      constexpr float accel_g_per_count = 16.0f / 32768.0f;
      constexpr float gyro_dps_per_count = 2000.0f / 32768.0f;
      ax = sample.accel_x * accel_g_per_count; ay = sample.accel_y * accel_g_per_count; az = sample.accel_z * accel_g_per_count;
      gx = sample.gyro_x * gyro_dps_per_count; gy = sample.gyro_y * gyro_dps_per_count; gz = sample.gyro_z * gyro_dps_per_count;
    }
  }
  edge.battery += battery_voltage(); edge.mic += mic_level;
  edge.accel[0] += ax; edge.accel[1] += ay; edge.accel[2] += az;
  edge.gyro[0] += gx; edge.gyro[1] += gy; edge.gyro[2] += gz;
  if (ENABLE_ANALOG) for (size_t i = 0; i < sizeof(analog_pins); ++i) edge.analog[i] += analogRead(analog_pins[i]);
  edge.count++;
}

void publish_edge_summary() {
  if (!edge.count) return;
  const double divisor = edge.count;
  const int average_mic = (int)(edge.mic / divisor);
  const int mic_pct = map(constrain(average_mic, MIC_VALUE_MIN, MIC_VALUE_MAX), MIC_VALUE_MIN, MIC_VALUE_MAX, 0, 100);
  char analog_json[48] = "";
  if (ENABLE_ANALOG) for (size_t i = 0; i < sizeof(analog_pins); ++i) {
    if (i) strlcat(analog_json, ",", sizeof(analog_json));
    char value[8]; snprintf(value, sizeof(value), "%d", (int)(edge.analog[i] / divisor));
    strlcat(analog_json, value, sizeof(analog_json));
  }
#if BLE_SUPPORTED
  ble_update_telemetry((float)(edge.battery / divisor),
      (float)(edge.accel[0] / divisor), (float)(edge.accel[1] / divisor), (float)(edge.accel[2] / divisor),
      (float)(edge.gyro[0] / divisor), (float)(edge.gyro[1] / divisor), (float)(edge.gyro[2] / divisor),
      average_mic, mic_pct, analog_json, edge.count);
#endif
  memset(&edge, 0, sizeof(edge));
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  uint32_t serial_wait_start = millis();
  while (!Serial && millis() - serial_wait_start < 1500) {
    delay(10);
  }
  Serial.println("{\"type\":\"boot\",\"step\":\"serial\"}");
  StoreStatus nvm_status = application_nvm.initialize();
  bool reset_recovery_pending = false;
  if (nvm_status == StoreStatus::Ok) {
    reset_recovery_status = factory_reset_controller.recover_on_boot(&reset_recovery_pending);
    identity_store_status = node_identity_store.load(&active_identity);
    StoredChannelConfiguration stored = {};
    configuration_store_status = runtime_configuration_store.load(&stored);
    if (configuration_store_status == StoreStatus::Ok || configuration_store_status == StoreStatus::RecoveredFromPrevious) {
      microphone_runtime_config.sample_interval_ms = stored.sample_interval_ms;
      microphone_runtime_config.processing_interval_ms = stored.processing_interval_ms;
      microphone_runtime_config.report_interval_ms = stored.report_interval_ms;
      microphone_runtime_config.heartbeat_interval_ms = stored.heartbeat_interval_ms;
      microphone_runtime_config.filter_type = static_cast<FilterType>(stored.filter_type);
      microphone_runtime_config.filter_window = stored.filter_window;
      microphone_runtime_config.enabled = stored.enabled == 1;
      microphone_channel.reconfigure(&microphone_runtime_config);
    }
  } else {
    identity_store_status = nvm_status;
    configuration_store_status = nvm_status;
  }
  const bool identity_valid = identity_store_status == StoreStatus::Ok || identity_store_status == StoreStatus::RecoveredFromPrevious;
  const bool configuration_valid = configuration_store_status == StoreStatus::Ok
      || configuration_store_status == StoreStatus::RecoveredFromPrevious || configuration_store_status == StoreStatus::NotFound;
  bootstrap_only = reset_recovery_status != StoreStatus::Ok || reset_recovery_pending || !identity_valid || !configuration_valid;
  if (reset_recovery_pending && reset_recovery_status == StoreStatus::Ok) {
    const bool reset_state_verified = identity_store_status == StoreStatus::Unprovisioned
        && configuration_store_status == StoreStatus::NotFound;
    reset_recovery_status = factory_reset_controller.complete_recovery_on_boot(reset_state_verified);
    bootstrap_only = true;
  }

  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(IMU_POWER_PIN, OUTPUT);
  pinMode(BATTERY_ENABLE_PIN, OUTPUT);

  digitalWrite(IMU_POWER_PIN, HIGH);
  digitalWrite(BATTERY_ENABLE_PIN, HIGH);
  delay(300);
  Serial.println("{\"type\":\"boot\",\"step\":\"power\"}");

  // Retry transient WHO_AM_I/I2C startup failures without resetting identity,
  // configuration, BLE, or the rest of the sensor application.
  imu_ok = initialize_imu();
  Serial.print("{\"type\":\"boot\",\"step\":\"imu\",\"ok\":");
  Serial.print(imu_ok ? "true" : "false");
  Serial.println("}");

  // FIFO acquisition starts after the one-time BLE GATT database build. That
  // startup operation is intentionally allowed to complete without filling the
  // FIFO; normal advertising is not delayed waiting for a vibration window.

#if ENABLE_MIC
  mic.begin(mic_buffer, MIC_SAMPLES);
  mic_ok = true;
#endif
  Serial.print("{\"type\":\"boot\",\"step\":\"mic\",\"ok\":");
  Serial.print(mic_ok ? "true" : "false");
  Serial.println("}");
#if ENABLE_MIC
  if (mic_ok) mic.startSampling(mic_samples_ready_cb);
#endif

  Serial.print("{\"type\":\"boot\",\"step\":\"ble\",\"supported\":");
  Serial.print(BLE_SUPPORTED ? "true" : "false");
  Serial.print(",\"enabled\":");
  Serial.print(ble_enabled ? "true" : "false");
  Serial.println("}");

  led_brightness = 0;
  update_led();
  Serial.println("{\"type\":\"hello\",\"board\":\"Seeed Studio XIAO MG24 Sense\",\"baud\":115200}");
  application_setup_complete = true;
}

#if BLE_SUPPORTED
void sl_bt_on_event(sl_bt_msg_t *evt) {
  switch (SL_BT_MSG_ID(evt->header)) {
    case sl_bt_evt_system_boot_id:
      ble_stack_ready = true;
      ble_system_booted = true;
      generate_telemetry_boot_id();
      break;
    case sl_bt_evt_connection_opened_id:
      ble_connection_handle = evt->data.evt_connection_opened.connection;
      ble_connected = true;
      break;
    case sl_bt_evt_connection_closed_id:
      ble_connection_handle = 0xff;
      ble_connected = false;
      ble_notify_enabled = false;
      ble_vibration_notify_enabled = false;
      if (reporting_mode == LOW_POWER_MODE) low_power_exit_pending = true;
      if (ble_database_initialized) ble_start_advertising();
      break;
    case sl_bt_evt_gatt_server_characteristic_status_id:
      if (evt->data.evt_gatt_server_characteristic_status.characteristic == ble_telemetry_characteristic_handle) {
        ble_notify_enabled = evt->data.evt_gatt_server_characteristic_status.client_config_flags & sl_bt_gatt_notification;
      } else if (evt->data.evt_gatt_server_characteristic_status.characteristic == ble_vibration_characteristic_handle) {
        ble_vibration_notify_enabled = evt->data.evt_gatt_server_characteristic_status.client_config_flags & sl_bt_gatt_notification;
      }
      break;
    case sl_bt_evt_gatt_server_attribute_value_id:
      if (evt->data.evt_gatt_server_attribute_value.attribute == ble_command_characteristic_handle) {
        size_t len = evt->data.evt_gatt_server_attribute_value.value.len;
        if (len >= sizeof(pending_ble_command) || ble_command_pending) {
          break;
        }
        memcpy(pending_ble_command, evt->data.evt_gatt_server_attribute_value.value.data, len);
        pending_ble_command[len] = '\0';
        ble_command_pending = true;
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
                                              SL_BT_GATTDB_CHARACTERISTIC_READ | SL_BT_GATTDB_CHARACTERISTIC_NOTIFY,
                                              0x00, 0x00, vibration_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, sizeof(vibration_json),
                                              1, &empty_value, &ble_vibration_characteristic_handle);
  app_assert_status(sc);

  // Correlation-only bootstrap identity. It is read-only and is never included in advertising data.
  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_READ,
                                              0x00, 0x00, onboarding_identity_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, sizeof(onboarding_identity_json),
                                              1, &empty_value,
                                              &ble_onboarding_identity_characteristic_handle);
  app_assert_status(sc);

  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_WRITE,
                                              0x00, 0x00, command_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, 191,
                                              1, &empty_value, &ble_command_characteristic_handle);
  app_assert_status(sc);

  snprintf(capabilities_json, sizeof(capabilities_json),
           "{\"schema_version\":1,\"node_id\":\"%s\",\"firmware_version\":\"%s\","
           "\"interfaces\":[{\"interface_id\":\"IMU0\",\"type\":\"built_in\",\"capabilities\":[\"built_in_imu_accelerometer\",\"built_in_imu_gyroscope\"]},"
           "{\"interface_id\":\"VBAT\",\"type\":\"built_in\",\"capabilities\":[\"built_in_battery\"]},"
           "{\"interface_id\":\"D0\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D1\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D2\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D3\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D4\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]},"
           "{\"interface_id\":\"D5\",\"type\":\"analog\",\"capabilities\":[\"raw_adc\"]}],"
           "\"processing\":{\"filters\":[\"none\",\"ema\",\"moving_average\",\"median\",\"digital_debounce\"],"
           "\"reporting_modes\":[\"live\",\"edge_summary\",\"low_power\",\"event\",\"heartbeat\"]},"
           "\"configuration\":{\"persistence\":\"nvm3_redundant_crc32\",\"readback\":true},"
           "\"data_management\":{\"telemetry_version\":2,\"boot_id\":true,\"persistence_ack\":true,\"backlog_ack\":true}}",
           runtime_node_id(), FIRMWARE_VERSION);
  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_READ,
                                              0x00, 0x00, capabilities_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, sizeof(capabilities_json),
                                              1, &empty_value,
                                              &ble_capabilities_characteristic_handle);
  app_assert_status(sc);

  snprintf(metadata_json, sizeof(metadata_json),
           "{\"node_id\":\"%s\",\"sensor_package_version\":\"%s\",\"firmware_version\":\"%s\","
           "\"protocol_version\":\"%s\",\"configuration_schema_version\":%d,\"build_identifier\":\"%s\","
           "\"git_commit\":\"%s\"}", runtime_node_id(), SENSOR_PACKAGE_VERSION, FIRMWARE_VERSION,
           PROTOCOL_VERSION, CONFIGURATION_SCHEMA_VERSION, BUILD_IDENTIFIER, FIRMWARE_GIT_COMMIT);
  sc = sl_bt_gattdb_add_uuid128_characteristic(session_id, telemetry_service_handle,
                                              SL_BT_GATTDB_CHARACTERISTIC_READ,
                                              0x00, 0x00, metadata_characteristic_uuid,
                                              sl_bt_gattdb_variable_length_value, sizeof(metadata_json),
                                              1, &empty_value,
                                              &ble_metadata_characteristic_handle);
  app_assert_status(sc);

  sc = sl_bt_gattdb_start_service(session_id, telemetry_service_handle);
  app_assert_status(sc);
  sc = sl_bt_gattdb_commit(session_id);
  app_assert_status(sc);
  ble_write_attribute_chunks(ble_capabilities_characteristic_handle,
                             (const uint8_t*)capabilities_json, strlen(capabilities_json));
  ble_write_attribute_chunks(ble_metadata_characteristic_handle,
                             (const uint8_t*)metadata_json, strlen(metadata_json));
  ble_refresh_onboarding_identity();
  ble_database_initialized = true;
}

void ble_refresh_onboarding_identity() {
  if (!ble_onboarding_identity_characteristic_handle) return;
  if (!bootstrap_only) {
    snprintf(onboarding_identity_json, sizeof(onboarding_identity_json),
             "{\"schema_version\":1,\"provisioning_state\":\"provisioned\",\"protocol_version\":\"%s\"}",
             PROTOCOL_VERSION);
  } else {
    char canonical_hardware_id[19];
    uint64_t hardware_id = getDeviceUniqueId();
    snprintf(canonical_hardware_id, sizeof(canonical_hardware_id), "0x%08lX%08lX",
             (unsigned long)(hardware_id >> 32), (unsigned long)hardware_id);
    const char domain[] = "MG24-ONBOARDING-V1";
    char digest_input[sizeof(domain) - 1 + sizeof(canonical_hardware_id) - 1];
    memcpy(digest_input, domain, sizeof(domain) - 1);
    memcpy(digest_input + sizeof(domain) - 1, canonical_hardware_id, sizeof(canonical_hardware_id) - 1);
    unsigned char digest[32];
    sha256_compute((const uint8_t*)digest_input, sizeof(digest_input), digest);
    char encoded[33];
    for (size_t index = 0; index < 16; ++index) snprintf(encoded + index * 2, 3, "%02x", digest[index]);
    const char* onboarding_state = reset_recovery_status == StoreStatus::Ok ? "unprovisioned" : "recovery";
    snprintf(onboarding_identity_json, sizeof(onboarding_identity_json),
             "{\"schema_version\":1,\"onboarding_identity\":\"%s\",\"provisioning_state\":\"%s\","
             "\"protocol_version\":\"%s\",\"firmware_version\":\"%s\"}",
             encoded, onboarding_state, PROTOCOL_VERSION, FIRMWARE_VERSION);
  }
  ble_write_attribute_chunks(ble_onboarding_identity_characteristic_handle,
                             (const uint8_t*)onboarding_identity_json, strlen(onboarding_identity_json));
}

void ble_initialize_when_ready() {
  if (!application_setup_complete || !ble_system_booted || ble_database_initialized) return;
  ble_initialize_gatt_db();
  ble_start_advertising();
}

void ble_write_attribute_chunks(uint16_t characteristic, const uint8_t* value, size_t length) {
  constexpr size_t kGattWriteChunkSize = 200;
  for (size_t offset = 0; offset < length; offset += kGattWriteChunkSize) {
    const size_t chunk_length = min(kGattWriteChunkSize, length - offset);
    sl_status_t sc = sl_bt_gatt_server_write_attribute_value(
      characteristic, (uint16_t)offset, chunk_length, value + offset);
    app_assert_status(sc);
  }
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

void ble_update_telemetry(float batt, float ax, float ay, float az, float gx, float gy, float gz, uint32_t mic_raw, int mic_pct, const char* analog_json, uint32_t sample_count) {
  if (!ble_enabled || ble_telemetry_characteristic_handle == 0) {
    return;
  }

  int written = snprintf(ble_json, sizeof(ble_json),
           "{\"t\":\"tele\",\"v\":2,\"id\":\"%s\",\"bid\":\"%s\",\"s\":%lu,\"ms\":%lu,\"sc\":%lu,\"m\":%lu,\"mp\":%d,\"bv\":%.3f,\"l\":%d,\"io\":%d,"
           "\"a\":[%.3f,%.3f,%.3f],\"g\":[%.2f,%.2f,%.2f],\"n\":[%s]}",
           runtime_node_id(), telemetry_boot_id, (unsigned long)telemetry_sequence++, millis(),
           (unsigned long)max(1UL, sample_count), (unsigned long)mic_raw, mic_pct, batt, led_brightness, imu_ok ? 1 : 0,
           ax, ay, az, gx, gy, gz, analog_json);
  if (written <= 0 || (size_t)written >= sizeof(ble_json)) {
    processing_error_count++;
    return;
  }
  char current_telemetry[sizeof(ble_json)];
  strlcpy(current_telemetry, ble_json, sizeof(current_telemetry));

  TelemetryRecord record = {};
  record.type = RecordType::Measurement; record.priority = RecordPriority::Routine;
  record.sequence_number = telemetry_sequence - 1; record.uptime_ms = millis();
  strlcpy(record.payload, current_telemetry, sizeof(record.payload));
  offline_buffer.push(record);
  ble_send_oldest_buffered();
  microphone_channel.mark_reported(millis());
}

bool ble_send_oldest_buffered() {
  if (!ble_connected || !ble_notify_enabled) return false;
  TelemetryRecord record = {};
  return offline_buffer.peek_oldest(&record) && ble_send_payload(record.payload);
}

void generate_telemetry_boot_id() {
  uint8_t random_bytes[8] = {};
  size_t random_length = 0;
  sl_status_t status = sl_bt_system_get_random_data(sizeof(random_bytes), sizeof(random_bytes), &random_length, random_bytes);
  if (status != SL_STATUS_OK || random_length != sizeof(random_bytes)) {
    processing_error_count++;
    for (size_t i = 0; i < sizeof(random_bytes); ++i) random_bytes[i] = (uint8_t)(micros() >> ((i % 4) * 8));
  }
  for (size_t i = 0; i < sizeof(random_bytes); ++i) snprintf(telemetry_boot_id + i * 2, 3, "%02x", random_bytes[i]);
  telemetry_sequence = 1;
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

void ble_publish_vibration() {
  if (!ble_vibration_characteristic_handle) return;
  const ProductionVibrationResult& latest = vibration_service.latest();
  if (latest.validity != seed_mg24::VibrationResultValidity::VALID ||
      latest.window_sequence == 0 ||
      latest.window_sequence == last_vibration_summary_sequence) return;
  if (!seed_mg24::encodeVibrationSummary(
          latest.metrics, latest.window_sequence, latest.processed_uptime_ms,
          vibration_json, sizeof(vibration_json))) {
    processing_error_count++;
    return;
  }
  const size_t length = strlen(vibration_json);
  if (sl_bt_gatt_server_write_attribute_value(
          ble_vibration_characteristic_handle, 0, length,
          reinterpret_cast<const uint8_t*>(vibration_json)) != SL_STATUS_OK) {
    processing_error_count++;
    return;
  }
  last_vibration_summary_sequence = latest.window_sequence;
  if (ble_connected && ble_vibration_notify_enabled &&
      sl_bt_gatt_server_notify_all(
          ble_vibration_characteristic_handle, length,
          reinterpret_cast<const uint8_t*>(vibration_json)) != SL_STATUS_OK) {
    processing_error_count++;
  }
}
#endif

void loop() {
#if BLE_SUPPORTED
  ble_initialize_when_ready();
#endif
  if (low_power_exit_pending) {
    low_power_exit_pending = false;
    exit_low_power_mode();
    reporting_mode = EDGE_SUMMARY_MODE;
    last_edge_sample_ms = millis();
    last_edge_report_ms = millis();
  }
  const uint32_t initialization_now = millis();
  if (!imu_ok && imu_initialization_attempts < IMU_MAX_INITIALIZATION_ATTEMPTS &&
      elapsed_since(initialization_now, last_imu_initialization_ms, IMU_RETRY_INTERVAL_MS)) {
    initialize_imu();
  }
  if (!vibration_initialized && imu_ok &&
      vibration_initialization_attempts < IMU_MAX_INITIALIZATION_ATTEMPTS &&
      (vibration_initialization_attempts == 0 ||
       elapsed_since(initialization_now, last_vibration_initialization_ms, IMU_RETRY_INTERVAL_MS))
#if BLE_SUPPORTED
      && ble_database_initialized
#endif
  ) {
    vibration_initialization_attempts++;
    last_vibration_initialization_ms = initialization_now;
    const bool vibration_ok = vibration_service.begin();
    vibration_initialized = vibration_ok;
    Serial.print("{\"type\":\"boot\",\"step\":\"vibration\",\"ok\":");
    Serial.print(vibration_ok ? "true" : "false");
    Serial.print(",\"attempt\":"); Serial.print(vibration_initialization_attempts); Serial.println("}");
    if (!vibration_ok) sensor_error_count++;
  }
#if BLE_SUPPORTED
  if (ble_command_pending) {
    char command[sizeof(pending_ble_command)];
    noInterrupts();
    strncpy(command, pending_ble_command, sizeof(command) - 1);
    command[sizeof(command) - 1] = '\0';
    ble_command_pending = false;
    interrupts();
    handle_command(String(command));
  }
#endif
  static char serial_line[kBootstrapMaxLine + 1];
  static size_t serial_length = 0;
  static bool serial_overflow = false;
  while (Serial.available()) {
    char c = (char)Serial.read();
    // A disconnected host can leave a partial request buffered. A new framed
    // bootstrap request always starts with 'M', so resynchronize instead of
    // concatenating two transactions and misreading their schema fields.
    if (c == 'M' && serial_length > 0 && Serial.peek() == 'G') {
      serial_length = 0;
      serial_overflow = false;
    }
    if (c == '\r') continue;
    if (c == '\n') {
      if (!serial_overflow && serial_length) {
        serial_line[serial_length] = '\0';
        if (!usb_bootstrap.handle_line(serial_line, Serial, millis())) handle_command(String(serial_line));
        if (factory_reset_controller.reboot_required()) {
          memset(&active_identity, 0, sizeof(active_identity)); identity_store_status = StoreStatus::Unprovisioned;
          microphone_runtime_config = MICROPHONE_CHANNEL_CONFIG; microphone_channel.reconfigure(&microphone_runtime_config);
          // USB CDC needs a bounded drain interval so the host receives the
          // successful destructive-confirmation acknowledgement before reboot.
          offline_buffer.clear(); bootstrap_only = true; Serial.flush(); delay(250); systemReset();
        }
      } else if (serial_overflow) {
        Serial.println("MG24BOOT1 {\"type\":\"bootstrap_response\",\"schema_version\":1,\"request_id\":\"unknown\",\"action\":\"unknown\",\"status\":\"error\",\"error_code\":\"line_too_large\"}");
      }
      serial_length = 0; serial_overflow = false;
    } else if (serial_length < kBootstrapMaxLine) serial_line[serial_length++] = c;
    else serial_overflow = true;
  }

  if (factory_reset_controller.busy()) bootstrap_only = true;
  // Vibration failure is isolated: FIFO/DSP health never gates BLE, identity,
  // provisioning, or the existing telemetry path.
  if (!factory_reset_controller.busy() && reporting_mode != LOW_POWER_MODE) vibration_service.service();
#if BLE_SUPPORTED
  const uint32_t vibration_now = millis();
  if (reporting_mode != LOW_POWER_MODE &&
      (reporting_mode == LIVE_MODE || elapsed_since(vibration_now, last_edge_vibration_ms, EDGE_SUMMARY_INTERVAL_MS))) {
    last_edge_vibration_ms = vibration_now;
    ble_publish_vibration();
  }
#endif
  if (bootstrap_only) { delay(5); return; }
  update_microphone();

  const uint32_t now = millis();
  if (reporting_mode == LIVE_MODE &&
      elapsed_since(now, last_sample_ms, microphone_runtime_config.report_interval_ms)) {
    last_sample_ms = now;
    print_telemetry();
  } else if (reporting_mode == EDGE_SUMMARY_MODE) {
    if (elapsed_since(now, last_edge_sample_ms, microphone_runtime_config.sample_interval_ms)) {
      last_edge_sample_ms = now;
      capture_edge_sample();
    }
    if (elapsed_since(now, last_edge_report_ms, EDGE_SUMMARY_INTERVAL_MS)) {
      last_edge_report_ms = now;
      publish_edge_summary();
    }
  } else if (reporting_mode == LOW_POWER_MODE &&
      elapsed_since(now, last_low_power_report_ms, LOW_POWER_REPORT_INTERVAL_MS)) {
    last_low_power_report_ms = now;
    publish_low_power_snapshot();
  }
#if BLE_SUPPORTED
  const uint32_t heartbeat_interval = reporting_mode == LOW_POWER_MODE
      ? LOW_POWER_REPORT_INTERVAL_MS
      : reporting_mode == EDGE_SUMMARY_MODE
      ? max((uint32_t)EDGE_HEARTBEAT_INTERVAL_MS, microphone_runtime_config.heartbeat_interval_ms)
      : microphone_runtime_config.heartbeat_interval_ms;
  if (ble_connected && elapsed_since(now, last_heartbeat_ms, heartbeat_interval)) {
    last_heartbeat_ms = now;
    if (encode_heartbeat(telemetry_sequence++, now, battery_voltage(), offline_buffer.size(),
                         offline_buffer.dropped_count(), processing_error_count, sensor_error_count,
                         runtime_node_id(), telemetry_boot_id, ble_json, sizeof(ble_json))) {
      TelemetryRecord heartbeat = {};
      heartbeat.type = RecordType::Heartbeat; heartbeat.priority = RecordPriority::Heartbeat;
      heartbeat.sequence_number = telemetry_sequence - 1; heartbeat.uptime_ms = now;
      strlcpy(heartbeat.payload, ble_json, sizeof(heartbeat.payload));
      offline_buffer.push(heartbeat);
      ble_send_oldest_buffered();
    } else {
      processing_error_count++;
    }
  }
#endif
  if (reporting_mode == LOW_POWER_MODE) LowPower.sleep(LOW_POWER_SLEEP_SLICE_MS);
}
