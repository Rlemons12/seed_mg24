#include "vibration_acquisition.h"

namespace seed_mg24 {
namespace {

// OUTX_L_G through OUTZ_H_XL are contiguous when register auto-increment is
// enabled by the library. Reading all 12 bytes in one transaction minimizes
// bus overhead and axis skew compared with six independent calls.
constexpr uint8_t kSixAxisFirstRegister = LSM6DS3_ACC_GYRO_OUTX_L_G;
constexpr uint8_t kSixAxisByteCount = 12;
constexpr uint8_t kAccelDataReadyMask = 0x01;
constexpr uint8_t kGyroDataReadyMask = 0x02;

}  // namespace

int16_t Lsm6ds3CoherentReader::decodeLittleEndian(const uint8_t* bytes) {
  return static_cast<int16_t>(
      static_cast<uint16_t>(bytes[0]) |
      (static_cast<uint16_t>(bytes[1]) << 8));
}

bool Lsm6ds3CoherentReader::dataReady(bool* accel_ready, bool* gyro_ready) {
  uint8_t status = 0;
  if (imu_.readRegister(&status, LSM6DS3_ACC_GYRO_STATUS_REG) != IMU_SUCCESS) return false;
  const bool accel = (status & kAccelDataReadyMask) != 0;
  const bool gyro = (status & kGyroDataReadyMask) != 0;
  if (accel_ready != 0) *accel_ready = accel;
  if (gyro_ready != 0) *gyro_ready = gyro;
  return accel && gyro;
}

bool Lsm6ds3CoherentReader::read(ImuRawSample* sample) {
  if (sample == 0) return false;
  uint8_t bytes[kSixAxisByteCount] = {};
  if (imu_.readRegisterRegion(bytes, kSixAxisFirstRegister, kSixAxisByteCount) != IMU_SUCCESS) {
    return false;
  }
  sample->gyro_x = decodeLittleEndian(bytes + 0);
  sample->gyro_y = decodeLittleEndian(bytes + 2);
  sample->gyro_z = decodeLittleEndian(bytes + 4);
  sample->accel_x = decodeLittleEndian(bytes + 6);
  sample->accel_y = decodeLittleEndian(bytes + 8);
  sample->accel_z = decodeLittleEndian(bytes + 10);
  return true;
}

}  // namespace seed_mg24
