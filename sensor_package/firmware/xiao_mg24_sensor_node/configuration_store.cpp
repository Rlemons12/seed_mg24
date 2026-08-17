#include "configuration_store.h"

#include <string.h>
#include "application_nvm_keys.h"

static const uint32_t CONFIG_MAGIC = 0x4D473234UL;

uint32_t configuration_checksum(const StoredChannelConfiguration& value) {
  const uint8_t* bytes = reinterpret_cast<const uint8_t*>(&value);
  uint32_t hash = 2166136261UL;
  for (size_t index = 0; index < sizeof(value) - sizeof(value.checksum); ++index) {
    hash ^= bytes[index];
    hash *= 16777619UL;
  }
  return hash;
}

bool validate_stored_configuration(const StoredChannelConfiguration& value) {
  if (value.magic != CONFIG_MAGIC || value.schema_version != 1 || value.checksum != configuration_checksum(value)) return false;
  if (value.enabled > 1 || value.sample_interval_ms < 10 || value.sample_interval_ms > 5000) return false;
  if (value.processing_interval_ms < 10 || value.processing_interval_ms > 5000) return false;
  if (value.report_interval_ms < 50 || value.report_interval_ms > 5000) return false;
  if (value.heartbeat_interval_ms < 1000 || value.heartbeat_interval_ms > 3600000) return false;
  if (value.filter_type > static_cast<uint8_t>(FilterType::DigitalDebounce) || value.filter_window < 1 || value.filter_window > 9) return false;
  return true;
}

VolatileConfigurationStore::VolatileConfigurationStore(uint32_t minimum_write_interval_ms)
    : active_slot_(0), last_write_ms_(0), minimum_write_interval_ms_(minimum_write_interval_ms), write_count_(0) {
  memset(slots_, 0, sizeof(slots_)); populated_[0] = populated_[1] = false;
}

bool VolatileConfigurationStore::write(const StoredChannelConfiguration& input, uint32_t now, bool force) {
  if (!force && write_count_ > 0 && static_cast<uint32_t>(now - last_write_ms_) < minimum_write_interval_ms_) return false;
  StoredChannelConfiguration value = input;
  value.magic = CONFIG_MAGIC; value.schema_version = 1;
  value.generation = write_count_ + 1; value.checksum = configuration_checksum(value);
  if (!validate_stored_configuration(value)) return false;
  uint8_t target = populated_[active_slot_] ? 1 - active_slot_ : active_slot_;
  slots_[target] = value;
  populated_[target] = true;
  active_slot_ = target; last_write_ms_ = now; write_count_++;
  return true;
}

bool VolatileConfigurationStore::load(StoredChannelConfiguration* output) const {
  if (!output) return false;
  bool valid0 = populated_[0] && validate_stored_configuration(slots_[0]);
  bool valid1 = populated_[1] && validate_stored_configuration(slots_[1]);
  if (!valid0 && !valid1) return false;
  uint8_t selected = valid1 && (!valid0 || slots_[1].generation > slots_[0].generation) ? 1 : 0;
  *output = slots_[selected]; return true;
}

void VolatileConfigurationStore::corrupt_slot_for_test(uint8_t slot) {
  if (slot < 2 && populated_[slot]) slots_[slot].checksum ^= 0xFFFFFFFFUL;
}

