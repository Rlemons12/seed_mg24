#include "sensor_channel.h"

#include <math.h>

SensorChannel::SensorChannel(const ChannelConfig* config) { reconfigure(config); }
void SensorChannel::reconfigure(const ChannelConfig* config) {
  config_ = config; filter_.configure(config->filter_type, config->filter_window); alarm_.configure(config);
  pending_raw_ = 0; pending_valid_ = false; has_value_ = false; has_reported_ = false; last_reported_ = 0;
  last_sample_ = last_processing_ = last_report_ = last_heartbeat_ = last_value_time_ = 0;
  samples_since_report_ = 0;
  value_ = {0, 0, false, MeasurementQuality::Invalid, 0, 0, 0, 0};
}
bool SensorChannel::sample_due(uint32_t now) const { return config_->enabled && elapsed_since(now, last_sample_, config_->sample_interval_ms); }
bool SensorChannel::processing_due(uint32_t now) const { return config_->enabled && pending_valid_ && elapsed_since(now, last_processing_, config_->processing_interval_ms); }
bool SensorChannel::report_due(uint32_t now) const { return config_->enabled && has_value_ && elapsed_since(now, last_report_, config_->report_interval_ms); }
bool SensorChannel::heartbeat_due(uint32_t now) const { return config_->enabled && elapsed_since(now, last_heartbeat_, config_->heartbeat_interval_ms); }
void SensorChannel::accept_raw(float value, bool valid, uint32_t now) { pending_raw_ = value; pending_valid_ = valid; last_sample_ = now; samples_since_report_++; }
ProcessedValue SensorChannel::process(uint32_t now) {
  last_processing_ = now;
  if (!pending_valid_ || !isfinite(pending_raw_)) { value_.quality = MeasurementQuality::Invalid; return value_; }
  pending_valid_ = false;
  float processed = filter_.update(pending_raw_);
  float converted = processed;
  bool calibrated = config_->calibration_enabled;
  if (calibrated) converted = processed * config_->calibration_gain + config_->calibration_offset;
  float previous_value = value_.processed_value;
  value_.raw_value = pending_raw_; value_.processed_value = converted; value_.engineering_value_available = calibrated;
  value_.quality = calibrated ? MeasurementQuality::Good : MeasurementQuality::Uncalibrated;
  if (!has_value_) { value_.minimum = value_.maximum = value_.peak = converted; value_.rate_of_change = 0; }
  else { if (converted < value_.minimum) value_.minimum = converted; if (converted > value_.maximum) value_.maximum = converted; if (fabsf(converted) > fabsf(value_.peak)) value_.peak = converted; uint32_t dt = now - last_value_time_; value_.rate_of_change = dt ? (converted - previous_value) * 1000.0f / dt : 0; }
  has_value_ = true; last_value_time_ = now; return value_;
}
bool SensorChannel::changed_for_report() const { return !has_reported_ || (config_->change_deadband.configured && fabsf(value_.processed_value - last_reported_) >= config_->change_deadband.value); }
AlarmTransition SensorChannel::evaluate_alarm(uint32_t now) { return alarm_.evaluate(value_.processed_value, value_.quality, now); }
void SensorChannel::mark_reported(uint32_t now) { last_reported_ = value_.processed_value; has_reported_ = true; last_report_ = now; samples_since_report_ = 0; }
void SensorChannel::mark_heartbeat(uint32_t now) { last_heartbeat_ = now; }
