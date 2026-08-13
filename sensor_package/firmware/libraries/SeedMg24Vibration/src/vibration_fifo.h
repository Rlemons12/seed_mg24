#ifndef SEED_MG24_VIBRATION_FIFO_H_
#define SEED_MG24_VIBRATION_FIFO_H_

#include <stddef.h>
#include <stdint.h>

#include "vibration_double_buffer.h"

namespace seed_mg24 {

constexpr size_t kImuFifoWordsPerFrame = 6;

struct RawImuFifoFrame {
  int16_t gyro_x;
  int16_t gyro_y;
  int16_t gyro_z;
  int16_t accel_x;
  int16_t accel_y;
  int16_t accel_z;
};

static_assert(sizeof(RawImuFifoFrame) == sizeof(ImuRawSample),
              "FIFO and vibration samples must remain six int16_t values");

bool parseFifoFrame(const int16_t* words, size_t word_count,
                    RawImuFifoFrame* frame);
ImuRawSample toImuRawSample(const RawImuFifoFrame& frame);

struct FifoAssemblyCounters {
  uint32_t frames_accepted;
  uint32_t partial_batches_rejected;
  uint32_t frames_rejected;
};

class FifoWindowAssembler {
 public:
  explicit FifoWindowAssembler(VibrationDoubleBuffer& buffers);
  void reset();
  bool appendBatch(const int16_t* words, size_t word_count,
                   uint32_t first_sample_us, uint32_t sample_interval_us);
  const FifoAssemblyCounters& counters() const { return counters_; }

 private:
  VibrationDoubleBuffer& buffers_;
  FifoAssemblyCounters counters_;
};

}  // namespace seed_mg24

#endif
