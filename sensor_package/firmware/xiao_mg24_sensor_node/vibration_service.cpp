#include "vibration_service.h"

namespace {

constexpr uint8_t kImuAddress = 0x6A;
constexpr uint8_t kFifoCtrl1 = LSM6DS3_ACC_GYRO_FIFO_CTRL1;
constexpr uint8_t kFifoCtrl2 = LSM6DS3_ACC_GYRO_FIFO_CTRL2;
constexpr uint8_t kFifoCtrl3 = LSM6DS3_ACC_GYRO_FIFO_CTRL3;
constexpr uint8_t kFifoCtrl4 = LSM6DS3_ACC_GYRO_FIFO_CTRL4;
constexpr uint8_t kFifoCtrl5 = LSM6DS3_ACC_GYRO_FIFO_CTRL5;
constexpr uint8_t kFifoDataLow = LSM6DS3_ACC_GYRO_FIFO_DATA_OUT_L;
constexpr float kAccelGPerCount = 16.0f / 32768.0f;
constexpr float kGyroDpsPerCount = 2000.0f / 32768.0f;
constexpr float kHighPassCutoffHz = 2.0f;
constexpr float kMinimumDominantFrequencyHz = 5.0f;

seed_mg24::VibrationProcessorConfig makeProcessorConfig() {
  return {416.0f, kHighPassCutoffHz, kMinimumDominantFrequencyHz,
          kAccelGPerCount, kGyroDpsPerCount};
}

}  // namespace

ProductionVibrationService::ProductionVibrationService(LSM6DS3& imu,
                                                       TwoWire& wire)
    : imu_(imu),
      reader_(wire, kImuAddress, kFifoDataLow),
      buffers_(),
      assembler_(buffers_),
      processor_(makeProcessorConfig(), micros),
      runtime_(),
      latest_{},
      timing_{},
      fifo_words_{},
      latest_raw_sample_{},
      has_latest_raw_sample_(false),
      acquisition_reference_us_(0),
      next_sample_timestamp_us_(0),
      accepted_frames_(0),
      fifo_overrun_count_(0),
      alignment_error_count_(0),
      service_read_error_count_(0),
      last_service_us_(0) {}

bool ProductionVibrationService::begin() {
  runtime_.reset();
  latest_ = ProductionVibrationResult{};
  latest_.validity = seed_mg24::VibrationResultValidity::INITIALIZING;
  timing_ = ProductionVibrationTiming{};
  buffers_.reset();
  assembler_.reset();
  reader_.resetCounters();
  processor_.resetFilterState();
  accepted_frames_ = 0;
  fifo_overrun_count_ = 0;
  alignment_error_count_ = 0;
  service_read_error_count_ = 0;
  last_service_us_ = 0;
  has_latest_raw_sample_ = false;
  if (!processor_.valid() || !configureFifo()) {
    markFault(seed_mg24::VibrationResultValidity::UNAVAILABLE, true);
    return false;
  }
  acquisition_reference_us_ = micros();
  next_sample_timestamp_us_ = acquisition_reference_us_;
  timing_.effective_sample_rate_hz = kFifoNominalRateHz;
  runtime_.markReady();
  runtime_.markAcquiring();
  return true;
}

bool ProductionVibrationService::configureFifo() {
  imu_.fifoEnd();
  const uint8_t values[5] = {
      static_cast<uint8_t>(kBatchWords & 0xFF),
      static_cast<uint8_t>((kBatchWords >> 8) & 0x0F),
      0x09,  // gyro and accel, no decimation
      0x00,  // no third/fourth dataset
      0x36,  // FIFO ODR 400 Hz, continuous mode
  };
  const uint8_t registers[5] = {
      kFifoCtrl1, kFifoCtrl2, kFifoCtrl3, kFifoCtrl4, kFifoCtrl5};
  for (size_t index = 0; index < 5; ++index) {
    if (imu_.writeRegister(registers[index], values[index]) != IMU_SUCCESS) {
      return false;
    }
  }
  for (size_t index = 0; index < 5; ++index) {
    uint8_t readback = 0;
    if (imu_.readRegister(&readback, registers[index]) != IMU_SUCCESS ||
        readback != values[index]) return false;
  }
  return true;
}

