#ifndef SEED_MG24_VIBRATION_ACQUISITION_H_
#define SEED_MG24_VIBRATION_ACQUISITION_H_

#include <LSM6DS3.h>

#include "vibration_types.h"

namespace seed_mg24 {

class Lsm6ds3CoherentReader {
 public:
  explicit Lsm6ds3CoherentReader(LSM6DS3& imu) : imu_(imu) {}
  bool dataReady(bool* accel_ready = 0, bool* gyro_ready = 0);
  bool read(ImuRawSample* sample);

 private:
  static int16_t decodeLittleEndian(const uint8_t* bytes);
  LSM6DS3& imu_;
};

}  // namespace seed_mg24

#endif
