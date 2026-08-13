#include "vibration_double_buffer.h"

namespace seed_mg24 {

VibrationDoubleBuffer::VibrationDoubleBuffer() { reset(); }

void VibrationDoubleBuffer::reset() {
  counters_ = DoubleBufferCounters{};
  for (uint8_t index = 0; index < 2; ++index) {
    buffers_[index].timing = WindowTiming{};
    buffers_[index].state = BufferState::FREE;
  }
  filling_index_ = -1;
  startFilling(0);
}

void VibrationDoubleBuffer::startFilling(uint8_t index) {
  buffers_[index].timing = WindowTiming{};
  buffers_[index].state = BufferState::FILLING;
  filling_index_ = static_cast<int8_t>(index);
}

int8_t VibrationDoubleBuffer::findFree() const {
  for (uint8_t index = 0; index < 2; ++index) {
    if (buffers_[index].state == BufferState::FREE) return static_cast<int8_t>(index);
  }
  return -1;
}

bool VibrationDoubleBuffer::append(const ImuRawSample& sample,
                                   uint32_t timestamp_us) {
  if (filling_index_ < 0) {
    const int8_t free_index = findFree();
    if (free_index < 0) {
      counters_.samples_dropped++;
      return false;
    }
    startFilling(static_cast<uint8_t>(free_index));
  }
  Buffer& buffer = buffers_[filling_index_];
  const uint16_t position = buffer.timing.sample_count;
  if (position == 0) buffer.timing.window_start_us = timestamp_us;
  buffer.samples[position] = sample;
  buffer.timing.sample_count++;
  buffer.timing.window_end_us = timestamp_us;
  counters_.samples_captured++;
  if (buffer.timing.sample_count != kVibrationWindowSamples) return true;

  const uint32_t elapsed = buffer.timing.window_end_us - buffer.timing.window_start_us;
  buffer.timing.effective_sample_rate_hz = elapsed > 0
      ? static_cast<float>(kVibrationWindowSamples - 1) * 1000000.0f / elapsed
      : 0.0f;
  buffer.state = BufferState::READY;
  counters_.windows_completed++;
  counters_.buffer_swaps++;
  filling_index_ = -1;
  const int8_t free_index = findFree();
  if (free_index >= 0) {
    startFilling(static_cast<uint8_t>(free_index));
  } else {
    counters_.buffer_overruns++;
  }
  return true;
}

bool VibrationDoubleBuffer::acquireReady(ReadyWindow* window) {
  if (window == 0) return false;
  for (uint8_t index = 0; index < 2; ++index) {
    Buffer& buffer = buffers_[index];
    if (buffer.state != BufferState::READY) continue;
    buffer.state = BufferState::PROCESSING;
    window->samples = buffer.samples;
    window->timing = buffer.timing;
    window->buffer_index = index;
    return true;
  }
  return false;
}

bool VibrationDoubleBuffer::release(uint8_t buffer_index) {
  if (buffer_index >= 2 || buffers_[buffer_index].state != BufferState::PROCESSING) {
    return false;
  }
  buffers_[buffer_index].state = BufferState::FREE;
  counters_.windows_processed++;
  if (filling_index_ < 0) startFilling(buffer_index);
  return true;
}

BufferState VibrationDoubleBuffer::state(uint8_t index) const {
  return index < 2 ? buffers_[index].state : BufferState::FREE;
}

}  // namespace seed_mg24
