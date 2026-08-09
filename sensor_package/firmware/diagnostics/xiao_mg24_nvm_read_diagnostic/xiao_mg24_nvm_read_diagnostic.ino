#include <Arduino.h>

#include <nvm_backend.cpp>
#include <persistent_record.cpp>
#include <node_identity_store.cpp>
#include <configuration_store.cpp>

#ifndef ARDUINO_SILABS_STACK_BLE_SILABS
#error "This diagnostic requires protocol_stack=ble_silabs"
#endif

namespace {
SiliconLabsNvm3Backend backend;
NodeIdentityStore identityStore(backend);
PersistentConfigurationStore configurationStore(backend);
StoreStatus backendStatus = StoreStatus::StorageUnavailable;
StoreStatus identityStatus = StoreStatus::StorageUnavailable;
StoreStatus configurationStatus = StoreStatus::StorageUnavailable;

void printStatuses() {
  Serial.print("{\"type\":\"nvm\",\"backend_status\":");
  Serial.print(static_cast<unsigned int>(backendStatus));
  Serial.print(",\"identity_status\":");
  Serial.print(static_cast<unsigned int>(identityStatus));
  Serial.print(",\"configuration_status\":");
  Serial.print(static_cast<unsigned int>(configurationStatus));
  Serial.println("}");
}
}  // namespace

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.println("{\"type\":\"boot\",\"stage\":\"setup_entered\"}");
  backendStatus = backend.initialize();
  Serial.println("{\"type\":\"nvm\",\"stage\":\"backend_initialized\"}");
  if (backendStatus == StoreStatus::Ok) {
    NodeIdentity identity = {};
    StoredChannelConfiguration configuration = {};
    identityStatus = identityStore.load(&identity);
    Serial.println("{\"type\":\"nvm\",\"stage\":\"identity_read_complete\"}");
    configurationStatus = configurationStore.load(&configuration);
    Serial.println("{\"type\":\"nvm\",\"stage\":\"configuration_read_complete\"}");
  }
  printStatuses();
  Serial.println("{\"type\":\"boot\",\"stage\":\"setup_complete\"}");
}

void loop() {
  static uint32_t lastStatus = 0;
  digitalWrite(LED_BUILTIN, LED_BUILTIN_ACTIVE);
  Serial.println("{\"type\":\"heartbeat\",\"led\":\"on\"}");
  delay(500);
  digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
  Serial.println("{\"type\":\"heartbeat\",\"led\":\"off\"}");
  delay(500);
  if (millis() - lastStatus >= 5000) {
    lastStatus = millis();
    printStatuses();
  }
}
