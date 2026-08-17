#ifndef SEED_MG24_VIBRATION_DOUBLE_BUFFER_H_
#define SEED_MG24_VIBRATION_DOUBLE_BUFFER_H_

#include "vibration_types.h"

namespace seed_mg24 {

enum class BufferState : uint8_t { FREE, FILLING, READY, PROCESSING };

struct WindowTiming {
  uint32_t window_start_us;
  uint32_t window_end_us;
  uint16_t sample_count;
  float effective_sample_rate_hz;
};

struct DoubleBufferCounters {
  uint32_t samples_captured;
  uint32_t samples_dropped;
  uint32_t windows_completed;
  uint32_t windows_processed;
  uint32_t buffer_swaps;
  uint32_t buffer_overruns;
};

struct ReadyWindow {
  const ImuRawSample* samples;
  WindowTiming timing;
  uint8_t buffer_index;
};

class VibrationDoubleBuffer {
 public:
  VibrationDoubleBuffer();
  void reset();
  bool append(const ImuRawSample& sample, uint32_t timestamp_us);
  bool acquireReady(ReadyWindow* window);
  bool release(uint8_t buffer_index);
  const DoubleBufferCounters& counters() const { return counters_; }
  BufferState state(uint8_t index) const;

 private:
  struct Buffer {
    ImuRawSample samples[kVibrationWindowSamples];
    WindowTiming timing;
    BufferState state;
  };

  void startFilling(uint8_t index);
  int8_t findFree() const;

  Buffer buffers_[2];
  int8_t filling_index_;
  DoubleBufferCounters counters_;
};

}  // namespace seed_mg24

#endif
