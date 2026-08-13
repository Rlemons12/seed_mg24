#include "vibration_fifo.h"

namespace seed_mg24 {

bool parseFifoFrame(const int16_t* words, size_t word_count,
                    RawImuFifoFrame* frame) {
  if (words == 0 || frame == 0 || word_count < kImuFifoWordsPerFrame) {
    return false;
  }
  frame->gyro_x = words[0];
  frame->gyro_y = words[1];
  frame->gyro_z = words[2];
  frame->accel_x = words[3];
  frame->accel_y = words[4];
  frame->accel_z = words[5];
  return true;
}

ImuRawSample toImuRawSample(const RawImuFifoFrame& frame) {
  ImuRawSample sample = {};
  sample.gyro_x = frame.gyro_x;
  sample.gyro_y = frame.gyro_y;
  sample.gyro_z = frame.gyro_z;
  sample.accel_x = frame.accel_x;
  sample.accel_y = frame.accel_y;
  sample.accel_z = frame.accel_z;
  return sample;
}

FifoWindowAssembler::FifoWindowAssembler(VibrationDoubleBuffer& buffers)
    : buffers_(buffers), counters_{} {}

void FifoWindowAssembler::reset() { counters_ = FifoAssemblyCounters{}; }

bool FifoWindowAssembler::appendBatch(const int16_t* words, size_t word_count,
                                      uint32_t first_sample_us,
                                      uint32_t sample_interval_us) {
  if (words == 0 || word_count == 0 ||
      word_count % kImuFifoWordsPerFrame != 0) {
    counters_.partial_batches_rejected++;
    return false;
  }
  const size_t frame_count = word_count / kImuFifoWordsPerFrame;
  for (size_t index = 0; index < frame_count; ++index) {
    RawImuFifoFrame frame = {};
    if (!parseFifoFrame(words + index * kImuFifoWordsPerFrame,
                        kImuFifoWordsPerFrame, &frame)) {
      counters_.frames_rejected++;
      return false;
    }
    const uint32_t timestamp =
        first_sample_us + static_cast<uint32_t>(index) * sample_interval_us;
    if (!buffers_.append(toImuRawSample(frame), timestamp)) {
      counters_.frames_rejected++;
      return false;
    }
    counters_.frames_accepted++;
  }
  return true;
}

}  // namespace seed_mg24