bool ProductionVibrationService::readFifoStatus(
    uint16_t* word_count, uint16_t* pattern, bool* overrun) {
  // Match the physically validated diagnostic path. A four-byte sequential
  // status read was fast but produced implausible near-full counts in the BLE
  // image; status/pattern are therefore read through the library one register
  // at a time while FIFO data retains the validated combined word transport.
  const uint16_t status = imu_.fifoGetStatus();
  uint8_t pattern_low = 0;
  uint8_t pattern_high = 0;
  if (imu_.readRegister(&pattern_low, LSM6DS3_ACC_GYRO_FIFO_STATUS3) != IMU_SUCCESS ||
      imu_.readRegister(&pattern_high, LSM6DS3_ACC_GYRO_FIFO_STATUS4) != IMU_SUCCESS) {
    service_read_error_count_++;
    return false;
  }
  *word_count = status & 0x0FFF;
  *overrun = (status & 0x4000) != 0;
  *pattern = static_cast<uint16_t>(pattern_low) |
      (static_cast<uint16_t>(pattern_high & 0x03) << 8);
  return true;
}

bool ProductionVibrationService::drainBatch(uint32_t now_us) {
  const uint32_t started = micros();
  if (!reader_.readWords(fifo_words_, kBatchWords, kBatchWords)) {
    service_read_error_count_++;
    markFault(seed_mg24::VibrationResultValidity::READ_ERROR);
    return false;
  }
  seed_mg24::RawImuFifoFrame latest_frame = {};
  if (!seed_mg24::parseFifoFrame(
          fifo_words_ + kBatchWords - kWordsPerFrame,
          kWordsPerFrame, &latest_frame)) {
    alignment_error_count_++;
    markFault(seed_mg24::VibrationResultValidity::ALIGNMENT_ERROR, true);
    return false;
  }
  latest_raw_sample_.gyro_x = latest_frame.gyro_x;
  latest_raw_sample_.gyro_y = latest_frame.gyro_y;
  latest_raw_sample_.gyro_z = latest_frame.gyro_z;
  latest_raw_sample_.accel_x = latest_frame.accel_x;
  latest_raw_sample_.accel_y = latest_frame.accel_y;
  latest_raw_sample_.accel_z = latest_frame.accel_z;
  has_latest_raw_sample_ = true;
  const uint32_t duration = micros() - started;
  timing_.fifo_drain_sum_us += duration;
  timing_.fifo_drain_count++;
  if (duration > timing_.fifo_drain_max_us) timing_.fifo_drain_max_us = duration;

  if (accepted_frames_ == 0) {
    next_sample_timestamp_us_ = now_us - static_cast<uint32_t>(
        (kBatchFrames - 1) * 1000000.0f / kFifoNominalRateHz);
    acquisition_reference_us_ = next_sample_timestamp_us_;
  } else {
    const uint32_t elapsed = now_us - acquisition_reference_us_;
    if (elapsed > 0) {
      const float observed = static_cast<float>(accepted_frames_ + kBatchFrames - 1) *
          1000000.0f / elapsed;
      if (observed > 350.0f && observed < 500.0f) {
        timing_.effective_sample_rate_hz =
            0.9f * timing_.effective_sample_rate_hz + 0.1f * observed;
      }
    }
  }
  const uint32_t interval_us = static_cast<uint32_t>(
      1000000.0f / timing_.effective_sample_rate_hz + 0.5f);
  if (!assembler_.appendBatch(fifo_words_, kBatchWords,
                              next_sample_timestamp_us_, interval_us)) {
    markFault(seed_mg24::VibrationResultValidity::BUFFER_OVERRUN);
    return false;
  }
  accepted_frames_ += kBatchFrames;
  next_sample_timestamp_us_ += kBatchFrames * interval_us;
  return true;
}

