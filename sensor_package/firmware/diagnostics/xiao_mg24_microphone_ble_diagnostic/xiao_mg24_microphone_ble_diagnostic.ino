#include <Arduino.h>
#include <SilabsMicrophoneAnalog.h>

#define MIC_DATA_PIN PC9
#define MIC_PWR_PIN PC8
#define MIC_SAMPLES 128

MicrophoneAnalog diagnosticMic(MIC_DATA_PIN, MIC_PWR_PIN);
uint32_t micBuffer[MIC_SAMPLES];
uint32_t micBufferLocal[MIC_SAMPLES];
volatile uint32_t callbackCount = 0;
volatile bool bleBootSeen = false;
volatile bool advertisingStarted = false;
volatile sl_status_t bleStatus = SL_STATUS_OK;
uint8_t advertisingHandle = 0xff;

void samplesReady() {
  memcpy(micBufferLocal, micBuffer, sizeof(micBuffer));
  callbackCount++;
}

void sl_bt_on_event(sl_bt_msg_t *event) {
  if (SL_BT_MSG_ID(event->header) != sl_bt_evt_system_boot_id) return;
  bleBootSeen = true;
  bleStatus = sl_bt_advertiser_create_set(&advertisingHandle);
  if (bleStatus != SL_STATUS_OK) return;
  bleStatus = sl_bt_advertiser_set_timing(advertisingHandle, 160, 160, 0, 0);
  if (bleStatus != SL_STATUS_OK) return;
  bleStatus = sl_bt_legacy_advertiser_generate_data(advertisingHandle, sl_bt_advertiser_general_discoverable);
  if (bleStatus != SL_STATUS_OK) return;
  bleStatus = sl_bt_legacy_advertiser_start(advertisingHandle, sl_bt_advertiser_connectable_scannable);
  if (bleStatus == SL_STATUS_OK) advertisingStarted = true;
}

void setup() {
  Serial.begin(115200); pinMode(LED_BUILTIN, OUTPUT);
  diagnosticMic.begin(micBuffer, MIC_SAMPLES);
  diagnosticMic.startSampling(samplesReady);
}

void loop() {
  digitalWrite(LED_BUILTIN, LED_BUILTIN_ACTIVE);
  Serial.print("{\"type\":\"combined\",\"callbacks\":"); Serial.print(callbackCount);
  Serial.print(",\"ble_boot\":"); Serial.print(bleBootSeen ? "true" : "false");
  Serial.print(",\"advertising\":"); Serial.print(advertisingStarted ? "true" : "false");
  Serial.print(",\"status\":"); Serial.print(static_cast<unsigned long>(bleStatus)); Serial.println("}");
  delay(500); digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
  Serial.println("{\"type\":\"heartbeat\",\"led\":\"off\"}"); delay(500);
}
