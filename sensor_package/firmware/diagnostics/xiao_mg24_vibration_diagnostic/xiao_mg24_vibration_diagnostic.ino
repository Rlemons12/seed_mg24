#include <Arduino.h>
#include <LSM6DS3.h>
#include <Wire.h>
#include <vibration_acquisition.h>
#include <vibration_double_buffer.h>
#include <vibration_fifo.h>
#include <vibration_fifo_wire.h>
#include <vibration_processor.h>

#define IMU_POWER_PIN PD5

// Arduino's sketch preprocessor can place generated prototypes before local
// struct declarations; keep this forward declaration explicit.
struct ContinuousStats;
void serviceContinuousAcquisition(ContinuousStats* stats);

using seed_mg24::AxisVibrationMetrics;
using seed_mg24::FrequencyMetrics;
using seed_mg24::ImuRawSample;
using seed_mg24::Lsm6ds3CoherentReader;
using seed_mg24::ReadyWindow;
using seed_mg24::FifoWindowAssembler;
using seed_mg24::Lsm6ds3FifoWireReader;
using seed_mg24::VibrationDoubleBuffer;
using seed_mg24::VibrationProcessor;
using seed_mg24::VibrationProcessorConfig;
using seed_mg24::VibrationWindowResult;

constexpr uint16_t kConfiguredOdrHz = 416;
constexpr uint16_t kRateTestSamples = 1024;
constexpr uint16_t kFeatureSamples = seed_mg24::kVibrationWindowSamples;
constexpr uint32_t kExpectedIntervalUs = 1000000UL / kConfiguredOdrHz;
constexpr uint32_t kAcquisitionTimeoutUs = 10000000UL;
constexpr float kAccelGPerCount = 16.0f / 32768.0f;
constexpr float kGyroDpsPerCount = 2000.0f / 32768.0f;
constexpr float kHighPassCutoffHz = 2.0f;
constexpr float kMinimumDominantFrequencyHz = 5.0f;
// Begin checking shortly before the ~2.317 ms measured period. This prevents
// every DSP yield from issuing a costly I2C STATUS transaction.
constexpr uint32_t kCooperativePollLeadUs = 1800;
constexpr uint32_t kDiagnosticI2cClockHz = 400000;
constexpr uint16_t kFifoValidationFrames = 32;
constexpr uint16_t kFifoValidationWords = kFifoValidationFrames * 6;
constexpr uint16_t kFifoBatchFrames = 16;
constexpr uint16_t kFifoBatchWords = kFifoBatchFrames * 6;
constexpr uint32_t kFifoSampleIntervalUs = 2500;

struct TimingResult {
  uint32_t samples;
  uint32_t elapsed_us;
  uint32_t min_interval_us;
  uint32_t max_interval_us;
  uint64_t interval_sum_us;
  uint64_t interval_square_sum_us;
  uint32_t estimated_missed_samples;
  uint32_t partial_ready_polls;
  uint32_t read_errors;
  uint16_t fifo_status;
  bool timed_out;
};

LSM6DS3 vibrationImu(I2C_MODE, 0x6A);
Lsm6ds3CoherentReader coherentReader(vibrationImu);
ImuRawSample sampleBuffer[kFeatureSamples];
VibrationProcessorConfig processorConfig = {
    static_cast<float>(kConfiguredOdrHz), kHighPassCutoffHz,
    kMinimumDominantFrequencyHz, kAccelGPerCount, kGyroDpsPerCount};
VibrationProcessor processor(processorConfig, micros);
VibrationDoubleBuffer continuousBuffers;
FifoWindowAssembler fifoAssembler(continuousBuffers);
Lsm6ds3FifoWireReader fifoWireReader(
    Wire1, 0x6A, LSM6DS3_ACC_GYRO_FIFO_DATA_OUT_L);
int16_t fifoBatchWords[kFifoBatchWords];
bool imuReady = false;
bool streamEnabled = false;

struct OperationTiming {
  uint64_t sum_us;
  uint32_t minimum_us;
  uint32_t maximum_us;
  uint32_t calls;
};

void addTiming(OperationTiming* timing, uint32_t duration_us) {
  timing->sum_us += duration_us;
  timing->calls++;
  if (timing->calls == 1 || duration_us < timing->minimum_us) {
    timing->minimum_us = duration_us;
  }
  if (duration_us > timing->maximum_us) timing->maximum_us = duration_us;
}

void printOperationTiming(const char* operation, const OperationTiming& timing) {
  Serial.print("i2c_operation="); Serial.print(operation);
  Serial.print(" calls="); Serial.print(timing.calls);
  Serial.print(" average_us=");
  Serial.print(timing.calls ? timing.sum_us / timing.calls : 0);
  Serial.print(" minimum_us="); Serial.print(timing.minimum_us);
  Serial.print(" maximum_us="); Serial.println(timing.maximum_us);
}