void ProductionVibrationService::processReadyWindow() {
  seed_mg24::ReadyWindow window = {};
  if (!buffers_.acquireReady(&window)) return;
  seed_mg24::VibrationWindowResult metrics = {};
  const uint32_t started = micros();
  const bool processed = processor_.process(
      window.samples, window.timing.sample_count, &metrics,
      timing_.effective_sample_rate_hz);
  const uint32_t duration = micros() - started;
  timing_.processing_sum_us += duration;
  timing_.processing_count++;
  if (duration > timing_.processing_max_us) timing_.processing_max_us = duration;
  if (!processed) {
    buffers_.release(window.buffer_index);
    markFault(seed_mg24::VibrationResultValidity::INSUFFICIENT_SAMPLES);
    return;
  }
  const uint32_t sequence = runtime_.windowSequence() + 1;
  latest_.window_sequence = sequence;
  latest_.window_start_us = window.timing.window_start_us;
  latest_.window_end_us = window.timing.window_end_us;
  latest_.processed_uptime_ms = millis();
  latest_.validity = seed_mg24::VibrationResultValidity::VALID;
  latest_.metrics = metrics;
  buffers_.release(window.buffer_index);
  runtime_.markValidWindow(sequence);
}

void ProductionVibrationService::refreshHealthCounters() {
  const seed_mg24::DoubleBufferCounters& buffers = buffers_.counters();
  const seed_mg24::FifoWireCounters& wire = reader_.counters();
  seed_mg24::VibrationHealthCounters health = {};
  health.fifo_overruns = fifo_overrun_count_;
  health.buffer_overruns = buffers.buffer_overruns;
  health.alignment_errors = alignment_error_count_;
  health.read_errors = service_read_error_count_ + wire.read_errors;
  health.short_reads = wire.short_reads;
  health.samples_dropped = buffers.samples_dropped;
  health.windows_completed = buffers.windows_completed;
  health.windows_processed = buffers.windows_processed;
  runtime_.updateCounters(health);
}

void ProductionVibrationService::markFault(
    seed_mg24::VibrationResultValidity fault, bool fatal) {
  latest_.validity = fault;
  runtime_.markFault(fault, fatal);
  refreshHealthCounters();
}

void ProductionVibrationService::service() {
  if (!available()) return;
  const uint32_t service_now = micros();
  if (last_service_us_ != 0) {
    const uint32_t gap = service_now - last_service_us_;
    if (gap > timing_.maximum_service_gap_us) {
      timing_.maximum_service_gap_us = gap;
    }
  }
  last_service_us_ = service_now;
  timing_.service_calls++;
  uint8_t batches = 0;
  uint16_t occupancy = 0;
  uint16_t pattern = 0;
  bool overrun = false;
  while (batches < kMaximumBatchesPerService) {
    if (!readFifoStatus(&occupancy, &pattern, &overrun)) {
      markFault(seed_mg24::VibrationResultValidity::READ_ERROR);
      return;
    }
    if (occupancy > timing_.maximum_fifo_occupancy_words) {
      timing_.maximum_fifo_occupancy_words = occupancy;
    }
    if (overrun) {
      fifo_overrun_count_++;
      // An overrun invalidates frame continuity. Latch the subsystem failed
      // rather than repeatedly counting the same FIFO flag or fabricating a
      // valid window; the rest of the node remains operational.
      markFault(seed_mg24::VibrationResultValidity::FIFO_OVERRUN, true);
      return;
    }
    if (occupancy < kBatchWords) break;
    if (pattern != 0) {
      alignment_error_count_++;
      markFault(seed_mg24::VibrationResultValidity::ALIGNMENT_ERROR, true);
      return;
    }
    if (!drainBatch(micros())) return;
    batches++;
  }
  // The loop exits immediately after the final permitted drain, so refresh
  // occupancy once before making the DSP scheduling decision.
  if (batches == kMaximumBatchesPerService &&
      !readFifoStatus(&occupancy, &pattern, &overrun)) {
    markFault(seed_mg24::VibrationResultValidity::READ_ERROR);
    return;
  }
  if (overrun) {
    fifo_overrun_count_++;
    markFault(seed_mg24::VibrationResultValidity::FIFO_OVERRUN, true);
    return;
  }
  if (pattern != 0) {
    alignment_error_count_++;
    markFault(seed_mg24::VibrationResultValidity::ALIGNMENT_ERROR, true);
    return;
  }
  // At four or more batches of backlog, preserve CPU for draining and BLE.
  if (seed_mg24::mayProcessReadyWindow(
          occupancy, kPriorityOccupancyWords)) processReadyWindow();
  refreshHealthCounters();
}

