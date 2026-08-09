#pragma once

#include "alarm_engine.h"
#include "filters.h"

class SensorChannel {
 public:
  explicit SensorChannel(const ChannelConfig* config);
  void reconfigure(const ChannelConfig* config);
  bool sample_due(uint32_t now) const;
  bool processing_due(uint32_t now) const;
  bool report_due(uint32_t now) const;
  bool heartbeat_due(uint32_t now) const;
  void accept_raw(float value, bool valid, uint32_t now);
  ProcessedValue process(uint32_t now);
  bool changed_for_report() const;
  AlarmTransition evaluate_alarm(uint32_t now);
  void mark_reported(uint32_t now);
  void mark_heartbeat(uint32_t now);
  const ProcessedValue& value() const { return value_; }

 private:
  const ChannelConfig* config_;
  NumericFilter filter_;
  AlarmEngine alarm_;
  ProcessedValue value_;
  float pending_raw_;
  bool pending_valid_;
  bool has_value_;
  bool has_reported_;
  float last_reported_;
  uint32_t last_sample_, last_processing_, last_report_, last_heartbeat_, last_value_time_;
};