struct ContinuousStats {
  uint32_t data_ready_events;
  uint32_t read_errors;
  uint32_t timeout_count;
  uint32_t test_start_us;
  uint32_t test_end_us;
  uint32_t previous_window_end_us;
  uint32_t window_interval_sum_us;
  uint32_t minimum_window_interval_us;
  uint32_t maximum_window_interval_us;
  uint64_t processing_sum_us;
  uint32_t processing_max_us;
  uint32_t maximum_processing_block_us;
  uint64_t sample_service_sum_us;
  uint32_t sample_service_calls;
  uint32_t sample_service_max_us;
  uint32_t last_sample_us;
  uint32_t last_yield_us;
  float effective_rate_sum_hz;
  float smoothed_effective_rate_hz;
};

void serviceContinuousAcquisition(ContinuousStats* stats) {
  const uint32_t service_start = micros();
  bool accelReady = false;
  bool gyroReady = false;
  if (coherentReader.dataReady(&accelReady, &gyroReady)) {
    stats->data_ready_events++;
    ImuRawSample sample = {};
    if (coherentReader.read(&sample)) {
      stats->last_sample_us = service_start;
      continuousBuffers.append(sample, service_start);
    } else {
      stats->read_errors++;
    }
  }
  const uint32_t duration = micros() - service_start;
  stats->sample_service_sum_us += duration;
  stats->sample_service_calls++;
  if (duration > stats->sample_service_max_us) stats->sample_service_max_us = duration;
}

void continuousProcessingYield(void* context) {
  ContinuousStats* stats = static_cast<ContinuousStats*>(context);
  const uint32_t now = micros();
  const uint32_t block = now - stats->last_yield_us;
  if (block > stats->maximum_processing_block_us) {
    stats->maximum_processing_block_us = block;
  }
  if (static_cast<uint32_t>(now - stats->last_sample_us) >= kCooperativePollLeadUs) {
    serviceContinuousAcquisition(stats);
  }
  stats->last_yield_us = micros();
}

TimingResult acquire(uint16_t targetSamples, bool storeSamples) {
  TimingResult result = {};
  result.min_interval_us = UINT32_MAX;
  const uint32_t waitStart = micros();
  uint32_t firstSampleUs = 0;
  uint32_t previousSampleUs = 0;
  while (result.samples < targetSamples) {
    bool accelReady = false;
    bool gyroReady = false;
    if (!coherentReader.dataReady(&accelReady, &gyroReady)) {
      if (accelReady != gyroReady) result.partial_ready_polls++;
      if (static_cast<uint32_t>(micros() - waitStart) > kAcquisitionTimeoutUs) {
        result.timed_out = true;
        break;
      }
      continue;
    }

    ImuRawSample sample = {};
    const uint32_t timestampUs = micros();
    if (!coherentReader.read(&sample)) {
      result.read_errors++;
      continue;
    }
    if (storeSamples && result.samples < kFeatureSamples) sampleBuffer[result.samples] = sample;
    if (result.samples == 0) {
      firstSampleUs = timestampUs;
    } else {
      const uint32_t interval = timestampUs - previousSampleUs;
      result.interval_sum_us += interval;
      result.interval_square_sum_us += static_cast<uint64_t>(interval) * interval;
      if (interval < result.min_interval_us) result.min_interval_us = interval;
      if (interval > result.max_interval_us) result.max_interval_us = interval;
      if (interval > kExpectedIntervalUs + kExpectedIntervalUs / 2) {
        const uint32_t periods = (interval + kExpectedIntervalUs / 2) / kExpectedIntervalUs;
        if (periods > 1) result.estimated_missed_samples += periods - 1;
      }
    }
    previousSampleUs = timestampUs;
    result.samples++;
  }
  result.elapsed_us = result.samples > 1 ? previousSampleUs - firstSampleUs : 0;
  result.fifo_status = vibrationImu.fifoGetStatus();
  if (result.samples < 2) result.min_interval_us = 0;
  return result;
}

void printTiming(const TimingResult& result, const char* mode) {
  const uint32_t intervals = result.samples > 1 ? result.samples - 1 : 0;
  const double average = intervals ? static_cast<double>(result.interval_sum_us) / intervals : 0.0;
  double variance = intervals ? static_cast<double>(result.interval_square_sum_us) / intervals - average * average : 0.0;
  if (variance < 0.0) variance = 0.0;
  const double rate = result.elapsed_us ? static_cast<double>(result.samples - 1) * 1000000.0 / result.elapsed_us : 0.0;
  Serial.print("VIBRATION_RATE mode="); Serial.println(mode);
  Serial.print("samples_collected="); Serial.println(result.samples);
  Serial.print("elapsed_us="); Serial.println(result.elapsed_us);
  Serial.print("measured_samples_per_second="); Serial.println(rate, 3);
  Serial.print("average_sample_interval_us="); Serial.println(average, 3);
  Serial.print("minimum_sample_interval_us="); Serial.println(result.min_interval_us);
  Serial.print("maximum_sample_interval_us="); Serial.println(result.max_interval_us);
  Serial.print("timing_jitter_us="); Serial.println(sqrt(variance), 3);
  Serial.print("estimated_missed_samples="); Serial.println(result.estimated_missed_samples);
  Serial.print("partial_ready_polls="); Serial.println(result.partial_ready_polls);
  Serial.print("read_errors="); Serial.println(result.read_errors);
  Serial.print("fifo_sample_words="); Serial.println(result.fifo_status & 0x0FFF);
  Serial.print("fifo_overrun="); Serial.println((result.fifo_status & 0x4000) ? 1 : 0);
  Serial.print("timed_out="); Serial.println(result.timed_out ? 1 : 0);
}

