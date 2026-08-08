#include <Arduino.h>

namespace {

constexpr uint8_t kInvalidAdvertisingHandle = 0xff;
constexpr uint8_t kAdvertisedName[] = "XIAO-MG24-DIAG";

volatile bool bleBootSeen = false;
volatile bool advertisingStarted = false;
volatile bool bleErrorPending = false;
volatile sl_status_t bleErrorStatus = SL_STATUS_OK;
const char *volatile bleErrorOperation = nullptr;

uint8_t advertisingSetHandle = kInvalidAdvertisingHandle;
uint16_t gattSessionId = 0;
uint16_t serviceHandle = 0;
uint16_t characteristicHandle = 0;

void printBoth(const char *message) {
  Serial.println(message);
  Serial1.println(message);
}

void reportBleError(const char *operation, sl_status_t status) {
  bleErrorOperation = operation;
  bleErrorStatus = status;
  bleErrorPending = true;
}

void printBleError(const char *operation, sl_status_t status) {
  Serial.print("{\"type\":\"ble_error\",\"operation\":\"");
  Serial.print(operation);
  Serial.print("\",\"status\":");
  Serial.print(static_cast<unsigned long>(status));
  Serial.println("}");

  Serial1.print("{\"type\":\"ble_error\",\"operation\":\"");
  Serial1.print(operation);
  Serial1.print("\",\"status\":");
  Serial1.print(static_cast<unsigned long>(status));
  Serial1.println("}");
}

bool initializeDeviceName() {
  sl_status_t status = sl_bt_gattdb_new_session(&gattSessionId);
  if (status != SL_STATUS_OK) {
    reportBleError("gattdb_new_session", status);
    return false;
  }

  const uint8_t genericAccessServiceUuid[] = {0x00, 0x18};
  status = sl_bt_gattdb_add_service(gattSessionId,
                                    sl_bt_gattdb_primary_service,
                                    SL_BT_GATTDB_ADVERTISED_SERVICE,
                                    sizeof(genericAccessServiceUuid),
                                    genericAccessServiceUuid,
                                    &serviceHandle);
  if (status != SL_STATUS_OK) {
    reportBleError("gattdb_add_service", status);
    return false;
  }

  const sl_bt_uuid_16_t deviceNameUuid = {.data = {0x00, 0x2a}};
  status = sl_bt_gattdb_add_uuid16_characteristic(gattSessionId,
                                                   serviceHandle,
                                                   SL_BT_GATTDB_CHARACTERISTIC_READ,
                                                   0,
                                                   0,
                                                   deviceNameUuid,
                                                   sl_bt_gattdb_fixed_length_value,
                                                   sizeof(kAdvertisedName) - 1,
                                                   sizeof(kAdvertisedName) - 1,
                                                   kAdvertisedName,
                                                   &characteristicHandle);
  if (status != SL_STATUS_OK) {
    reportBleError("gattdb_add_device_name", status);
    return false;
  }

  status = sl_bt_gattdb_start_service(gattSessionId, serviceHandle);
  if (status != SL_STATUS_OK) {
    reportBleError("gattdb_start_service", status);
    return false;
  }

  status = sl_bt_gattdb_commit(gattSessionId);
  if (status != SL_STATUS_OK) {
    reportBleError("gattdb_commit", status);
    return false;
  }
  return true;
}

bool startAdvertising() {
  sl_status_t status = sl_bt_advertiser_create_set(&advertisingSetHandle);
  if (status != SL_STATUS_OK) {
    reportBleError("advertiser_create_set", status);
    return false;
  }

  status = sl_bt_advertiser_set_timing(advertisingSetHandle, 160, 160, 0, 0);
  if (status != SL_STATUS_OK) {
    reportBleError("advertiser_set_timing", status);
    return false;
  }

  status = sl_bt_legacy_advertiser_generate_data(advertisingSetHandle,
                                                  sl_bt_advertiser_general_discoverable);
  if (status != SL_STATUS_OK) {
    reportBleError("legacy_advertiser_generate_data", status);
    return false;
  }

  status = sl_bt_legacy_advertiser_start(advertisingSetHandle,
                                          sl_bt_advertiser_connectable_scannable);
  if (status != SL_STATUS_OK) {
    reportBleError("legacy_advertiser_start", status);
    return false;
  }

  advertisingStarted = true;
  return true;
}

void emitDeferredBleStatus() {
  static bool bootReported = false;
  static bool advertisingReported = false;
  static bool errorReported = false;

  if (bleBootSeen && !bootReported) {
    printBoth("{\"type\":\"ble\",\"event\":\"system_boot\"}");
    bootReported = true;
  }
  if (advertisingStarted && !advertisingReported) {
    Serial.print("{\"type\":\"ble\",\"event\":\"advertising_started\",\"handle\":");
    Serial.print(static_cast<unsigned int>(advertisingSetHandle));
    Serial.println("}");
    Serial1.print("{\"type\":\"ble\",\"event\":\"advertising_started\",\"handle\":");
    Serial1.print(static_cast<unsigned int>(advertisingSetHandle));
    Serial1.println("}");
    advertisingReported = true;
  }
  if (bleErrorPending && !errorReported) {
    printBleError(bleErrorOperation, bleErrorStatus);
    errorReported = true;
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200);
  printBoth("{\"type\":\"boot\",\"stage\":\"setup_entered\"}");
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
  printBoth("{\"type\":\"boot\",\"stage\":\"setup_complete\"}");
}

void loop() {
  emitDeferredBleStatus();

  digitalWrite(LED_BUILTIN, LED_BUILTIN_ACTIVE);
  printBoth("{\"type\":\"heartbeat\",\"led\":\"on\"}");
  delay(500);

  emitDeferredBleStatus();
  digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
  printBoth("{\"type\":\"heartbeat\",\"led\":\"off\"}");
  delay(500);
}

void sl_bt_on_event(sl_bt_msg_t *event) {
  if (SL_BT_MSG_ID(event->header) != sl_bt_evt_system_boot_id) {
    return;
  }

  bleBootSeen = true;
  if (!initializeDeviceName()) {
    return;
  }
  startAdvertising();
}

#ifndef ARDUINO_SILABS_STACK_BLE_SILABS
#error "This diagnostic requires protocol_stack=ble_silabs"
#endif