struct ConfigSlot { StoreStatus status; StoredChannelConfiguration value; uint32_t generation; };
static void config_put16(uint8_t* p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void config_put32(uint8_t* p,uint32_t v){for(uint8_t i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i));}
static uint16_t config_get16(const uint8_t* p){return (uint16_t)p[0]|((uint16_t)p[1]<<8);}
static uint32_t config_get32(const uint8_t* p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void serialize_configuration(const StoredChannelConfiguration& v,uint8_t* p){
  config_put32(p,v.magic);config_put16(p+4,v.schema_version);config_put16(p+6,v.reserved);config_put32(p+8,v.generation);
  config_put32(p+12,v.sample_interval_ms);config_put32(p+16,v.processing_interval_ms);config_put32(p+20,v.report_interval_ms);config_put32(p+24,v.heartbeat_interval_ms);
  p[28]=v.filter_type;p[29]=v.filter_window;p[30]=v.enabled;p[31]=v.reserved2;config_put32(p+32,v.checksum);
}
static void deserialize_configuration(const uint8_t* p,StoredChannelConfiguration* v){
  memset(v,0,sizeof(*v));v->magic=config_get32(p);v->schema_version=config_get16(p+4);v->reserved=config_get16(p+6);v->generation=config_get32(p+8);
  v->sample_interval_ms=config_get32(p+12);v->processing_interval_ms=config_get32(p+16);v->report_interval_ms=config_get32(p+20);v->heartbeat_interval_ms=config_get32(p+24);
  v->filter_type=p[28];v->filter_window=p[29];v->enabled=p[30];v->reserved2=p[31];v->checksum=config_get32(p+32);
}
static ConfigSlot read_config_slot(NvmBackend& backend,uint32_t key){
  ConfigSlot result={StoreStatus::NotFound,{},0}; uint8_t record[kPersistentMaxRecord];size_t size=0;StoreStatus s=backend.read(key,record,sizeof(record),&size);if(s!=StoreStatus::Ok){result.status=s;return result;}
  RecordView view;s=decode_persistent_record(record,size,PersistentRecordType::Configuration,&view);if(s!=StoreStatus::Ok){result.status=s;return result;}
  if(view.payload_size!=sizeof(StoredChannelConfiguration)){result.status=StoreStatus::Corrupt;return result;} deserialize_configuration(view.payload,&result.value);
  if(result.value.generation!=view.generation||!validate_stored_configuration(result.value)){result.status=StoreStatus::Corrupt;return result;}
  result.generation=view.generation;result.status=StoreStatus::Ok;return result;
}
StoreStatus PersistentConfigurationStore::load(StoredChannelConfiguration* out) const{
  if(!out)return StoreStatus::InvalidArgument;
  ConfigSlot a=read_config_slot(backend_,ApplicationNvmKeys::kConfigurationSlotA),b=read_config_slot(backend_,ApplicationNvmKeys::kConfigurationSlotB);
  bool av=a.status==StoreStatus::Ok,bv=b.status==StoreStatus::Ok;if(!av&&!bv){if(a.status==StoreStatus::NotFound&&b.status==StoreStatus::NotFound)return StoreStatus::NotFound;return StoreStatus::Corrupt;}
  if(av&&bv&&a.generation==b.generation)return StoreStatus::GenerationConflict;
  *out=(bv&&(!av||generation_newer(b.generation,a.generation)))?b.value:a.value;if(av&&bv)return StoreStatus::Ok;return (av?b.status:a.status)==StoreStatus::NotFound?StoreStatus::Ok:StoreStatus::RecoveredFromPrevious;
}
StoreStatus PersistentConfigurationStore::write(const StoredChannelConfiguration& input,StoredChannelConfiguration* verified){
  if(!verified)return StoreStatus::InvalidArgument;
  StoredChannelConfiguration current={};StoreStatus old=load(&current);uint32_t generation=(old==StoreStatus::Ok||old==StoreStatus::RecoveredFromPrevious)?current.generation+1:1;
  if(old!=StoreStatus::Ok&&old!=StoreStatus::RecoveredFromPrevious&&old!=StoreStatus::NotFound)return old;
  StoredChannelConfiguration value=input;value.magic=CONFIG_MAGIC;value.schema_version=1;value.reserved=0;value.reserved2=0;value.generation=generation;value.checksum=configuration_checksum(value);
  if(!validate_stored_configuration(value))return StoreStatus::InvalidArgument;
  uint32_t target=(generation&1)?ApplicationNvmKeys::kConfigurationSlotA:ApplicationNvmKeys::kConfigurationSlotB;
  uint8_t payload[sizeof(StoredChannelConfiguration)];serialize_configuration(value,payload);uint8_t record[kPersistentMaxRecord];size_t size=0;StoreStatus s=encode_persistent_record(PersistentRecordType::Configuration,generation,0,payload,sizeof(payload),record,sizeof(record),&size);if(s!=StoreStatus::Ok)return s;
  s=backend_.write(target,record,size);if(s!=StoreStatus::Ok)return s;ConfigSlot check=read_config_slot(backend_,target);if(check.status!=StoreStatus::Ok||check.generation!=generation)return StoreStatus::ReadbackFailed;*verified=check.value;return StoreStatus::Ok;
}