void printAxis(const char* signal, const AxisVibrationMetrics& metrics) {
  Serial.print("VIBRATION_FEATURE signal="); Serial.print(signal);
  Serial.print(" mean="); Serial.print(metrics.mean, 6);
  Serial.print(" rms="); Serial.print(metrics.rms, 6);
  Serial.print(" peak_abs="); Serial.print(metrics.peak_abs, 6);
  Serial.print(" peak_to_peak="); Serial.print(metrics.peak_to_peak, 6);
  Serial.print(" standard_deviation="); Serial.print(metrics.standard_deviation, 6);
  Serial.print(" crest_factor="); Serial.print(metrics.crest_factor, 6);
  Serial.print(" kurtosis="); Serial.println(metrics.kurtosis, 6);
}

void printFrequency(const char* signal, const FrequencyMetrics& metrics) {
  Serial.print("VIBRATION_FREQUENCY signal="); Serial.print(signal);
  Serial.print(" dominant_frequency_hz="); Serial.print(metrics.dominant_frequency_hz, 3);
  Serial.print(" dominant_amplitude="); Serial.print(metrics.dominant_amplitude, 6);
  Serial.print(" dominant_bin="); Serial.println(metrics.dominant_bin);
}

bool acquireAndProcess(VibrationWindowResult* result, TimingResult* timing) {
  *timing = acquire(kFeatureSamples, true);
  processor.resetFilterState();
  const float effectiveRate = timing->elapsed_us > 0
      ? static_cast<float>(timing->samples - 1) * 1000000.0f / timing->elapsed_us
      : static_cast<float>(kConfiguredOdrHz);
  return timing->samples == kFeatureSamples &&
         processor.process(sampleBuffer, kFeatureSamples, result, effectiveRate);
}

void runContinuousTest(uint16_t targetWindows) {
  ContinuousStats stats = {};
  stats.minimum_window_interval_us = UINT32_MAX;
  stats.smoothed_effective_rate_hz = static_cast<float>(kConfiguredOdrHz);
  continuousBuffers.reset();
  processor.resetFilterState();
  stats.test_start_us = micros();
  const uint32_t timeout_us = static_cast<uint32_t>(targetWindows) * 1000000UL + 10000000UL;

  while (continuousBuffers.counters().windows_processed < targetWindows) {
    serviceContinuousAcquisition(&stats);
    ReadyWindow window = {};
    if (continuousBuffers.acquireReady(&window)) {
      if (stats.previous_window_end_us != 0) {
        const uint32_t interval = window.timing.window_end_us - stats.previous_window_end_us;
        stats.window_interval_sum_us += interval;
        if (interval < stats.minimum_window_interval_us) stats.minimum_window_interval_us = interval;
        if (interval > stats.maximum_window_interval_us) stats.maximum_window_interval_us = interval;
      }
      stats.previous_window_end_us = window.timing.window_end_us;
      stats.effective_rate_sum_hz += window.timing.effective_sample_rate_hz;
      if (continuousBuffers.counters().windows_completed == 1) {
        stats.smoothed_effective_rate_hz = window.timing.effective_sample_rate_hz;
      } else {
        stats.smoothed_effective_rate_hz =
            0.8f * stats.smoothed_effective_rate_hz +
            0.2f * window.timing.effective_sample_rate_hz;
      }
      VibrationWindowResult result = {};
      const uint32_t processingStart = micros();
      stats.last_yield_us = processingStart;
      const bool processed = processor.process(
          window.samples, window.timing.sample_count, &result,
          stats.smoothed_effective_rate_hz, continuousProcessingYield, &stats);
      const uint32_t processingDuration = micros() - processingStart;
      stats.processing_sum_us += processingDuration;
      if (processingDuration > stats.processing_max_us) stats.processing_max_us = processingDuration;
      if (!processed || !continuousBuffers.release(window.buffer_index)) {
        stats.timeout_count++;
        break;
      }
    }
    if (static_cast<uint32_t>(micros() - stats.test_start_us) > timeout_us) {
      stats.timeout_count++;
      break;
    }
  }
  stats.test_end_us = micros();
  const seed_mg24::DoubleBufferCounters& counters = continuousBuffers.counters();
  const uint32_t intervals = counters.windows_completed > 1
      ? counters.windows_completed - 1 : 0;
  Serial.println("CONTINUOUS_TEST_RESULT");
  Serial.print("target_windows="); Serial.println(targetWindows);
  Serial.print("windows_acquired="); Serial.println(counters.windows_completed);
  Serial.print("windows_processed="); Serial.println(counters.windows_processed);
  Serial.print("samples_captured="); Serial.println(counters.samples_captured);
  Serial.print("samples_dropped="); Serial.println(counters.samples_dropped);
  Serial.print("buffer_swap_count="); Serial.println(counters.buffer_swaps);
  Serial.print("buffer_overrun_count="); Serial.println(counters.buffer_overruns);
  Serial.print("data_ready_events="); Serial.println(stats.data_ready_events);
  Serial.print("read_errors="); Serial.println(stats.read_errors);
  Serial.print("timeout_count="); Serial.println(stats.timeout_count);
  Serial.print("configured_sample_rate_hz="); Serial.println(kConfiguredOdrHz);
  Serial.print("average_effective_sample_rate_hz=");
  Serial.println(counters.windows_completed ? stats.effective_rate_sum_hz / counters.windows_completed : 0.0f, 3);
  Serial.print("smoothed_effective_sample_rate_hz="); Serial.println(stats.smoothed_effective_rate_hz, 3);
  Serial.print("average_window_interval_us=");
  Serial.println(intervals ? stats.window_interval_sum_us / intervals : 0);
  Serial.print("minimum_window_interval_us="); Serial.println(intervals ? stats.minimum_window_interval_us : 0);
  Serial.print("maximum_window_interval_us="); Serial.println(stats.maximum_window_interval_us);
  Serial.print("processing_time_average_us=");
  Serial.println(counters.windows_processed ? stats.processing_sum_us / counters.windows_processed : 0);
  Serial.print("processing_time_max_us="); Serial.println(stats.processing_max_us);
  Serial.print("maximum_processing_block_us="); Serial.println(stats.maximum_processing_block_us);
  Serial.print("sample_service_average_us=");
  Serial.println(stats.sample_service_calls ? stats.sample_service_sum_us / stats.sample_service_calls : 0);
  Serial.print("sample_service_max_us="); Serial.println(stats.sample_service_max_us);
  Serial.print("test_elapsed_us="); Serial.println(stats.test_end_us - stats.test_start_us);
  Serial.println("CONTINUOUS_TEST_END");
}

