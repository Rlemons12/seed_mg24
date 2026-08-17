#include "vibration_features.h"

#include <math.h>

namespace seed_mg24 {

AxisVibrationMetrics calculateAxisMetrics(
    const float* samples, size_t count, size_t stride,
    ProcessingYieldHook hook, void* hook_context) {
  AxisVibrationMetrics result = {};
  if (samples == 0 || count == 0 || stride == 0) return result;

  double sum = 0.0;
  double sum_squares = 0.0;
  float minimum = samples[0];
  float maximum = samples[0];
  float peak = fabsf(samples[0]);
  for (size_t index = 0; index < count; ++index) {
    const float value = samples[index * stride];
    sum += value;
    sum_squares += static_cast<double>(value) * value;
    if (value < minimum) minimum = value;
    if (value > maximum) maximum = value;
    if (fabsf(value) > peak) peak = fabsf(value);
    if (hook != 0 && (index & 0x07u) == 0x07u) hook(hook_context);
  }
  result.mean = static_cast<float>(sum / count);
  result.rms = static_cast<float>(sqrt(sum_squares / count));
  result.peak_abs = peak;
  result.peak_to_peak = maximum - minimum;

  double variance_sum = 0.0;
  double fourth_moment_sum = 0.0;
  for (size_t index = 0; index < count; ++index) {
    const double delta = samples[index * stride] - result.mean;
    const double square = delta * delta;
    variance_sum += square;
    fourth_moment_sum += square * square;
    if (hook != 0 && (index & 0x07u) == 0x07u) hook(hook_context);
  }
  const double variance = variance_sum / count;
  result.standard_deviation = static_cast<float>(sqrt(variance));
  result.crest_factor = result.rms > 1.0e-12f ? peak / result.rms : 0.0f;
  result.kurtosis = variance > 1.0e-20
                        ? static_cast<float>((fourth_moment_sum / count) /
                                             (variance * variance))
                        : 0.0f;
  return result;
}

}  // namespace seed_mg24
