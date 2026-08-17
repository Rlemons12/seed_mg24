#include <assert.h>
#include <math.h>
#include <string.h>

#include "filters.h"
#include "alarm_engine.h"
#include "sensor_channel.h"
#include "telemetry_buffer.h"
#include "telemetry_encoder.h"
#include "configuration_store.h"

static ChannelConfig config() {
  ChannelConfig c = MICROPHONE_CHANNEL_CONFIG;
  c.filter_type = FilterType::None; c.filter_window = 1;
  return c;
}

int main() {
  assert(elapsed_since(5, 0xFFFFFFF0u, 20));

  NumericFilter filter;
  filter.configure(FilterType::MovingAverage, 3);
  assert(filter.update(1) == 1); assert(filter.update(2) == 1.5f); assert(filter.update(3) == 2);
  filter.configure(FilterType::Exponential, 2, 0.5f);
  assert(filter.update(2) == 2); assert(filter.update(4) == 3);
  filter.configure(FilterType::Median, 3);
  filter.update(9); filter.update(1); assert(filter.update(3) == 3);
  DigitalDebounceFilter debounce; debounce.configure(2); debounce.reset(false);
  assert(!debounce.update(true)); assert(debounce.update(true));

  ChannelConfig c = config(); c.sample_interval_ms=10; c.processing_interval_ms=20; c.report_interval_ms=30; c.heartbeat_interval_ms=40;
  SensorChannel channel(&c);
  assert(channel.sample_due(10)); channel.accept_raw(100, true, 10); assert(!channel.processing_due(19));
  assert(channel.processing_due(20)); ProcessedValue first=channel.process(20);
  assert(first.quality == MeasurementQuality::Uncalibrated && !first.engineering_value_available);
  assert(first.minimum == 100 && first.maximum == 100 && channel.report_due(30) && channel.heartbeat_due(40));
  assert(channel.samples_since_report() == 1);
  channel.accept_raw(110, true, 40); ProcessedValue second=channel.process(40);
  assert(second.maximum == 110 && second.rate_of_change > 0);
  assert(channel.samples_since_report() == 2);
  channel.mark_reported(40); assert(channel.samples_since_report() == 0);
  assert(channel.sample_due(50) && !channel.report_due(69) && channel.report_due(70));

  c.warning_high={true,10}; c.activation_persistence_ms=100; c.clearing_persistence_ms=50; c.hysteresis=1;
  AlarmEngine alarm; alarm.configure(&c);
  assert(!alarm.evaluate(11,MeasurementQuality::Good,0).changed);
  AlarmTransition active=alarm.evaluate(11,MeasurementQuality::Good,100); assert(active.changed && active.current==MonitoringState::WarningHigh);
  assert(!alarm.evaluate(11,MeasurementQuality::Good,200).changed);
  assert(!alarm.evaluate(8,MeasurementQuality::Good,210).changed);
  AlarmTransition clear=alarm.evaluate(8,MeasurementQuality::Good,260); assert(clear.changed && clear.current==MonitoringState::Normal);

  TelemetryBuffer buffer; TelemetryRecord routine={}; routine.priority=RecordPriority::Routine;
  for (int i=0;i<TELEMETRY_BUFFER_CAPACITY;i++) { routine.sequence_number=i; assert(buffer.push(routine)); }
  TelemetryRecord alarm_record=routine; alarm_record.priority=RecordPriority::Alarm; alarm_record.sequence_number=99;
  assert(buffer.push(alarm_record)); assert(buffer.dropped_count()==1);
  TelemetryRecord peeked; assert(buffer.peek_oldest(&peeked)); assert(buffer.size()==TELEMETRY_BUFFER_CAPACITY);
  assert(buffer.oldest_sequence()==peeked.sequence_number && buffer.newest_sequence()==99);
  assert(buffer.acknowledge_through(5)>0 && buffer.size()<TELEMETRY_BUFFER_CAPACITY);
  bool found=false; TelemetryRecord out; while(buffer.pop(&out)) if(out.sequence_number==99) found=true;
  assert(found && out.delayed);
  TelemetryBuffer bounded;
  for (int i=0;i<TELEMETRY_BUFFER_CAPACITY+1;i++) { routine.sequence_number=i; assert(bounded.push(routine)); }
  assert(bounded.size()==TELEMETRY_BUFFER_CAPACITY && bounded.oldest_sequence()==1 &&
         bounded.newest_sequence()==TELEMETRY_BUFFER_CAPACITY && bounded.dropped_count()==1);

  char encoded[244]; alarm_record.type=RecordType::Measurement; strcpy(alarm_record.channel_id,"sensor_1");
  alarm_record.quality=MeasurementQuality::Uncalibrated; alarm_record.raw_value=1834; alarm_record.uptime_ms=20;
  assert(encode_record(alarm_record,"ARM2001-01",encoded,sizeof(encoded))); assert(strlen(encoded)<244);
  assert(strstr(encoded,"adc_count") && strstr(encoded,"uncalibrated"));

  StoredChannelConfiguration stored = {};
  stored.sample_interval_ms=100; stored.processing_interval_ms=100; stored.report_interval_ms=100;
  stored.heartbeat_interval_ms=30000; stored.filter_type=(uint8_t)FilterType::None; stored.filter_window=1; stored.enabled=1;
  VolatileConfigurationStore store(1000);
  assert(store.write(stored, 0, true)); assert(!store.write(stored, 100, false)); assert(store.write_count()==1);
  StoredChannelConfiguration loaded; assert(store.load(&loaded)); assert(loaded.report_interval_ms==100);
  stored.report_interval_ms=200; assert(store.write(stored, 1000, false)); store.corrupt_slot_for_test(1);
  assert(store.load(&loaded)); assert(loaded.report_interval_ms==100);
  return 0;
}