uint16_t readFifoPattern() {
  uint8_t low = 0;
  uint8_t high = 0;
  vibrationImu.readRegister(&low, LSM6DS3_ACC_GYRO_FIFO_STATUS3);
  vibrationImu.readRegister(&high, LSM6DS3_ACC_GYRO_FIFO_STATUS4);
  return static_cast<uint16_t>(low) | (static_cast<uint16_t>(high & 0x03) << 8);
}

int16_t readFifoWordRegion() {
  uint8_t bytes[2] = {};
  vibrationImu.readRegisterRegion(
      bytes, LSM6DS3_ACC_GYRO_FIFO_DATA_OUT_L, sizeof(bytes));
  return static_cast<int16_t>(static_cast<uint16_t>(bytes[0]) |
                              (static_cast<uint16_t>(bytes[1]) << 8));
}

void runFifoValidation() {
  vibrationImu.fifoEnd();
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL1,
                             kFifoValidationWords & 0xFF);
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL2,
                             (kFifoValidationWords >> 8) & 0x0F);
  // No decimation: accel bits [2:0]=1, gyro bits [5:3]=1.
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL3, 0x09);
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL4, 0x00);
  // FIFO ODR 400 Hz (0x30), continuous mode (0x06).
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL5, 0x36);
  const uint32_t waitStart = micros();
  uint16_t status = 0;
  while (((status = vibrationImu.fifoGetStatus()) & 0x8000) == 0 &&
         static_cast<uint32_t>(micros() - waitStart) < 3000000UL) {}
  const uint16_t patternBefore = readFifoPattern();
  const uint16_t wordsBefore = status & 0x0FFF;
  int16_t firstFrame[6] = {};
  int32_t absoluteSums[6] = {};
  const uint32_t readStart = micros();
  uint16_t wordsRead = 0;
  for (uint16_t frame = 0; frame < kFifoValidationFrames; ++frame) {
    for (uint8_t word = 0; word < 6; ++word) {
      const int16_t value = readFifoWordRegion();
      if (frame == 0) firstFrame[word] = value;
      absoluteSums[word] += value < 0 ? -static_cast<int32_t>(value) : value;
      wordsRead++;
    }
  }
  const uint32_t readDuration = micros() - readStart;
  const uint16_t patternAfter = readFifoPattern();
  const uint16_t statusAfter = vibrationImu.fifoGetStatus();
  vibrationImu.fifoEnd();
  Serial.println("FIFO_VALIDATION_RESULT");
  Serial.println("fifo_mode=continuous");
  Serial.println("fifo_odr_hz=400");
  Serial.print("watermark_words="); Serial.println(kFifoValidationWords);
  Serial.print("watermark_reached="); Serial.println((status & 0x8000) ? 1 : 0);
  Serial.print("words_before_read="); Serial.println(wordsBefore);
  Serial.print("words_read="); Serial.println(wordsRead);
  Serial.print("pattern_before="); Serial.println(patternBefore);
  Serial.print("pattern_after="); Serial.println(patternAfter);
  Serial.print("words_after_read="); Serial.println(statusAfter & 0x0FFF);
  Serial.print("overrun_before="); Serial.println((status & 0x4000) ? 1 : 0);
  Serial.print("overrun_after="); Serial.println((statusAfter & 0x4000) ? 1 : 0);
  Serial.print("batch_read_us="); Serial.println(readDuration);
  Serial.println("fifo_read_method=two_byte_register_region");
  const char* names[6] = {"gyro_x", "gyro_y", "gyro_z", "accel_x", "accel_y", "accel_z"};
  for (uint8_t word = 0; word < 6; ++word) {
    Serial.print("fifo_word position="); Serial.print(word);
    Serial.print(" assumed_signal="); Serial.print(names[word]);
    Serial.print(" first_raw="); Serial.print(firstFrame[word]);
    Serial.print(" mean_abs_raw="); Serial.println(absoluteSums[word] / kFifoValidationFrames);
  }
  Serial.println("FIFO_VALIDATION_END");
}

