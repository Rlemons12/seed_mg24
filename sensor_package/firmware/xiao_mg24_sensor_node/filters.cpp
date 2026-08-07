#include "filters.h"

#include <string.h>

NumericFilter::NumericFilter() { configure(FilterType::None, 1); }

void NumericFilter::configure(FilterType type, uint8_t window, float ema_alpha) {
  type_ = type;
  window_ = window < 1 ? 1 : (window > MAX_FILTER_WINDOW ? MAX_FILTER_WINDOW : window);
  alpha_ = ema_alpha < 0.0f ? 0.0f : (ema_alpha > 1.0f ? 1.0f : ema_alpha);
  reset();
}

void NumericFilter::reset() {
  count_ = 0; index_ = 0; ema_ = 0.0f; initialized_ = false;
  memset(values_, 0, sizeof(values_));
}

float NumericFilter::update(float value) {
  if (type_ == FilterType::None) return value;
  if (type_ == FilterType::Exponential) {
    ema_ = initialized_ ? alpha_ * value + (1.0f - alpha_) * ema_ : value;
    initialized_ = true; return ema_;
  }
  values_[index_] = value; index_ = (index_ + 1) % window_; if (count_ < window_) count_++;
  float work[MAX_FILTER_WINDOW]; float sum = 0.0f;
  for (uint8_t i = 0; i < count_; ++i) { work[i] = values_[i]; sum += values_[i]; }
  if (type_ == FilterType::MovingAverage) return sum / count_;
  for (uint8_t i = 1; i < count_; ++i) { float current = work[i]; int8_t j = i - 1; while (j >= 0 && work[j] > current) { work[j + 1] = work[j]; --j; } work[j + 1] = current; }
  return count_ % 2 ? work[count_ / 2] : (work[count_ / 2 - 1] + work[count_ / 2]) * 0.5f;
}

DigitalDebounceFilter::DigitalDebounceFilter() { configure(2); reset(false); }
void DigitalDebounceFilter::configure(uint8_t required_samples) { required_ = required_samples < 1 ? 1 : required_samples; count_ = 0; }
void DigitalDebounceFilter::reset(bool initial) { output_ = initial; candidate_ = initial; count_ = 0; }
bool DigitalDebounceFilter::update(bool value) {
  if (value == output_) { candidate_ = value; count_ = 0; return output_; }
  if (value != candidate_) { candidate_ = value; count_ = 1; } else if (count_ < required_) { count_++; }
  if (count_ >= required_) { output_ = candidate_; count_ = 0; }
  return output_;
}
