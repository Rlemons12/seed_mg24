#ifndef SEED_MG24_VIBRATION_FEATURES_H_
#define SEED_MG24_VIBRATION_FEATURES_H_

#include "vibration_types.h"

namespace seed_mg24 {

AxisVibrationMetrics calculateAxisMetrics(
    const float* samples, size_t count, size_t stride = 1,
    ProcessingYieldHook hook = 0, void* hook_context = 0);

}  // namespace seed_mg24

#endif