void configureDiagnosticFifo(uint16_t watermark_words) {
  vibrationImu.fifoEnd();
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL1,
                             watermark_words & 0xFF);
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL2,
                             (watermark_words >> 8) & 0x0F);
  // One gyro dataset followed by one accelerometer dataset, no decimation.
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL3, 0x09);
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL4, 0x00);
  vibrationImu.writeRegister(LSM6DS3_ACC_GYRO_FIFO_CTRL5, 0x36);
}

bool waitForFifoWords(uint16_t minimum_words, uint16_t* status,
                      uint32_t timeout_us) {
  const uint32_t started = micros();
  do {
    *status = vibrationImu.fifoGetStatus();
    if ((*status & 0x0FFF) >= minimum_words) return true;
  } while (static_cast<uint32_t>(micros() - started) < timeout_us);
  return false;
}

void runI2cTimingTest() {
  constexpr uint16_t kIterations = 128;
  OperationTiming statusLegacy = {};
  OperationTiming statusCombined = {};
  OperationTiming coherentLegacy = {};
  OperationTiming coherentCombined = {};
  uint8_t byte = 0;
  uint8_t sample[12] = {};
  for (uint16_t index = 0; index < kIterations; ++index) {
    uint32_t started = micros();
    vibrationImu.readRegister(&byte, LSM6DS3_ACC_GYRO_STATUS_REG);
    addTiming(&statusLegacy, micros() - started);
    started = micros();
    fifoWireReader.readRegisterCombined(
        LSM6DS3_ACC_GYRO_STATUS_REG, &byte, sizeof(byte));
    addTiming(&statusCombined, micros() - started);
    started = micros();
    vibrationImu.readRegisterRegion(
        sample, LSM6DS3_ACC_GYRO_OUTX_L_G, sizeof(sample));
    addTiming(&coherentLegacy, micros() - started);
    started = micros();
    fifoWireReader.readRegisterCombined(
        LSM6DS3_ACC_GYRO_OUTX_L_G, sample, sizeof(sample));
    addTiming(&coherentCombined, micros() - started);
  }
  Serial.println("I2C_TIMING_RESULT");
  Serial.print("wire_rx_buffer_bytes=64 iterations="); Serial.println(kIterations);
  printOperationTiming("status_legacy_two_transfer", statusLegacy);
  printOperationTiming("status_combined_write_read", statusCombined);
  printOperationTiming("coherent_12_byte_legacy_two_transfer", coherentLegacy);
  printOperationTiming("coherent_12_byte_combined_write_read", coherentCombined);
  Serial.println("fifo_safe_payload_bytes_per_transaction=2");
  Serial.println("fifo_multibyte_reason=addresses_after_0x3f_are_timestamp_registers");
  Serial.println("I2C_TIMING_END");
}

