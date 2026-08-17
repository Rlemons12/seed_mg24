#ifndef SEED_MG24_VIBRATION_FFT_H_
#define SEED_MG24_VIBRATION_FFT_H_

#include "vibration_types.h"

namespace seed_mg24 {

class VibrationFft256 {
 public:
  FrequencyMetrics analyze(const float* samples, float sample_rate_hz,
                           float minimum_frequency_hz,
                           ProcessingYieldHook hook = 0,
                           void* hook_context = 0);
  const float* amplitudes() const { return amplitudes_; }
  size_t amplitudeCount() const { return kVibrationWindowSamples / 2; }

 private:
  float real_[kVibrationWindowSamples];
  float imaginary_[kVibrationWindowSamples];
  float amplitudes_[kVibrationWindowSamples / 2];
};

}  // namespace seed_mg24

#endif
