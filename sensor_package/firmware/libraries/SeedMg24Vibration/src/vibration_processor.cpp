#include "vibration_processor.h"

#include <stddef.h>

#include "vibration_features.h"

namespace seed_mg24 {
namespace {

float readMember(const ImuEngineeringSample& sample, size_t offset) {
  const uint8_t* bytes = reinterpret_cast<const uint8_t*>(&sample);
  return *reinterpret_cast<const float*>(bytes + offset);
}

}  // namespace

VibrationProcessor::VibrationProcessor(const VibrationProcessorConfig& config,
                                       VibrationClockMicros clock)
    : config_(config), clock_(clock), valid_(true), filters_initialized_(false) {
  if (!(config_.sample_rate_hz > 0.0f) ||
      !(config_.accel_g_per_count > 0.0f) ||
      !(config_.gyro_dps_per_count > 0.0f)) valid_ = false;
  for (size_t axis = 0; axis < 6; ++axis) {
    if (!filters_[axis].configure(config_.sample_rate_hz,
                                  config_.high_pass_cutoff_hz)) valid_ = false;
  }
}

void VibrationProcessor::condition(const ImuRawSample* samples,
                                   ProcessingYieldHook hook,
                                   void* hook_context,
                                   float effective_sample_rate_hz) {
  for (size_t axis = 0; axis < 6; ++axis) {
    if (!filters_[axis].configure(effective_sample_rate_hz,
                                  config_.high_pass_cutoff_hz)) return;
  }
  if (!filters_initialized_) {
    const float initial[6] = {
        samples[0].accel_x * config_.accel_g_per_count,
        samples[0].accel_y * config_.accel_g_per_count,
        samples[0].accel_z * config_.accel_g_per_count,
        samples[0].gyro_x * config_.gyro_dps_per_count,
        samples[0].gyro_y * config_.gyro_dps_per_count,
        samples[0].gyro_z * config_.gyro_dps_per_count,
    };
    for (size_t axis = 0; axis < 6; ++axis) filters_[axis].reset(initial[axis]);
    filters_initialized_ = true;
  }
  for (size_t index = 0; index < kVibrationWindowSamples; ++index) {
    const float values[6] = {
        samples[index].accel_x * config_.accel_g_per_count,
        samples[index].accel_y * config_.accel_g_per_count,
        samples[index].accel_z * config_.accel_g_per_count,
        samples[index].gyro_x * config_.gyro_dps_per_count,
        samples[index].gyro_y * config_.gyro_dps_per_count,
        samples[index].gyro_z * config_.gyro_dps_per_count,
    };
    conditioned_[index].accel_x_g = filters_[0].apply(values[0]);
    conditioned_[index].accel_y_g = filters_[1].apply(values[1]);
    conditioned_[index].accel_z_g = filters_[2].apply(values[2]);
    conditioned_[index].gyro_x_dps = filters_[3].apply(values[3]);
    conditioned_[index].gyro_y_dps = filters_[4].apply(values[4]);
    conditioned_[index].gyro_z_dps = filters_[5].apply(values[5]);
    if (hook != 0 && (index & 0x07u) == 0x07u) hook(hook_context);
  }
}

FrequencyMetrics VibrationProcessor::analyzeAxis(
    size_t member_offset, float sample_rate_hz, ProcessingYieldHook hook,
    void* hook_context) {
  for (size_t index = 0; index < kVibrationWindowSamples; ++index) {
    fft_input_[index] = readMember(conditioned_[index], member_offset);
    if (hook != 0 && (index & 0x0Fu) == 0x0Fu) hook(hook_context);
  }
  return fft_.analyze(fft_input_, sample_rate_hz,
                      config_.minimum_dominant_frequency_hz, hook, hook_context);
}

bool VibrationProcessor::process(const ImuRawSample* samples, size_t count,
                                 VibrationWindowResult* result,
                                 float effective_sample_rate_hz,
                                 ProcessingYieldHook hook,
                                 void* hook_context) {
  if (!valid_ || samples == 0 || result == 0 ||
      count != kVibrationWindowSamples) return false;
  *result = VibrationWindowResult{};
  if (!(effective_sample_rate_hz > 0.0f)) {
    effective_sample_rate_hz = config_.sample_rate_hz;
  }
  const uint32_t total_start = clock_ != 0 ? clock_() : 0;
  const uint32_t feature_start = total_start;
  condition(samples, hook, hook_context, effective_sample_rate_hz);
  const size_t stride = sizeof(ImuEngineeringSample) / sizeof(float);
  result->accel_x = calculateAxisMetrics(&conditioned_[0].accel_x_g, count, stride, hook, hook_context);
  result->accel_y = calculateAxisMetrics(&conditioned_[0].accel_y_g, count, stride, hook, hook_context);
  result->accel_z = calculateAxisMetrics(&conditioned_[0].accel_z_g, count, stride, hook, hook_context);
  result->gyro_x = calculateAxisMetrics(&conditioned_[0].gyro_x_dps, count, stride, hook, hook_context);
  result->gyro_y = calculateAxisMetrics(&conditioned_[0].gyro_y_dps, count, stride, hook, hook_context);
  result->gyro_z = calculateAxisMetrics(&conditioned_[0].gyro_z_dps, count, stride, hook, hook_context);
  const uint32_t fft_start = clock_ != 0 ? clock_() : 0;
  result->feature_processing_us = clock_ != 0 ? fft_start - feature_start : 0;
  result->accel_x_frequency = analyzeAxis(offsetof(ImuEngineeringSample, accel_x_g), effective_sample_rate_hz, hook, hook_context);
  result->accel_y_frequency = analyzeAxis(offsetof(ImuEngineeringSample, accel_y_g), effective_sample_rate_hz, hook, hook_context);
  result->accel_z_frequency = analyzeAxis(offsetof(ImuEngineeringSample, accel_z_g), effective_sample_rate_hz, hook, hook_context);
  const uint32_t end = clock_ != 0 ? clock_() : 0;
  result->fft_processing_us = clock_ != 0 ? end - fft_start : 0;
  result->total_processing_us = clock_ != 0 ? end - total_start : 0;
  result->sample_count = static_cast<uint16_t>(count);
  result->configured_sample_rate_hz = config_.sample_rate_hz;
  result->effective_sample_rate_hz = effective_sample_rate_hz;
  result->high_pass_cutoff_hz = config_.high_pass_cutoff_hz;
  return true;
}

}  // namespace seed_mg24