void runFifoTransportTest() {
  configureDiagnosticFifo(kFifoBatchWords);
  uint16_t statusBefore = 0;
  const bool ready = waitForFifoWords(kFifoBatchWords, &statusBefore, 3000000UL);
  const uint16_t patternBefore = readFifoPattern();
  fifoWireReader.resetCounters();
  const uint32_t started = micros();
  const bool readOk = ready && fifoWireReader.readWords(
      fifoBatchWords, kFifoBatchWords, kFifoBatchWords);
  const uint32_t elapsed = micros() - started;
  const uint16_t patternAfter = readFifoPattern();
  const uint16_t statusAfter = vibrationImu.fifoGetStatus();
  vibrationImu.fifoEnd();
  const seed_mg24::FifoWireCounters& counters = fifoWireReader.counters();
  const float wordsPerSecond = elapsed > 0
      ? counters.words_read * 1000000.0f / elapsed : 0.0f;
  Serial.println("FIFO_TRANSPORT_RESULT");
  Serial.print("ready="); Serial.println(ready ? 1 : 0);
  Serial.print("read_ok="); Serial.println(readOk ? 1 : 0);
  Serial.print("words_before="); Serial.println(statusBefore & 0x0FFF);
  Serial.print("pattern_before="); Serial.println(patternBefore);
  Serial.print("words_read="); Serial.println(counters.words_read);
  Serial.print("bytes_read="); Serial.println(counters.words_read * 2);
  Serial.print("transactions="); Serial.println(counters.transactions);
  Serial.print("elapsed_us="); Serial.println(elapsed);
  Serial.print("words_per_second="); Serial.println(wordsPerSecond, 1);
  Serial.print("frames_per_second="); Serial.println(wordsPerSecond / 6.0f, 1);
  Serial.print("headroom_over_2400_words_per_second=");
  Serial.println(wordsPerSecond / 2400.0f, 3);
  Serial.print("words_after="); Serial.println(statusAfter & 0x0FFF);
  Serial.print("pattern_after="); Serial.println(patternAfter);
  Serial.print("short_reads="); Serial.println(counters.short_reads);
  Serial.print("read_errors="); Serial.println(counters.read_errors);
  Serial.print("fifo_overrun="); Serial.println((statusAfter & 0x4000) ? 1 : 0);
  Serial.println("FIFO_TRANSPORT_END");
}

void runFifoContinuousTest(uint16_t target_windows) {
  continuousBuffers.reset();
  fifoAssembler.reset();
  fifoWireReader.resetCounters();
  processor.resetFilterState();
  configureDiagnosticFifo(kFifoBatchWords);
  const uint32_t testStart = micros();
  const uint32_t timeoutUs = static_cast<uint32_t>(target_windows) * 1000000UL + 10000000UL;
  uint32_t batches = 0;
  uint32_t fifoOverruns = 0;
  uint32_t alignmentErrors = 0;
  uint32_t readErrors = 0;
  uint32_t maximumOccupancyWords = 0;
  OperationTiming drainTiming = {};
  OperationTiming processingTiming = {};

  while (continuousBuffers.counters().windows_processed < target_windows &&
         static_cast<uint32_t>(micros() - testStart) < timeoutUs) {
    const uint16_t status = vibrationImu.fifoGetStatus();
    const uint16_t availableWords = status & 0x0FFF;
    if (availableWords > maximumOccupancyWords) maximumOccupancyWords = availableWords;
    if (status & 0x4000) fifoOverruns++;
    if (availableWords >= kFifoBatchWords) {
      const uint16_t patternBefore = readFifoPattern();
      if (patternBefore != 0) {
        alignmentErrors++;
        break;
      }
      const uint32_t drainStart = micros();
      const bool readOk = fifoWireReader.readWords(
          fifoBatchWords, kFifoBatchWords, kFifoBatchWords);
      addTiming(&drainTiming, micros() - drainStart);
      if (!readOk) {
        readErrors++;
        break;
      }
      const uint16_t patternAfter = readFifoPattern();
      if (patternAfter != 0) {
        alignmentErrors++;
        break;
      }
      const uint32_t firstSampleUs = testStart +
          fifoAssembler.counters().frames_accepted * kFifoSampleIntervalUs;
      if (!fifoAssembler.appendBatch(
              fifoBatchWords, kFifoBatchWords, firstSampleUs,
              kFifoSampleIntervalUs)) {
        break;
      }
      batches++;
    }

    ReadyWindow window = {};
    if (continuousBuffers.acquireReady(&window)) {
      VibrationWindowResult result = {};
      const uint32_t processingStart = micros();
      const bool processed = processor.process(
          window.samples, window.timing.sample_count, &result, 400.0f);
      addTiming(&processingTiming, micros() - processingStart);
      if (!processed || !continuousBuffers.release(window.buffer_index)) {
        readErrors++;
        break;
      }
    }
  }
  const uint32_t testElapsed = micros() - testStart;
  const uint16_t finalStatus = vibrationImu.fifoGetStatus();
  if (finalStatus & 0x4000) fifoOverruns++;
  vibrationImu.fifoEnd();
  const seed_mg24::DoubleBufferCounters& buffers = continuousBuffers.counters();
  const seed_mg24::FifoWireCounters& wire = fifoWireReader.counters();
  const float effectiveRate = testElapsed > 0
      ? fifoAssembler.counters().frames_accepted * 1000000.0f / testElapsed
      : 0.0f;
  Serial.println("FIFO_CONTINUOUS_RESULT");
  Serial.print("target_windows="); Serial.println(target_windows);
  Serial.print("windows_acquired="); Serial.println(buffers.windows_completed);
  Serial.print("windows_processed="); Serial.println(buffers.windows_processed);
  Serial.print("samples_captured="); Serial.println(buffers.samples_captured);
  Serial.print("samples_dropped="); Serial.println(buffers.samples_dropped);
  Serial.print("buffer_overruns="); Serial.println(buffers.buffer_overruns);
  Serial.print("fifo_overruns="); Serial.println(fifoOverruns);
  Serial.print("fifo_words_read="); Serial.println(wire.words_read);
  Serial.print("fifo_frames_read="); Serial.println(fifoAssembler.counters().frames_accepted);
  Serial.print("fifo_batches="); Serial.println(batches);
  Serial.print("fifo_short_reads="); Serial.println(wire.short_reads);
  Serial.print("frame_alignment_errors="); Serial.println(alignmentErrors);
  Serial.print("read_errors="); Serial.println(readErrors + wire.read_errors);
  Serial.print("effective_sample_rate_hz="); Serial.println(effectiveRate, 3);
  Serial.print("maximum_fifo_occupancy_words="); Serial.println(maximumOccupancyWords);
  Serial.print("average_fifo_drain_us=");
  Serial.println(drainTiming.calls ? drainTiming.sum_us / drainTiming.calls : 0);
  Serial.print("maximum_fifo_drain_us="); Serial.println(drainTiming.maximum_us);
  Serial.print("processing_average_us=");
  Serial.println(processingTiming.calls ? processingTiming.sum_us / processingTiming.calls : 0);
  Serial.print("processing_max_us="); Serial.println(processingTiming.maximum_us);
  Serial.print("test_elapsed_us="); Serial.println(testElapsed);
  Serial.println("FIFO_CONTINUOUS_END");
}

