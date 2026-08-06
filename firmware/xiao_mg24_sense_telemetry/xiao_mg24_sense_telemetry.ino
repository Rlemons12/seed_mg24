#include <Arduino.h>
#include <Wire.h>
#include <LSM6DS3.h>
#include <SilabsMicrophoneAnalog.h>
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
#endif
char telemetry_json[512];
char ble_json[244];

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

void ble_initialize_gatt_db();
void ble_start_advertising();
void ble_update_telemetry(float batt, float ax, float ay, float az, float gx, float gy, float gz, int mic_pct, const char* analog_json);
#endif

void handle_command(String command) {
  command.trim();
  command.toUpperCase();

  if (command == "LED ON") {
    led_brightness = 255;
    update_led();
  } else if (command == "LED OFF") {
    led_brightness = 0;
    update_led();
  } else if (command.startsWith("LED ")) {
    led_brightness = constrain(command.substring(4).toInt(), 0, 255);
    update_led();
  } else if (command.startsWith("RATE ")) {
    sample_interval_ms = constrain(command.substring(5).toInt(), 50, 5000);
  } else if (command == "BLE START") {
    ble_enabled = true;
#if BLE_SUPPORTED
    ble_start_advertising();
#endif
  } else if (command == "BLE STOP") {
    ble_enabled = false;
  } else if (command == "PING") {
    Serial.println("{\"type\":\"pong\"}");
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
           "{\"t\":\"tele\",\"ms\":%lu,\"m\":%lu,\"mp\":%d,\"bv\":%.3f,\"l\":%d,"
           "\"bs\":1,\"be\":%d,\"bc\":%d,\"imu\":%d,\"mk\":%d,"
           "\"a\":[%.3f,%.3f,%.3f],\"g\":[%.2f,%.2f,%.2f],\"n\":[%s]}",
           millis(), mic_level, mic_pct, batt, led_brightness,
           ble_enabled ? 1 : 0, ble_connected ? 1 : 0, imu_ok ? 1 : 0, mic_ok ? 1 : 0,
           ax, ay, az, gx, gy, gz, analog_json);

  size_t len = strlen(ble_json);
  sl_bt_gatt_server_write_attribute_value(ble_telemetry_characteristic_handle, 0, len, (const uint8_t*)ble_json);
  if (ble_connected && ble_notify_enabled) {
    sl_bt_gatt_server_notify_all(ble_telemetry_characteristic_handle, len, (const uint8_t*)ble_json);
  }
}
#endif

void loop() {
  while (Serial.available()) {
    handle_command(Serial.readStringUntil('\n'));
  }

  update_microphone();

  const uint32_t now = millis();
  if (now - last_sample_ms >= sample_interval_ms) {
    last_sample_ms = now;
    print_telemetry();
  }
}
