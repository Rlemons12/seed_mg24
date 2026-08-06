#include <Arduino.h>

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_BUILTIN, LED_BUILTIN_ACTIVE);
  Serial.println("{\"type\":\"diagnostic\",\"led\":\"on\"}");
  Serial1.println("{\"type\":\"diagnostic\",\"port\":\"Serial1\",\"led\":\"on\"}");
  delay(500);
  digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
  Serial.println("{\"type\":\"diagnostic\",\"led\":\"off\"}");
  Serial1.println("{\"type\":\"diagnostic\",\"port\":\"Serial1\",\"led\":\"off\"}");
  delay(500);
}