void runProcessingTest(bool printSpectrum) {
  TimingResult timing = {};
  VibrationWindowResult result = {};
  if (!acquireAndProcess(&result, &timing)) {
    printTiming(timing, "FFT_TEST");
    Serial.println("error=window_acquisition_or_processing_failed");
    return;
  }
  printTiming(timing, "FFT_TEST");
  Serial.print("configured_sample_rate_hz="); Serial.println(result.configured_sample_rate_hz, 3);
  Serial.print("effective_sample_rate_hz="); Serial.println(result.effective_sample_rate_hz, 3);
  Serial.print("sample_count="); Serial.println(result.sample_count);
  Serial.print("window_duration_s="); Serial.println(result.sample_count / result.effective_sample_rate_hz, 6);
  Serial.print("frequency_resolution_hz="); Serial.println(result.effective_sample_rate_hz / result.sample_count, 6);
  Serial.print("high_pass_cutoff_hz="); Serial.println(result.high_pass_cutoff_hz, 3);
  Serial.print("feature_processing_us="); Serial.println(result.feature_processing_us);
  Serial.print("fft_processing_us="); Serial.println(result.fft_processing_us);
  Serial.print("total_window_processing_us="); Serial.println(result.total_processing_us);
  printAxis("accel_x_dynamic_g", result.accel_x);
  printAxis("accel_y_dynamic_g", result.accel_y);
  printAxis("accel_z_dynamic_g", result.accel_z);
  printAxis("gyro_x_dynamic_dps", result.gyro_x);
  printAxis("gyro_y_dynamic_dps", result.gyro_y);
  printAxis("gyro_z_dynamic_dps", result.gyro_z);
  printFrequency("accel_x_dynamic_g", result.accel_x_frequency);
  printFrequency("accel_y_dynamic_g", result.accel_y_frequency);
  printFrequency("accel_z_dynamic_g", result.accel_z_frequency);
  if (printSpectrum) {
    // The processor's retained spectrum is the last analyzed axis: accel Z.
    Serial.println("SPECTRUM signal=accel_z_dynamic_g columns=frequency_hz,amplitude_g");
    const float* amplitudes = processor.lastSpectrum();
    for (size_t bin = 1; bin < processor.spectrumBinCount(); ++bin) {
      Serial.print(bin * result.effective_sample_rate_hz / result.sample_count, 6);
      Serial.print(',');
      Serial.println(amplitudes[bin], 8);
    }
    Serial.println("SPECTRUM_END");
  }
}

