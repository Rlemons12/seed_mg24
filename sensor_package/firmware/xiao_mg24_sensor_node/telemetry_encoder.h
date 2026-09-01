#pragma once

#include <stddef.h>
#include "sensor_types.h"

bool encode_record(const TelemetryRecord& record, const char* device_id, char* output, size_t output_size);
bool encode_heartbeat(uint32_t sequence, uint32_t uptime, float battery_voltage, uint8_t buffer_count,
                      uint32_t dropped, uint32_t processing_errors, uint32_t sensor_errors,
                      const char* device_id, const char* boot_id, const char* runtime_mode,
                      char* output, size_t output_size);