bool ProductionVibrationService::available() const {
  return runtime_.lifecycle() != seed_mg24::VibrationLifecycleState::UNINITIALIZED &&
         runtime_.lifecycle() != seed_mg24::VibrationLifecycleState::FAILED;
}

bool ProductionVibrationService::latestRawSample(
    seed_mg24::ImuRawSample* sample) const {
  if (!sample || !has_latest_raw_sample_) return false;
  *sample = latest_raw_sample_;
  return true;
}

void ProductionVibrationService::printHealth(Stream& output) const {
  const seed_mg24::VibrationHealthCounters& health = runtime_.counters();
  output.print("{\"type\":\"vibration_health\",\"lifecycle\":\"");
  output.print(seed_mg24::vibrationLifecycleName(runtime_.lifecycle()));
  output.print("\",\"validity\":\"");
  output.print(seed_mg24::vibrationValidityName(runtime_.validity()));
  output.print("\",\"window_sequence\":"); output.print(runtime_.windowSequence());
  output.print(",\"samples_captured\":"); output.print(accepted_frames_);
  output.print(",\"samples_dropped\":"); output.print(health.samples_dropped);
  output.print(",\"windows_completed\":"); output.print(health.windows_completed);
  output.print(",\"windows_processed\":"); output.print(health.windows_processed);
  output.print(",\"fifo_overruns\":"); output.print(health.fifo_overruns);
  output.print(",\"buffer_overruns\":"); output.print(health.buffer_overruns);
  output.print(",\"alignment_errors\":"); output.print(health.alignment_errors);
  output.print(",\"read_errors\":"); output.print(health.read_errors);
  output.print(",\"short_reads\":"); output.print(health.short_reads);
  output.print(",\"effective_sample_rate_hz\":");
  output.print(timing_.effective_sample_rate_hz, 3);
  output.print(",\"max_fifo_occupancy_words\":");
  output.print(timing_.maximum_fifo_occupancy_words);
  output.print(",\"average_processing_us\":");
  output.print(timing_.processing_count
      ? timing_.processing_sum_us / timing_.processing_count : 0);
  output.print(",\"maximum_processing_us\":"); output.print(timing_.processing_max_us);
  output.print(",\"fifo_batches\":"); output.print(timing_.fifo_drain_count);
  output.print(",\"average_fifo_drain_us\":");
  output.print(timing_.fifo_drain_count
      ? timing_.fifo_drain_sum_us / timing_.fifo_drain_count : 0);
  output.print(",\"maximum_fifo_drain_us\":"); output.print(timing_.fifo_drain_max_us);
  output.print(",\"service_calls\":"); output.print(timing_.service_calls);
  output.print(",\"maximum_service_gap_us\":");
  output.print(timing_.maximum_service_gap_us);
  output.print(",\"ble_payload\":\"unchanged_protocol_1.0.0\"}");
  output.println();
}
