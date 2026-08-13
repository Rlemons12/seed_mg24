#ifndef SEED_MG24_VIBRATION_SUMMARY_H_
#define SEED_MG24_VIBRATION_SUMMARY_H_

#include <stddef.h>
#include <stdint.h>

#include "vibration_types.h"

namespace seed_mg24 {

constexpr uint8_t kVibrationSummarySchemaVersion = 1;
constexpr uint8_t kVibrationAlgorithmVersion = 1;
constexpr size_t kVibrationSummaryMaximumBytes = 244;

bool encodeVibrationSummary(const VibrationWindowResult& result,
                            uint32_t window_sequence, uint32_t uptime_ms,
                            char* output, size_t output_size);

}  // namespace seed_mg24

#endif
