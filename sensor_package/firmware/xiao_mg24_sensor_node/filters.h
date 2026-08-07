#pragma once

#include <stdint.h>
#include "sensor_config.h"

class NumericFilter {
 public:
  NumericFilter();
  void configure(FilterType type, uint8_t window, float ema_alpha = 0.5f);
  void reset();
  float update(float value);

 private:
  FilterType type_;
  uint8_t window_;
  uint8_t count_;
  uint8_t index_;
  float alpha_;
  float values_[MAX_FILTER_WINDOW];
  float ema_;
  bool initialized_;
};

class DigitalDebounceFilter {
 public:
  DigitalDebounceFilter();
  void configure(uint8_t required_samples);
  void reset(bool initial = false);
  bool update(bool value);

 private:
  uint8_t required_;
  uint8_t count_;
  bool candidate_;
  bool output_;
};
