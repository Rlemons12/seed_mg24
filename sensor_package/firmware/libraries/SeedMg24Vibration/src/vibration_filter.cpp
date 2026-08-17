#include "vibration_filter.h"

#include <math.h>

namespace seed_mg24 {

FirstOrderHighPass::FirstOrderHighPass()
    : alpha_(0.0f), previous_input_(0.0f), previous_output_(0.0f), initialized_(false) {}

bool FirstOrderHighPass::configure(float sample_rate_hz, float cutoff_hz) {
  if (!(sample_rate_hz > 0.0f) || !(cutoff_hz > 0.0f) ||
      cutoff_hz >= sample_rate_hz * 0.5f) return false;
  const float dt = 1.0f / sample_rate_hz;
  const float rc = 1.0f / (2.0f * 3.14159265358979323846f * cutoff_hz);
  alpha_ = rc / (rc + dt);
  return true;
}

void FirstOrderHighPass::reset(float initial_input) {
  previous_input_ = initial_input;
  previous_output_ = 0.0f;
  initialized_ = true;
}

float FirstOrderHighPass::apply(float input) {
  if (!initialized_) {
    reset(input);
    return 0.0f;
  }
  const float output = alpha_ * (previous_output_ + input - previous_input_);
  previous_input_ = input;
  previous_output_ = output;
  return output;
}

}  // namespace seed_mg24
