#include <Arduino.h>
#include <SilabsMicrophoneAnalog.h>

#define MIC_DATA_PIN PC9
#define MIC_PWR_PIN PC8
#define MIC_SAMPLES 128

MicrophoneAnalog diagnosticMic(MIC_DATA_PIN, MIC_PWR_PIN);
uint32_t micBuffer[MIC_SAMPLES];
uint32_t micBufferLocal[MIC_SAMPLES];
volatile bool micReady = false;
volatile uint32_t callbackCount = 0;

void samplesReady() {
  memcpy(micBufferLocal, micBuffer, sizeof(micBuffer));
  micReady = true;
  callbackCount++;
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.println("{\"type\":\"microphone\",\"stage\":\"initialization_started\"}");
  diagnosticMic.begin(micBuffer, MIC_SAMPLES);
  Serial.println("{\"type\":\"microphone\",\"stage\":\"begin_complete\"}");
  diagnosticMic.startSampling(samplesReady);
  Serial.println("{\"type\":\"microphone\",\"stage\":\"sampling_started\"}");
}

void loop() {
  static uint32_t lastCount = 0;
  digitalWrite(LED_BUILTIN, LED_BUILTIN_ACTIVE);
  uint32_t count = callbackCount;
  Serial.print("{\"type\":\"microphone_sample\",\"callbacks\":");
  Serial.print(count);
  Serial.print(",\"new_data\":");
  Serial.print(micReady ? "true" : "false");
  if (micReady) {
    micReady = false;
    Serial.print(",\"first_raw\":");
    Serial.print(micBufferLocal[0]);
  }
  Serial.print(",\"advanced\":");
  Serial.print(count != lastCount ? "true" : "false");
  Serial.println("}");
  lastCount = count;
  delay(500);
  digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
  Serial.println("{\"type\":\"heartbeat\",\"led\":\"off\"}");
  delay(500);
}
