#include <Arduino.h>
#include <Wire.h>
#include <LSM6DS3.h>

#define IMU_POWER_PIN PD5

LSM6DS3 diagnosticImu(I2C_MODE, 0x6A);
int imuStatus = -1;

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(IMU_POWER_PIN, OUTPUT);
  digitalWrite(IMU_POWER_PIN, HIGH);
  Serial.println("{\"type\":\"imu\",\"stage\":\"power_enabled\"}");
  delay(300);
  Serial.println("{\"type\":\"imu\",\"stage\":\"initialization_started\"}");
  imuStatus = diagnosticImu.begin();
  Serial.print("{\"type\":\"imu\",\"stage\":\"initialization_complete\",\"status\":");
  Serial.print(imuStatus);
  Serial.println("}");
}

void loop() {
  digitalWrite(LED_BUILTIN, LED_BUILTIN_ACTIVE);
  Serial.print("{\"type\":\"imu_sample\",\"status\":");
  Serial.print(imuStatus);
  if (imuStatus == 0) {
    Serial.print(",\"ax\":"); Serial.print(diagnosticImu.readFloatAccelX(), 4);
    Serial.print(",\"ay\":"); Serial.print(diagnosticImu.readFloatAccelY(), 4);
    Serial.print(",\"az\":"); Serial.print(diagnosticImu.readFloatAccelZ(), 4);
    Serial.print(",\"gx\":"); Serial.print(diagnosticImu.readFloatGyroX(), 3);
  }
  Serial.println("}");
  delay(500);
  digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
  Serial.println("{\"type\":\"heartbeat\",\"led\":\"off\"}");
  delay(500);
}
