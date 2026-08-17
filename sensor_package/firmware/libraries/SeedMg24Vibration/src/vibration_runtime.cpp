#include "vibration_runtime.h"

namespace seed_mg24 {

VibrationRuntimeState::VibrationRuntimeState() { reset(); }

void VibrationRuntimeState::reset() {
  lifecycle_ = VibrationLifecycleState::UNINITIALIZED;
  validity_ = VibrationResultValidity::INITIALIZING;
  window_sequence_ = 0;
  counters_ = VibrationHealthCounters{};
}

void VibrationRuntimeState::markReady() {
  if (lifecycle_ != VibrationLifecycleState::FAILED) {
    lifecycle_ = VibrationLifecycleState::READY;
    validity_ = VibrationResultValidity::INITIALIZING;
  }
}

void VibrationRuntimeState::markAcquiring() {
  if (lifecycle_ != VibrationLifecycleState::FAILED) {
    lifecycle_ = VibrationLifecycleState::ACQUIRING;
  }
}

void VibrationRuntimeState::markValidWindow(uint32_t sequence) {
  if (lifecycle_ == VibrationLifecycleState::FAILED) return;
  lifecycle_ = VibrationLifecycleState::ACQUIRING;
  validity_ = VibrationResultValidity::VALID;
  window_sequence_ = sequence;
}

void VibrationRuntimeState::markFault(VibrationResultValidity fault,
                                      bool fatal) {
  validity_ = fault;
  lifecycle_ = fatal ? VibrationLifecycleState::FAILED
                     : VibrationLifecycleState::DEGRADED;
}

void VibrationRuntimeState::updateCounters(
    const VibrationHealthCounters& counters) {
  counters_ = counters;
}

const char* vibrationLifecycleName(VibrationLifecycleState state) {
  switch (state) {
    case VibrationLifecycleState::UNINITIALIZED: return "uninitialized";
    case VibrationLifecycleState::READY: return "ready";
    case VibrationLifecycleState::ACQUIRING: return "acquiring";
    case VibrationLifecycleState::DEGRADED: return "degraded";
    case VibrationLifecycleState::FAILED: return "failed";
  }
  return "failed";
}

const char* vibrationValidityName(VibrationResultValidity validity) {
  switch (validity) {
    case VibrationResultValidity::INITIALIZING: return "initializing";
    case VibrationResultValidity::VALID: return "valid";
    case VibrationResultValidity::INSUFFICIENT_SAMPLES: return "insufficient_samples";
    case VibrationResultValidity::FIFO_OVERRUN: return "fifo_overrun";
    case VibrationResultValidity::BUFFER_OVERRUN: return "buffer_overrun";
    case VibrationResultValidity::READ_ERROR: return "read_error";
    case VibrationResultValidity::ALIGNMENT_ERROR: return "alignment_error";
    case VibrationResultValidity::UNAVAILABLE: return "unavailable";
  }
  return "unavailable";
}

bool shouldPrioritizeFifo(uint16_t occupancy_words,
                          uint16_t priority_threshold_words) {
  return occupancy_words >= priority_threshold_words;
}

bool mayProcessReadyWindow(uint16_t occupancy_words,
                           uint16_t priority_threshold_words) {
  return !shouldPrioritizeFifo(occupancy_words, priority_threshold_words);
}

}  // namespace seed_mg24
