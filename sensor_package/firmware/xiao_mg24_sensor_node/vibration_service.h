#pragma once

#include <Arduino.h>
#include <LSM6DS3.h>
#include <Wire.h>

#include <vibration_double_buffer.h>
#include <vibration_fifo.h>
#include <vibration_fifo_wire.h>
#include <vibration_processor.h>
#include <vibration_runtime.h>

struct ProductionVibrationResult {
  uint32_t window_sequence;
  uint32_t window_start_us;
  uint32_t window_end_us;
  uint32_t processed_uptime_ms;
  seed_mg24::VibrationResultValidity validity;
  seed_mg24::VibrationWindowResult metrics;
};

struct ProductionVibrationTiming {
  uint32_t maximum_fifo_occupancy_words;
  uint64_t fifo_drain_sum_us;
  uint32_t fifo_drain_count;
  uint32_t fifo_drain_max_us;
  uint64_t processing_sum_us;
  uint32_t processing_count;
  uint32_t processing_max_us;
  uint32_t service_calls;
  uint32_t maximum_service_gap_us;
  float effective_sample_rate_hz;
};

class ProductionVibrationService {
 public:
  ProductionVibrationService(LSM6DS3& imu, TwoWire& wire);
  bool begin();
  void service();
  void printHealth(Stream& output) const;

  const seed_mg24::VibrationRuntimeState& runtime() const { return runtime_; }
  const ProductionVibrationResult& latest() const { return latest_; }
  const ProductionVibrationTiming& timing() const { return timing_; }
  bool latestRawSample(seed_mg24::ImuRawSample* sample) const;
  bool available() const;

 private:
  static constexpr uint16_t kWordsPerFrame = 6;
  static constexpr uint16_t kBatchFrames = 16;
  static constexpr uint16_t kBatchWords = kBatchFrames * kWordsPerFrame;
  static constexpr uint16_t kPriorityOccupancyWords = kBatchWords * 4;
  // A 16-batch bounded catch-up can drain 1,536 words. At the validated
  // transport rate it reduces backlog despite concurrent 2,400-word/s fill,
  // while remaining far below an unbounded drain loop.
  static constexpr uint8_t kMaximumBatchesPerService = 16;
  static constexpr float kConfiguredSampleRateHz = 416.0f;
  static constexpr float kFifoNominalRateHz = 400.0f;

  bool configureFifo();
  bool readFifoStatus(uint16_t* word_count, uint16_t* pattern,
                      bool* overrun);
  bool drainBatch(uint32_t now_us);
  void processReadyWindow();
  void refreshHealthCounters();
  void markFault(seed_mg24::VibrationResultValidity fault, bool fatal = false);

  LSM6DS3& imu_;
  seed_mg24::Lsm6ds3FifoWireReader reader_;
  seed_mg24::VibrationDoubleBuffer buffers_;
  seed_mg24::FifoWindowAssembler assembler_;
  seed_mg24::VibrationProcessor processor_;
  seed_mg24::VibrationRuntimeState runtime_;
  ProductionVibrationResult latest_;
  ProductionVibrationTiming timing_;
  int16_t fifo_words_[kBatchWords];
  seed_mg24::ImuRawSample latest_raw_sample_;
  bool has_latest_raw_sample_;
  uint32_t acquisition_reference_us_;
  uint32_t next_sample_timestamp_us_;
  uint32_t accepted_frames_;
  uint32_t fifo_overrun_count_;
  uint32_t alignment_error_count_;
  uint32_t service_read_error_count_;
  uint32_t last_service_us_;
};
