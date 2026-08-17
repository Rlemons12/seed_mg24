#ifndef SEED_MG24_VIBRATION_PROCESSOR_H_
#define SEED_MG24_VIBRATION_PROCESSOR_H_

#include "vibration_fft.h"
#include "vibration_filter.h"
#include "vibration_types.h"

namespace seed_mg24 {

struct VibrationProcessorConfig {
  float sample_rate_hz;
  float high_pass_cutoff_hz;
  float minimum_dominant_frequency_hz;
  float accel_g_per_count;
  float gyro_dps_per_count;
};

typedef uint32_t (*VibrationClockMicros)();

class VibrationProcessor {
 public:
  explicit VibrationProcessor(const VibrationProcessorConfig& config,
                              VibrationClockMicros clock = 0);
  bool valid() const { return valid_; }
  void resetFilterState() { filters_initialized_ = false; }
  bool process(const ImuRawSample* samples, size_t count,
               VibrationWindowResult* result,
               float effective_sample_rate_hz = 0.0f,
               ProcessingYieldHook hook = 0,
               void* hook_context = 0);
  const float* lastSpectrum() const { return fft_.amplitudes(); }
  size_t spectrumBinCount() const { return fft_.amplitudeCount(); }

 private:
  void condition(const ImuRawSample* samples, ProcessingYieldHook hook,
                 void* hook_context, float effective_sample_rate_hz);
  FrequencyMetrics analyzeAxis(size_t member_offset, float sample_rate_hz,
                               ProcessingYieldHook hook, void* hook_context);

  VibrationProcessorConfig config_;
  VibrationClockMicros clock_;
  bool valid_;
  bool filters_initialized_;
  ImuEngineeringSample conditioned_[kVibrationWindowSamples];
  FirstOrderHighPass filters_[6];
  float fft_input_[kVibrationWindowSamples];
  VibrationFft256 fft_;
};

}  // namespace seed_mg24

#endif
