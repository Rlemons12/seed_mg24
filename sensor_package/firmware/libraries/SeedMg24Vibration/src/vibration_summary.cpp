#include "vibration_summary.h"

#include <math.h>
#include <stdio.h>

namespace seed_mg24 {
namespace {

bool metricsAreBounded(const VibrationWindowResult& value) {
  const AxisVibrationMetrics accel_axes[] = {
      value.accel_x, value.accel_y, value.accel_z};
  for (const AxisVibrationMetrics& axis : accel_axes) {
    if (!isfinite(axis.rms) || axis.rms < 0.0f || axis.rms > 16.0f ||
        !isfinite(axis.peak_abs) || axis.peak_abs < 0.0f ||
        axis.peak_abs > 16.0f || !isfinite(axis.crest_factor) ||
        axis.crest_factor < 0.0f || axis.crest_factor > 100.0f ||
        !isfinite(axis.kurtosis) || axis.kurtosis < 0.0f ||
        axis.kurtosis > 1000.0f) return false;
  }
  const AxisVibrationMetrics gyro_axes[] = {
      value.gyro_x, value.gyro_y, value.gyro_z};
  for (const AxisVibrationMetrics& axis : gyro_axes) {
    if (!isfinite(axis.rms) || axis.rms < 0.0f || axis.rms > 2000.0f) {
      return false;
    }
  }
  const FrequencyMetrics frequencies[] = {
      value.accel_x_frequency, value.accel_y_frequency,
      value.accel_z_frequency};
  for (const FrequencyMetrics& frequency : frequencies) {
    if (!isfinite(frequency.dominant_frequency_hz) ||
        frequency.dominant_frequency_hz < 0.0f ||
        frequency.dominant_frequency_hz > 250.0f ||
        !isfinite(frequency.dominant_amplitude) ||
        frequency.dominant_amplitude < 0.0f ||
        frequency.dominant_amplitude > 16.0f) return false;
  }
  return isfinite(value.effective_sample_rate_hz) &&
      value.effective_sample_rate_hz > 0.0f &&
      value.effective_sample_rate_hz <= 500.0f;
}

}  // namespace

bool encodeVibrationSummary(const VibrationWindowResult& value,
                            uint32_t sequence, uint32_t uptime_ms,
                            char* output, size_t output_size) {
  if (!output || output_size < kVibrationSummaryMaximumBytes ||
      !metricsAreBounded(value)) return false;
  const int written = snprintf(
      output, output_size,
      "{\"t\":\"v\",\"v\":%u,\"s\":%lu,\"m\":%lu,\"a\":%u,"
      "\"f\":%lu,\"q\":1,"
      "\"r\":[%lu,%lu,%lu],\"p\":[%lu,%lu,%lu],"
      "\"c\":[%lu,%lu,%lu],\"k\":[%lu,%lu,%lu],"
      "\"d\":[%lu,%lu,%lu],\"x\":[%lu,%lu,%lu],"
      "\"g\":[%lu,%lu,%lu]}",
      kVibrationSummarySchemaVersion, static_cast<unsigned long>(sequence),
      static_cast<unsigned long>(uptime_ms), kVibrationAlgorithmVersion,
      static_cast<unsigned long>(lroundf(value.effective_sample_rate_hz * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_x.rms * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_y.rms * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_z.rms * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_x.peak_abs * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_y.peak_abs * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_z.peak_abs * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_x.crest_factor * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_y.crest_factor * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_z.crest_factor * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_x.kurtosis * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_y.kurtosis * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_z.kurtosis * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_x_frequency.dominant_frequency_hz * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_y_frequency.dominant_frequency_hz * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_z_frequency.dominant_frequency_hz * 10.0f)),
      static_cast<unsigned long>(lroundf(value.accel_x_frequency.dominant_amplitude * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_y_frequency.dominant_amplitude * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.accel_z_frequency.dominant_amplitude * 1000.0f)),
      static_cast<unsigned long>(lroundf(value.gyro_x.rms * 10.0f)),
      static_cast<unsigned long>(lroundf(value.gyro_y.rms * 10.0f)),
      static_cast<unsigned long>(lroundf(value.gyro_z.rms * 10.0f)));
  return written > 0 && static_cast<size_t>(written) < output_size &&
      static_cast<size_t>(written) <= kVibrationSummaryMaximumBytes;
}

}  // namespace seed_mg24
