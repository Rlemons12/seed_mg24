#include <assert.h>
#include <math.h>
#include <string.h>

#include "vibration_features.h"
#include "vibration_fft.h"
#include "vibration_filter.h"
#include "vibration_double_buffer.h"
#include "vibration_fifo.h"
#include "vibration_processor.h"
#include "vibration_runtime.h"
#include "vibration_summary.h"

namespace {

bool near(float actual, float expected, float tolerance = 1.0e-4f) {
  return fabsf(actual - expected) <= tolerance;
}

constexpr size_t kCount = seed_mg24::kVibrationWindowSamples;
constexpr float kSampleRate = 256.0f;
constexpr float kPi = 3.14159265358979323846f;

void makeSine(float* output, float frequency, float amplitude, float dc = 0.0f) {
  for (size_t index = 0; index < kCount; ++index) {
    output[index] = dc + amplitude * sinf(2.0f * kPi * frequency * index / kSampleRate);
  }
}

}  // namespace

int main() {
  using seed_mg24::AxisVibrationMetrics;
  using seed_mg24::FirstOrderHighPass;
  using seed_mg24::FrequencyMetrics;
  using seed_mg24::VibrationFft256;
  using seed_mg24::VibrationProcessor;
  using seed_mg24::VibrationProcessorConfig;
  using seed_mg24::VibrationWindowResult;
  using seed_mg24::calculateAxisMetrics;

  const AxisVibrationMetrics empty = calculateAxisMetrics(0, 0);
  assert(empty.mean == 0.0f && empty.rms == 0.0f && empty.crest_factor == 0.0f);

  float signal[kCount] = {};
  AxisVibrationMetrics metrics = calculateAxisMetrics(signal, kCount);
  assert(metrics.rms == 0.0f && metrics.kurtosis == 0.0f);

  for (size_t index = 0; index < kCount; ++index) signal[index] = 3.0f;
  metrics = calculateAxisMetrics(signal, kCount);
  assert(near(metrics.mean, 3.0f) && near(metrics.rms, 3.0f));
  assert(near(metrics.standard_deviation, 0.0f));
  assert(near(metrics.crest_factor, 1.0f) && near(metrics.kurtosis, 0.0f));

  makeSine(signal, 20.0f, 2.5f);
  metrics = calculateAxisMetrics(signal, kCount);
  assert(near(metrics.rms, 2.5f / sqrtf(2.0f), 1.0e-3f));
  assert(near(metrics.peak_abs, 2.5f, 1.0e-3f));
  assert(near(metrics.peak_to_peak, 5.0f, 1.0e-3f));

  VibrationFft256 fft;
  FrequencyMetrics frequency = fft.analyze(signal, kSampleRate, 5.0f);
  assert(near(frequency.dominant_frequency_hz, 20.0f));
  assert(near(frequency.dominant_amplitude, 2.5f, 0.03f));

  makeSine(signal, 20.0f, 1.5f, 9.81f);
  frequency = fft.analyze(signal, kSampleRate, 5.0f);
  assert(near(frequency.dominant_frequency_hz, 20.0f));
  assert(near(frequency.dominant_amplitude, 1.5f, 0.03f));

  for (size_t index = 0; index < kCount; ++index) {
    signal[index] = 0.4f * sinf(2.0f * kPi * 12.0f * index / kSampleRate) +
                    1.2f * sinf(2.0f * kPi * 45.0f * index / kSampleRate);
  }
  frequency = fft.analyze(signal, kSampleRate, 5.0f);
  assert(near(frequency.dominant_frequency_hz, 45.0f));

  FirstOrderHighPass filter;
  assert(filter.configure(kSampleRate, 2.0f));
  filter.reset(4.0f);
  for (size_t index = 0; index < kCount; ++index) signal[index] = filter.apply(4.0f);
  metrics = calculateAxisMetrics(signal, kCount);
  assert(metrics.rms < 1.0e-6f);
  assert(!filter.configure(0.0f, 2.0f));
  assert(!filter.configure(kSampleRate, kSampleRate));

  uint32_t state = 0x12345678u;
  for (size_t index = 0; index < kCount; ++index) {
    state = state * 1664525u + 1013904223u;
    signal[index] = static_cast<int32_t>(state >> 8) / 8388608.0f;
  }
  metrics = calculateAxisMetrics(signal, kCount);
  frequency = fft.analyze(signal, kSampleRate, 5.0f);
  assert(isfinite(metrics.rms) && isfinite(metrics.kurtosis));
  assert(isfinite(frequency.dominant_frequency_hz));

  seed_mg24::ImuRawSample raw[kCount] = {};
  for (size_t index = 0; index < kCount; ++index) {
    raw[index].accel_x = static_cast<int16_t>(
        10000.0f + 1200.0f * sinf(2.0f * kPi * 20.0f * index / kSampleRate));
    raw[index].accel_y = static_cast<int16_t>(
        300.0f * sinf(2.0f * kPi * 12.0f * index / kSampleRate));
    raw[index].gyro_x = static_cast<int16_t>(
        500.0f * sinf(2.0f * kPi * 20.0f * index / kSampleRate));
  }
  const VibrationProcessorConfig config = {
      kSampleRate, 2.0f, 5.0f, 0.001f, 0.01f};
  VibrationProcessor processor(config);
  VibrationWindowResult result = {};
  assert(processor.valid());
  assert(processor.process(raw, kCount, &result));
  assert(!processor.process(raw, kCount - 1, &result));
  assert(near(result.accel_x_frequency.dominant_frequency_hz, 20.0f));
  assert(result.accel_x.rms > 0.7f && result.accel_x.rms < 1.0f);
  assert(result.gyro_x.rms > 3.0f && result.gyro_x.rms < 4.0f);
  assert(isfinite(result.accel_x.kurtosis));

  seed_mg24::VibrationDoubleBuffer buffers;
  seed_mg24::ImuRawSample one_sample = {};
  for (size_t index = 0; index < kCount; ++index) {
    assert(buffers.append(one_sample, static_cast<uint32_t>(1000 + index * 2500)));
  }
  assert(buffers.counters().windows_completed == 1);
  assert(buffers.counters().samples_captured == kCount);
  seed_mg24::ReadyWindow ready = {};
  assert(buffers.acquireReady(&ready));
  assert(ready.timing.sample_count == kCount);
  assert(near(ready.timing.effective_sample_rate_hz, 400.0f));
  assert(!buffers.acquireReady(&ready));
  for (size_t index = 0; index < kCount; ++index) {
    assert(buffers.append(one_sample, static_cast<uint32_t>(700000 + index * 2500)));
  }
  assert(buffers.counters().buffer_overruns == 1);
  assert(!buffers.append(one_sample, 1400000));
  assert(buffers.counters().samples_dropped == 1);
  assert(buffers.release(ready.buffer_index));
  assert(!buffers.release(ready.buffer_index));
  assert(buffers.counters().windows_processed == 1);

  int16_t fifo_words[seed_mg24::kImuFifoWordsPerFrame * 3] = {
      1, 2, 3, 4, 5, 6, 11, 12, 13, 14, 15, 16, 21, 22, 23, 24, 25, 26};
  seed_mg24::RawImuFifoFrame fifo_frame = {};
  assert(seed_mg24::parseFifoFrame(
      fifo_words, seed_mg24::kImuFifoWordsPerFrame, &fifo_frame));
  assert(fifo_frame.gyro_x == 1 && fifo_frame.accel_z == 6);
  assert(!seed_mg24::parseFifoFrame(fifo_words, 5, &fifo_frame));

  seed_mg24::VibrationDoubleBuffer fifo_buffers;
  seed_mg24::FifoWindowAssembler assembler(fifo_buffers);
  assert(!assembler.appendBatch(fifo_words, 7, 1000, 2500));
  assert(assembler.counters().partial_batches_rejected == 1);
  for (size_t batch = 0; batch < 85; ++batch) {
    assert(assembler.appendBatch(fifo_words, 18,
        static_cast<uint32_t>(1000 + batch * 3 * 2500), 2500));
  }
  assert(fifo_buffers.counters().samples_captured == 255);
  assert(assembler.appendBatch(fifo_words, 6, 638500, 2500));
  assert(fifo_buffers.counters().windows_completed == 1);
  seed_mg24::ReadyWindow fifo_ready = {};
  assert(fifo_buffers.acquireReady(&fifo_ready));
  assert(fifo_ready.samples[0].gyro_x == 1);
  assert(fifo_ready.samples[0].accel_z == 6);
  assert(fifo_ready.samples[3].gyro_x == 1);
  assert(fifo_buffers.release(fifo_ready.buffer_index));

  seed_mg24::VibrationRuntimeState runtime;
  assert(runtime.lifecycle() == seed_mg24::VibrationLifecycleState::UNINITIALIZED);
  assert(runtime.validity() == seed_mg24::VibrationResultValidity::INITIALIZING);
  runtime.markReady();
  runtime.markAcquiring();
  runtime.markValidWindow(7);
  assert(runtime.lifecycle() == seed_mg24::VibrationLifecycleState::ACQUIRING);
  assert(runtime.validity() == seed_mg24::VibrationResultValidity::VALID);
  assert(runtime.windowSequence() == 7);
  seed_mg24::VibrationHealthCounters health = {};
  health.fifo_overruns = 2;
  health.windows_completed = 8;
  health.windows_processed = 7;
  runtime.updateCounters(health);
  assert(runtime.counters().fifo_overruns == 2);
  assert(runtime.counters().windows_completed == 8);
  runtime.markFault(seed_mg24::VibrationResultValidity::READ_ERROR);
  assert(runtime.lifecycle() == seed_mg24::VibrationLifecycleState::DEGRADED);
  runtime.markValidWindow(8);
  assert(runtime.validity() == seed_mg24::VibrationResultValidity::VALID);
  runtime.markFault(seed_mg24::VibrationResultValidity::UNAVAILABLE, true);
  runtime.markValidWindow(9);
  assert(runtime.lifecycle() == seed_mg24::VibrationLifecycleState::FAILED);
  assert(runtime.windowSequence() == 8);

  assert(!seed_mg24::shouldPrioritizeFifo(383, 384));
  assert(seed_mg24::shouldPrioritizeFifo(384, 384));
  assert(seed_mg24::mayProcessReadyWindow(383, 384));
  assert(!seed_mg24::mayProcessReadyWindow(384, 384));

  char summary[seed_mg24::kVibrationSummaryMaximumBytes] = {};
  result.effective_sample_rate_hz = 431.672f;
  assert(seed_mg24::encodeVibrationSummary(result, 123, 456, summary,
                                            sizeof(summary)));
  assert(strstr(summary, "\"t\":\"v\"") != 0);
  assert(strstr(summary, "\"a\":1") != 0);
  assert(strlen(summary) < sizeof(summary));
  result.effective_sample_rate_hz = 500.0f;
  AxisVibrationMetrics maximum_accel = {};
  maximum_accel.rms = 16.0f;
  maximum_accel.peak_abs = 16.0f;
  maximum_accel.crest_factor = 100.0f;
  maximum_accel.kurtosis = 1000.0f;
  result.accel_x = result.accel_y = result.accel_z = maximum_accel;
  result.gyro_x.rms = result.gyro_y.rms = result.gyro_z.rms = 2000.0f;
  result.accel_x_frequency = result.accel_y_frequency =
      result.accel_z_frequency = {250.0f, 16.0f};
  assert(seed_mg24::encodeVibrationSummary(
      result, 0xFFFFFFFFu, 0xFFFFFFFFu, summary, sizeof(summary)));
  assert(strlen(summary) <= seed_mg24::kVibrationSummaryMaximumBytes - 1);
  result.accel_x.rms = NAN;
  assert(!seed_mg24::encodeVibrationSummary(result, 124, 457, summary,
                                             sizeof(summary)));
  return 0;
}
