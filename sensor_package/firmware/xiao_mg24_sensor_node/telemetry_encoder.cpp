#include "telemetry_encoder.h"

#include <stdio.h>

bool encode_record(const TelemetryRecord& r, const char* id, char* out, size_t size) {
  const char* type = r.type == RecordType::AlarmTransition || r.type == RecordType::SensorFaultTransition || r.type == RecordType::Recovery ? "e" : "m";
  const char* quality = r.quality == MeasurementQuality::Uncalibrated ? "uncalibrated" : r.quality == MeasurementQuality::Invalid ? "invalid" : r.quality == MeasurementQuality::SensorFault ? "sensor_fault" : "good";
  int written;
  if (r.has_normalized_value)
    written = snprintf(out, size, "{\"t\":\"%s\",\"v\":1,\"id\":\"%s\",\"s\":%lu,\"ms\":%lu,\"c\":\"%s\",\"rv\":%.3f,\"nv\":%.3f,\"u\":\"adc_count\",\"q\":\"%s\",\"d\":%d}", type, id, (unsigned long)r.sequence_number, (unsigned long)r.uptime_ms, r.channel_id, r.raw_value, r.normalized_value, quality, r.delayed ? 1 : 0);
  else
    written = snprintf(out, size, "{\"t\":\"%s\",\"v\":1,\"id\":\"%s\",\"s\":%lu,\"ms\":%lu,\"c\":\"%s\",\"rv\":%.3f,\"nv\":null,\"u\":\"adc_count\",\"q\":\"%s\",\"d\":%d}", type, id, (unsigned long)r.sequence_number, (unsigned long)r.uptime_ms, r.channel_id, r.raw_value, quality, r.delayed ? 1 : 0);
  return written > 0 && static_cast<size_t>(written) < size;
}
bool encode_heartbeat(uint32_t seq, uint32_t ms, float bv, uint8_t bu, uint32_t dr, uint32_t pe, uint32_t se, const char* id, const char* boot_id, char* out, size_t size) {
  int written = snprintf(out, size, "{\"t\":\"h\",\"v\":2,\"id\":\"%s\",\"bid\":\"%s\",\"s\":%lu,\"ms\":%lu,\"bv\":%.3f,\"sh\":1,\"bu\":%u,\"dr\":%lu,\"pe\":%lu,\"se\":%lu}", id, boot_id, (unsigned long)seq, (unsigned long)ms, bv, bu, (unsigned long)dr, (unsigned long)pe, (unsigned long)se);
  return written > 0 && static_cast<size_t>(written) < size;
}
