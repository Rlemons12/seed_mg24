#ifndef SEED_MG24_VIBRATION_TYPES_H_
#define SEED_MG24_VIBRATION_TYPES_H_

#include <stddef.h>
#include <stdint.h>

namespace seed_mg24 {

constexpr size_t kVibrationWindowSamples = 256;
typedef void (*ProcessingYieldHook)(void* context);

struct ImuRawSample {
  int16_t gyro_x;
  int16_t gyro_y;
  int16_t gyro_z;
  int16_t accel_x;
  int16_t accel_y;
  int16_t accel_z;
};

struct ImuEngineeringSample {
  float accel_x_g;
  float accel_y_g;
  float accel_z_g;
  float gyro_x_dps;
  float gyro_y_dps;
  float gyro_z_dps;
};

struct AxisVibrationMetrics {
  float mean;
  float rms;
  float peak_abs;
  float peak_to_peak;
  float standard_deviation;
  float crest_factor;
  float kurtosis;
};

struct FrequencyMetrics {
  float dominant_frequency_hz;
  float dominant_amplitude;
  uint16_t dominant_bin;
};

struct VibrationWindowResult {
  uint16_t sample_count;
  float configured_sample_rate_hz;
  float effective_sample_rate_hz;
  float high_pass_cutoff_hz;
  uint32_t feature_processing_us;
  uint32_t fft_processing_us;
  uint32_t total_processing_us;
  AxisVibrationMetrics accel_x;
  AxisVibrationMetrics accel_y;
  AxisVibrationMetrics accel_z;
  AxisVibrationMetrics gyro_x;
  AxisVibrationMetrics gyro_y;
  AxisVibrationMetrics gyro_z;
  FrequencyMetrics accel_x_frequency;
  FrequencyMetrics accel_y_frequency;
  FrequencyMetrics accel_z_frequency;
};

}  // namespace seed_mg24

#endif
