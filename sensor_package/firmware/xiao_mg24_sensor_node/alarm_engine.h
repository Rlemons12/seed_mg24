#pragma once

#include "sensor_types.h"

struct AlarmTransition {
  bool changed;
  MonitoringState previous;
  MonitoringState current;
  uint32_t timestamp_ms;
  float value;
};

class AlarmEngine {
 public:
  AlarmEngine();
  void configure(const ChannelConfig* config);
  void reset();
  AlarmTransition evaluate(float value, MeasurementQuality quality, uint32_t now);
  MonitoringState state() const { return state_; }
  void clear_latch();

 private:
  MonitoringState desired_state(float value, MeasurementQuality quality) const;
  const ChannelConfig* config_;
  MonitoringState state_;
  MonitoringState pending_;
  uint32_t pending_since_;
  bool pending_active_;
};
