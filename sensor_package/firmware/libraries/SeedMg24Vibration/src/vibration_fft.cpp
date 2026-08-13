#include "vibration_fft.h"

#include <math.h>

namespace seed_mg24 {
namespace {

constexpr float kPi = 3.14159265358979323846f;

void transform(float* real, float* imaginary, size_t count,
               ProcessingYieldHook hook, void* hook_context) {
  for (size_t index = 1, reversed = 0; index < count; ++index) {
    size_t bit = count >> 1;
    for (; reversed & bit; bit >>= 1) reversed ^= bit;
    reversed ^= bit;
    if (index < reversed) {
      const float real_temp = real[index];
      real[index] = real[reversed];
      real[reversed] = real_temp;
      const float imaginary_temp = imaginary[index];
      imaginary[index] = imaginary[reversed];
      imaginary[reversed] = imaginary_temp;
    }
    if (hook != 0 && (index & 0x0Fu) == 0x0Fu) hook(hook_context);
  }
  size_t butterflies_since_hook = 0;
  for (size_t length = 2; length <= count; length <<= 1) {
    const float angle = -2.0f * kPi / static_cast<float>(length);
    const float root_real = cosf(angle);
    const float root_imaginary = sinf(angle);
    for (size_t base = 0; base < count; base += length) {
      float twiddle_real = 1.0f;
      float twiddle_imaginary = 0.0f;
      for (size_t offset = 0; offset < length / 2; ++offset) {
        const size_t even = base + offset;
        const size_t odd = even + length / 2;
        const float odd_real = real[odd] * twiddle_real - imaginary[odd] * twiddle_imaginary;
        const float odd_imaginary = real[odd] * twiddle_imaginary + imaginary[odd] * twiddle_real;
        real[odd] = real[even] - odd_real;
        imaginary[odd] = imaginary[even] - odd_imaginary;
        real[even] += odd_real;
        imaginary[even] += odd_imaginary;
        const float next_real = twiddle_real * root_real - twiddle_imaginary * root_imaginary;
        twiddle_imaginary = twiddle_real * root_imaginary + twiddle_imaginary * root_real;
        twiddle_real = next_real;
        if (hook != 0 && ++butterflies_since_hook == 16) {
          hook(hook_context);
          butterflies_since_hook = 0;
        }
      }
    }
  }
}

}  // namespace

FrequencyMetrics VibrationFft256::analyze(
    const float* samples, float sample_rate_hz, float minimum_frequency_hz,
    ProcessingYieldHook hook, void* hook_context) {
  FrequencyMetrics result = {};
  if (samples == 0 || !(sample_rate_hz > 0.0f)) return result;

  float window_sum = 0.0f;
  for (size_t index = 0; index < kVibrationWindowSamples; ++index) {
    const float window = 0.5f - 0.5f * cosf(
        2.0f * kPi * index / static_cast<float>(kVibrationWindowSamples - 1));
    real_[index] = samples[index] * window;
    imaginary_[index] = 0.0f;
    window_sum += window;
    if (hook != 0 && (index & 0x07u) == 0x07u) hook(hook_context);
  }
  transform(real_, imaginary_, kVibrationWindowSamples, hook, hook_context);

  const float bin_hz = sample_rate_hz / kVibrationWindowSamples;
  size_t first_bin = static_cast<size_t>(ceilf(minimum_frequency_hz / bin_hz));
  if (first_bin < 1) first_bin = 1;
  if (first_bin >= kVibrationWindowSamples / 2) return result;
  for (size_t bin = 0; bin < kVibrationWindowSamples / 2; ++bin) {
    const float magnitude = sqrtf(real_[bin] * real_[bin] + imaginary_[bin] * imaginary_[bin]);
    amplitudes_[bin] = bin == 0 ? magnitude / window_sum : 2.0f * magnitude / window_sum;
    if (bin >= first_bin && amplitudes_[bin] > result.dominant_amplitude) {
      result.dominant_amplitude = amplitudes_[bin];
      result.dominant_bin = static_cast<uint16_t>(bin);
    }
    if (hook != 0 && (bin & 0x0Fu) == 0x0Fu) hook(hook_context);
  }
  result.dominant_frequency_hz = result.dominant_bin * bin_hz;
  return result;
}

}  // namespace seed_mg24
