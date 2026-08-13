#ifndef SEED_MG24_VIBRATION_FIFO_WIRE_H_
#define SEED_MG24_VIBRATION_FIFO_WIRE_H_

#include <Arduino.h>
#include <Wire.h>

namespace seed_mg24 {

struct FifoWireCounters {
  uint32_t transactions;
  uint32_t words_read;
  uint32_t short_reads;
  uint32_t read_errors;
};

class Lsm6ds3FifoWireReader {
 public:
  Lsm6ds3FifoWireReader(TwoWire& wire, uint8_t address,
                        uint8_t fifo_data_low_register);
  void resetCounters();
  bool readRegisterCombined(uint8_t register_address, uint8_t* destination,
                            size_t byte_count);
  bool readWords(int16_t* destination, size_t destination_capacity_words,
                 size_t word_count);
  const FifoWireCounters& counters() const { return counters_; }

 private:
  TwoWire& wire_;
  uint8_t address_;
  uint8_t fifo_data_low_register_;
  FifoWireCounters counters_;
};

}  // namespace seed_mg24

#endif
