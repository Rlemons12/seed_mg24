#ifndef SEED_MG24_VIBRATION_RUNTIME_H_
#define SEED_MG24_VIBRATION_RUNTIME_H_

#include <stdint.h>

namespace seed_mg24 {

enum class VibrationLifecycleState : uint8_t {
  UNINITIALIZED,
  READY,
  ACQUIRING,
  DEGRADED,
  FAILED,
};

enum class VibrationResultValidity : uint8_t {
  INITIALIZING,
  VALID,
  INSUFFICIENT_SAMPLES,
  FIFO_OVERRUN,
  BUFFER_OVERRUN,
  READ_ERROR,
  ALIGNMENT_ERROR,
  UNAVAILABLE,
};

struct VibrationHealthCounters {
  uint32_t fifo_overruns;
  uint32_t buffer_overruns;
  uint32_t alignment_errors;
  uint32_t read_errors;
  uint32_t short_reads;
  uint32_t samples_dropped;
  uint32_t windows_completed;
  uint32_t windows_processed;
};

class VibrationRuntimeState {
 public:
  VibrationRuntimeState();
  void reset();
  void markReady();
  void markAcquiring();
  void markValidWindow(uint32_t sequence);
  void markFault(VibrationResultValidity fault, bool fatal = false);
  void updateCounters(const VibrationHealthCounters& counters);

  VibrationLifecycleState lifecycle() const { return lifecycle_; }
  VibrationResultValidity validity() const { return validity_; }
  uint32_t windowSequence() const { return window_sequence_; }
  const VibrationHealthCounters& counters() const { return counters_; }

 private:
  VibrationLifecycleState lifecycle_;
  VibrationResultValidity validity_;
  uint32_t window_sequence_;
  VibrationHealthCounters counters_;
};

const char* vibrationLifecycleName(VibrationLifecycleState state);
const char* vibrationValidityName(VibrationResultValidity validity);
bool shouldPrioritizeFifo(uint16_t occupancy_words,
                          uint16_t priority_threshold_words);
bool mayProcessReadyWindow(uint16_t occupancy_words,
                           uint16_t priority_threshold_words);

}  // namespace seed_mg24

#endif
