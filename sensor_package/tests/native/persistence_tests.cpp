#include <assert.h>
#include <string.h>
#include "application_nvm_keys.h"
#include "configuration_store.h"
#include "factory_reset.h"
#include "node_identity_store.h"
#include "persistent_record.h"

class FakeNvm : public NvmBackend {
 public:
  struct Entry { uint32_t key; uint8_t data[kPersistentMaxRecord]; size_t size; bool used; } entries[16];
  bool fail_write=false,fail_read=false,fail_remove=false;
  FakeNvm(){memset(entries,0,sizeof(entries));}
  StoreStatus initialize() override{return StoreStatus::Ok;}
  Entry* find(uint32_t key){for(auto& e:entries)if(e.used&&e.key==key)return &e;return nullptr;}
  StoreStatus read(uint32_t key,uint8_t* data,size_t cap,size_t* size) override{if(fail_read)return StoreStatus::ReadFailed;Entry*e=find(key);if(!e)return StoreStatus::NotFound;if(e->size>cap)return StoreStatus::SizeExceeded;memcpy(data,e->data,e->size);*size=e->size;return StoreStatus::Ok;}
  StoreStatus write(uint32_t key,const uint8_t* data,size_t size) override{if(fail_write)return StoreStatus::WriteFailed;Entry*e=find(key);if(!e)for(auto& x:entries)if(!x.used){e=&x;e->used=true;e->key=key;break;}if(!e||size>sizeof(e->data))return StoreStatus::WriteFailed;memcpy(e->data,data,size);e->size=size;return StoreStatus::Ok;}
  StoreStatus remove(uint32_t key) override{if(fail_remove)return StoreStatus::WriteFailed;Entry*e=find(key);if(e)e->used=false;return StoreStatus::Ok;}
};
static StoredChannelConfiguration valid_config(){StoredChannelConfiguration x={};x.sample_interval_ms=100;x.processing_interval_ms=100;x.report_interval_ms=100;x.heartbeat_interval_ms=30000;x.filter_type=0;x.filter_window=1;x.enabled=1;return x;}
int main(){
  assert(persistent_crc32((const uint8_t*)"123456789",9)==0xCBF43926u);assert(generation_newer(0,0xFFFFFFFFu));
  uint8_t payload[]={1,2,3},record[kPersistentMaxRecord];size_t n=0;assert(encode_persistent_record(PersistentRecordType::Identity,7,0,payload,3,record,sizeof(record),&n)==StoreStatus::Ok);RecordView view;assert(decode_persistent_record(record,n,PersistentRecordType::Identity,&view)==StoreStatus::Ok&&view.generation==7);record[n-1]^=1;assert(decode_persistent_record(record,n,PersistentRecordType::Identity,&view)==StoreStatus::IntegrityFailed);
  FakeNvm nvm;NodeIdentityStore ids(nvm);NodeIdentity id={};assert(ids.load(&id)==StoreStatus::Unprovisioned);assert(!NodeIdentityStore::valid_node_id("bad id"));assert(ids.provision("MG24-0001",&id)==StoreStatus::Ok);assert(!strcmp(id.node_id,"MG24-0001"));assert(ids.provision("MG24-0002",&id)==StoreStatus::InvalidArgument);NodeIdentityStore restarted(nvm);assert(restarted.load(&id)==StoreStatus::RecoveredFromPrevious||restarted.load(&id)==StoreStatus::Ok);
  PersistentConfigurationStore configs(nvm);StoredChannelConfiguration verified={};auto c=valid_config();assert(configs.write(c,&verified)==StoreStatus::Ok);c.report_interval_ms=200;nvm.fail_write=true;assert(configs.write(c,&verified)==StoreStatus::WriteFailed);nvm.fail_write=false;assert(configs.load(&verified)==StoreStatus::RecoveredFromPrevious||configs.load(&verified)==StoreStatus::Ok);assert(verified.report_interval_ms==100);
  FactoryResetController reset(nvm);ResetChallenge challenge;assert(reset.prepare(ResetScope::ConfigurationOnly,10,1000,&challenge)==StoreStatus::Ok);assert(nvm.find(ApplicationNvmKeys::kConfigurationSlotA));assert(reset.confirm(ResetScope::ApplicationFactory,challenge.token,20).status==StoreStatus::InvalidArgument);assert(reset.confirm(ResetScope::ConfigurationOnly,challenge.token,20).status==StoreStatus::Ok);assert(!nvm.find(ApplicationNvmKeys::kConfigurationSlotA));assert(nvm.find(ApplicationNvmKeys::kIdentitySlotA));assert(reset.confirm(ResetScope::ConfigurationOnly,challenge.token,20).status==StoreStatus::InvalidArgument);
  assert(reset.prepare(ResetScope::ApplicationFactory,100,1000,&challenge)==StoreStatus::Ok);assert(reset.confirm(ResetScope::ApplicationFactory,challenge.token,1200).status==StoreStatus::InvalidArgument);assert(nvm.find(ApplicationNvmKeys::kIdentitySlotA));
  return 0;
}
