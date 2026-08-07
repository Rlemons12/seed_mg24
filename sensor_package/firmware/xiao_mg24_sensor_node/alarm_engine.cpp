#include "alarm_engine.h"

AlarmEngine::AlarmEngine() : config_(nullptr) { reset(); }
void AlarmEngine::configure(const ChannelConfig* config) { config_ = config; reset(); }
void AlarmEngine::reset() { state_ = MonitoringState::Normal; pending_ = MonitoringState::Normal; pending_since_ = 0; pending_active_ = false; }
void AlarmEngine::clear_latch() { if (config_ && config_->latching_enabled) reset(); }

MonitoringState AlarmEngine::desired_state(float value, MeasurementQuality quality) const {
  if (quality == MeasurementQuality::SensorFault) return MonitoringState::SensorFault;
  if (quality == MeasurementQuality::Invalid) return MonitoringState::Invalid;
  if (!config_) return MonitoringState::Normal;
  float h = config_->hysteresis;
  if (config_->alarm_low.configured && value <= config_->alarm_low.value + (state_ == MonitoringState::AlarmLow ? h : 0)) return MonitoringState::AlarmLow;
  if (config_->alarm_high.configured && value >= config_->alarm_high.value - (state_ == MonitoringState::AlarmHigh ? h : 0)) return MonitoringState::AlarmHigh;
  if (config_->warning_low.configured && value <= config_->warning_low.value + (state_ == MonitoringState::WarningLow ? h : 0)) return MonitoringState::WarningLow;
  if (config_->warning_high.configured && value >= config_->warning_high.value - (state_ == MonitoringState::WarningHigh ? h : 0)) return MonitoringState::WarningHigh;
  return MonitoringState::Normal;
}

AlarmTransition AlarmEngine::evaluate(float value, MeasurementQuality quality, uint32_t now) {
  MonitoringState desired = desired_state(value, quality);
  if (config_ && config_->latching_enabled && (state_ == MonitoringState::AlarmLow || state_ == MonitoringState::AlarmHigh) && desired == MonitoringState::Normal)
    desired = state_;
  if (desired == state_) { pending_active_ = false; return {false, state_, state_, now, value}; }
  if (!pending_active_ || pending_ != desired) { pending_ = desired; pending_since_ = now; pending_active_ = true; }
  uint32_t persistence = desired == MonitoringState::Normal ? config_->clearing_persistence_ms : config_->activation_persistence_ms;
  if (!elapsed_since(now, pending_since_, persistence)) return {false, state_, state_, now, value};
  MonitoringState previous = state_; state_ = desired; pending_active_ = false;
  return {true, previous, state_, now, value};
}