void printStatus() {
  uint8_t whoAmI = 0;
  vibrationImu.readRegister(&whoAmI, LSM6DS3_ACC_GYRO_WHO_AM_I_REG);
  Serial.println("VIBRATION_DIAGNOSTIC");
  Serial.println("imu=LSM6DS3_or_LSM6DS3TR-C");
  Serial.print("who_am_i=0x"); Serial.println(whoAmI, HEX);
  Serial.print("initialization_ok="); Serial.println(imuReady ? 1 : 0);
  Serial.println("acquisition=single_12_byte_register_region_read");
  Serial.println("sample_storage=interleaved_raw_int16");
  Serial.print("accel_range_g=+-"); Serial.println(vibrationImu.settings.accelRange);
  Serial.print("accel_configured_odr_hz="); Serial.println(vibrationImu.settings.accelSampleRate);
  Serial.print("accel_configured_bandwidth_hz="); Serial.println(vibrationImu.settings.accelBandWidth);
  Serial.println("accel_units=dynamic_g_after_high_pass");
  Serial.print("gyro_range_dps=+-"); Serial.println(vibrationImu.settings.gyroRange);
  Serial.print("gyro_configured_odr_hz="); Serial.println(vibrationImu.settings.gyroSampleRate);
  Serial.println("gyro_units=degrees_per_second");
  Serial.print("high_pass_cutoff_hz="); Serial.println(kHighPassCutoffHz, 3);
  Serial.print("minimum_dominant_frequency_hz="); Serial.println(kMinimumDominantFrequencyHz, 3);
  Serial.println("fft_window=hann fft_size=256");
  Serial.println("fifo_mode=diagnostic_commands_only");
  Serial.println("fifo_frame=gyro_x,gyro_y,gyro_z,accel_x,accel_y,accel_z");
  Serial.println("fifo_interrupt_route=none_int1_and_int2_unconnected");
  Serial.println("fifo_transport=combined_write_read_one_transaction_per_word");
  Serial.println("fifo_wire_buffer_bytes=64 fifo_safe_payload_bytes=2");
  Serial.print("sample_buffer_bytes="); Serial.println(sizeof(sampleBuffer));
  Serial.println("double_buffer_bytes=6144");
  Serial.println("continuous_strategy=fifo_hardware_buffer_plus_double_buffer");
  Serial.println("imu_i2c_bus=Wire1 pins=PB2_SDA1,PB3_SCL1");
  Serial.print("i2c_clock_hz="); Serial.println(kDiagnosticI2cClockHz);
  Serial.println("commands=STATUS,STREAM,RATE_TEST,BUFFER_TEST,FEATURE_TEST,FFT_TEST,SPECTRUM,CONTINUOUS_TEST,CONTINUOUS_LONG_TEST,FIFO_TEST,I2C_TIMING_TEST,FIFO_TRANSPORT_TEST,FIFO_CONTINUOUS_TEST,FIFO_CONTINUOUS_LONG_TEST,STOP");
}

void handleCommand(String command) {
  command.trim(); command.toUpperCase();
  if (command == "STATUS") printStatus();
  else if (command == "RATE_TEST") printTiming(acquire(kRateTestSamples, false), "RATE_TEST");
  else if (command == "BUFFER_TEST" || command == "FEATURE_TEST" || command == "FFT_TEST") runProcessingTest(false);
  else if (command == "SPECTRUM") runProcessingTest(true);
  else if (command == "CONTINUOUS_TEST") runContinuousTest(10);
  else if (command == "CONTINUOUS_LONG_TEST") runContinuousTest(100);
  else if (command == "FIFO_TEST") runFifoValidation();
  else if (command == "I2C_TIMING_TEST") runI2cTimingTest();
  else if (command == "FIFO_TRANSPORT_TEST") runFifoTransportTest();
  else if (command == "FIFO_CONTINUOUS_TEST") runFifoContinuousTest(10);
  else if (command == "FIFO_CONTINUOUS_LONG_TEST") runFifoContinuousTest(100);
  else if (command == "STREAM") { streamEnabled = true; Serial.println("stream=started serial_output_affects_timing=1"); }
  else if (command == "STOP") { streamEnabled = false; Serial.println("stream=stopped"); }
  else Serial.println("error=unknown_command");
}

void setup() {
  Serial.begin(115200);
  pinMode(IMU_POWER_PIN, OUTPUT);
  digitalWrite(IMU_POWER_PIN, HIGH);
  delay(300);
  vibrationImu.settings.accelRange = 16;
  vibrationImu.settings.accelSampleRate = kConfiguredOdrHz;
  vibrationImu.settings.accelBandWidth = 100;
  vibrationImu.settings.gyroRange = 2000;
  vibrationImu.settings.gyroSampleRate = kConfiguredOdrHz;
  imuReady = vibrationImu.begin() == IMU_SUCCESS && processor.valid();
  // Seeed's XIAO MG24 Sense library binds the onboard IMU to Wire1 (PB2/PB3).
  Wire1.setClock(kDiagnosticI2cClockHz);
  printStatus();
}

void loop() {
  if (Serial.available()) handleCommand(Serial.readStringUntil('\n'));
  if (!imuReady || !streamEnabled || !coherentReader.dataReady()) return;
  ImuRawSample raw = {};
  if (!coherentReader.read(&raw)) return;
  const uint32_t timestampUs = micros();
  Serial.print("VIBRATION_SAMPLE timestamp_us="); Serial.print(timestampUs);
  Serial.print(" accel_x_g="); Serial.print(raw.accel_x * kAccelGPerCount, 6);
  Serial.print(" accel_y_g="); Serial.print(raw.accel_y * kAccelGPerCount, 6);
  Serial.print(" accel_z_g="); Serial.print(raw.accel_z * kAccelGPerCount, 6);
  Serial.print(" gyro_x_dps="); Serial.print(raw.gyro_x * kGyroDpsPerCount, 6);
  Serial.print(" gyro_y_dps="); Serial.print(raw.gyro_y * kGyroDpsPerCount, 6);
  Serial.print(" gyro_z_dps="); Serial.println(raw.gyro_z * kGyroDpsPerCount, 6);
}
