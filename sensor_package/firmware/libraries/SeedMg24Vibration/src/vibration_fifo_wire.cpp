#include "vibration_fifo_wire.h"

namespace seed_mg24 {

Lsm6ds3FifoWireReader::Lsm6ds3FifoWireReader(
    TwoWire& wire, uint8_t address, uint8_t fifo_data_low_register)
    : wire_(wire),
      address_(address),
      fifo_data_low_register_(fifo_data_low_register),
      counters_{} {}

void Lsm6ds3FifoWireReader::resetCounters() { counters_ = FifoWireCounters{}; }

bool Lsm6ds3FifoWireReader::readRegisterCombined(
    uint8_t register_address, uint8_t* destination, size_t byte_count) {
  if (destination == 0 || byte_count == 0 || byte_count > 64) {
    counters_.read_errors++;
    return false;
  }
  wire_.beginTransmission(address_);
  if (wire_.write(register_address) != 1) {
    wire_.endTransmission(true);
    counters_.read_errors++;
    return false;
  }
  // Silicon Labs Wire consumes its pending TX bytes as the command phase of
  // one I2C_FLAG_WRITE_READ transfer. The explicit stop overload also releases
  // Wire's transaction mutex after that combined transfer.
  const size_t received = wire_.requestFrom(address_, byte_count, true);
  counters_.transactions++;
  if (received != byte_count) {
    counters_.short_reads++;
    return false;
  }
  for (size_t index = 0; index < byte_count; ++index) {
    const int value = wire_.read();
    if (value < 0) {
      counters_.short_reads++;
      return false;
    }
    destination[index] = static_cast<uint8_t>(value);
  }
  return true;
}

bool Lsm6ds3FifoWireReader::readWords(
    int16_t* destination, size_t destination_capacity_words,
    size_t word_count) {
  if (destination == 0 || word_count == 0 ||
      word_count > destination_capacity_words) {
    counters_.read_errors++;
    return false;
  }
  for (size_t index = 0; index < word_count; ++index) {
    uint8_t bytes[2] = {};
    // LSM6DS3/TR-C exposes one FIFO word through 3E/3F. Reading beyond 3F
    // advances into timestamp registers, so every FIFO word needs a fresh
    // combined register-address/write + two-byte/read transaction.
    if (!readRegisterCombined(fifo_data_low_register_, bytes, sizeof(bytes))) {
      return false;
    }
    destination[index] = static_cast<int16_t>(
        static_cast<uint16_t>(bytes[0]) |
        (static_cast<uint16_t>(bytes[1]) << 8));
    counters_.words_read++;
  }
  return true;
}

}  // namespace seed_mg24
