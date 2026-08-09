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
  uint32_t fail_write_key=0,fail_remove_key=0;
  FakeNvm(){memset(entries,0,sizeof(entries));}
  StoreStatus initialize() override{return StoreStatus::Ok;}
  Entry* find(uint32_t key){for(auto& e:entries)if(e.used&&e.key==key)return &e;return nullptr;}
  StoreStatus read(uint32_t key,uint8_t* data,size_t cap,size_t* size) override{if(fail_read)return StoreStatus::ReadFailed;Entry*e=find(key);if(!e)return StoreStatus::NotFound;if(e->size>cap)return StoreStatus::SizeExceeded;memcpy(data,e->data,e->size);*size=e->size;return StoreStatus::Ok;}
  StoreStatus write(uint32_t key,const uint8_t* data,size_t size) override{if(fail_write||key==fail_write_key)return StoreStatus::WriteFailed;Entry*e=find(key);if(!e)for(auto& x:entries)if(!x.used){e=&x;e->used=true;e->key=key;break;}if(!e||size>sizeof(e->data))return StoreStatus::WriteFailed;memcpy(e->data,data,size);e->size=size;return StoreStatus::Ok;}
  StoreStatus remove(uint32_t key) override{if(fail_remove||key==fail_remove_key)return StoreStatus::WriteFailed;Entry*e=find(key);if(e)e->used=false;return StoreStatus::Ok;}
};
static StoredChannelConfiguration valid_config(){StoredChannelConfiguration x={};x.sample_interval_ms=100;x.processing_interval_ms=100;x.report_interval_ms=100;x.heartbeat_interval_ms=30000;x.filter_type=0;x.filter_window=1;x.enabled=1;return x;}
static void write_reset_marker(FakeNvm& nvm,const char* operation,ResetStage stage){
  uint8_t payload[35]={1,static_cast<uint8_t>(stage),32};memcpy(payload+3,operation,32);
  uint8_t record[kPersistentMaxRecord];size_t size=0;
  assert(encode_persistent_record(PersistentRecordType::StoreMetadata,1,0,payload,sizeof(payload),record,sizeof(record),&size)==StoreStatus::Ok);
  assert(nvm.write(ApplicationNvmKeys::kResetTransactionMarker,record,size)==StoreStatus::Ok);
}
int main(){
  assert(persistent_crc32((const uint8_t*)"123456789",9)==0xCBF43926u);assert(generation_newer(0,0xFFFFFFFFu));
  uint8_t payload[]={1,2,3},record[kPersistentMaxRecord];size_t n=0;assert(encode_persistent_record(PersistentRecordType::Identity,7,0,payload,3,record,sizeof(record),&n)==StoreStatus::Ok);RecordView view;assert(decode_persistent_record(record,n,PersistentRecordType::Identity,&view)==StoreStatus::Ok&&view.generation==7);record[n-1]^=1;assert(decode_persistent_record(record,n,PersistentRecordType::Identity,&view)==StoreStatus::IntegrityFailed);
  FakeNvm nvm;NodeIdentityStore ids(nvm);NodeIdentity id={};assert(ids.load(&id)==StoreStatus::Unprovisioned);assert(!NodeIdentityStore::valid_node_id("bad id"));assert(ids.provision("MG24-0001",&id)==StoreStatus::Ok);assert(!strcmp(id.node_id,"MG24-0001"));assert(ids.provision("MG24-0002",&id)==StoreStatus::InvalidArgument);NodeIdentityStore restarted(nvm);assert(restarted.load(&id)==StoreStatus::RecoveredFromPrevious||restarted.load(&id)==StoreStatus::Ok);
  PersistentConfigurationStore configs(nvm);StoredChannelConfiguration verified={};auto c=valid_config();assert(configs.write(c,&verified)==StoreStatus::Ok);c.report_interval_ms=200;nvm.fail_write=true;assert(configs.write(c,&verified)==StoreStatus::WriteFailed);nvm.fail_write=false;assert(configs.load(&verified)==StoreStatus::RecoveredFromPrevious||configs.load(&verified)==StoreStatus::Ok);assert(verified.report_interval_ms==100);
  const char* hw="0x0123456789ABCDEF";const char* op="00112233445566778899AABBCCDDEEFF";const char* token="FFEEDDCCBBAA99887766554433221100";
  FactoryResetController reset(nvm);ResetChallenge challenge;
  assert(reset.prepare(ResetScope::ApplicationFactory,hw,"0xFEDCBA9876543210",op,token,10,1000,&challenge)==StoreStatus::InvalidArgument);
  assert(reset.prepare(ResetScope::ApplicationFactory,hw,hw,op,token,10,1000,&challenge)==StoreStatus::Ok);
  assert(reset.confirm(ResetScope::ApplicationFactory,hw,op,"00000000000000000000000000000000",20).status==StoreStatus::InvalidArgument);
  assert(nvm.find(ApplicationNvmKeys::kIdentitySlotA));
  assert(reset.prepare(ResetScope::ApplicationFactory,hw,hw,op,token,30,1000,&challenge)==StoreStatus::Ok);
  ResetResult result=reset.confirm(ResetScope::ApplicationFactory,hw,op,token,40);assert(result.status==StoreStatus::Ok);
  assert(!nvm.find(ApplicationNvmKeys::kIdentitySlotA));assert(nvm.find(ApplicationNvmKeys::kResetTransactionMarker));
  assert(reset.confirm(ResetScope::ApplicationFactory,hw,op,token,50).status==StoreStatus::InvalidArgument);
  FactoryResetController rebooted(nvm);bool recovered=false;assert(rebooted.recover_on_boot(&recovered)==StoreStatus::Ok&&recovered);
  assert(!nvm.find(ApplicationNvmKeys::kResetTransactionMarker));assert(!nvm.find(ApplicationNvmKeys::kIdentitySlotA));

  // Expired confirmations are consumed and cannot be replayed.
  FakeNvm expired_nvm;FactoryResetController expired(expired_nvm);
  assert(expired.prepare(ResetScope::ApplicationFactory,hw,hw,op,token,10,1000,&challenge)==StoreStatus::Ok);
  assert(expired.confirm(ResetScope::ApplicationFactory,hw,op,token,1010).status==StoreStatus::InvalidArgument);
  assert(expired.confirm(ResetScope::ApplicationFactory,hw,op,token,20).status==StoreStatus::InvalidArgument);

  // A power cut after marker creation resumes deletion before marker completion.
  FakeNvm interrupted;uint8_t sentinel=7;
  assert(interrupted.write(ApplicationNvmKeys::kIdentitySlotA,&sentinel,1)==StoreStatus::Ok);
  assert(interrupted.write(0x0FF10,&sentinel,1)==StoreStatus::Ok); // application-nonresettable representative
  write_reset_marker(interrupted,op,ResetStage::MarkerWritten);
  FactoryResetController interrupted_boot(interrupted);recovered=false;
  assert(interrupted_boot.recover_on_boot(&recovered)==StoreStatus::Ok&&recovered);
  assert(!interrupted.find(ApplicationNvmKeys::kIdentitySlotA));assert(interrupted.find(0x0FF10));

  // A failed key deletion leaves the marker and safely completes on a later boot.
  FakeNvm failed_delete;assert(failed_delete.write(ApplicationNvmKeys::kIdentitySlotA,&sentinel,1)==StoreStatus::Ok);
  FactoryResetController failed_reset(failed_delete);
  assert(failed_reset.prepare(ResetScope::ApplicationFactory,hw,hw,op,token,10,1000,&challenge)==StoreStatus::Ok);
  failed_delete.fail_remove_key=ApplicationNvmKeys::kIdentitySlotA;
  assert(failed_reset.confirm(ResetScope::ApplicationFactory,hw,op,token,20).status==StoreStatus::WriteFailed);
  assert(failed_delete.find(ApplicationNvmKeys::kResetTransactionMarker));
  failed_delete.fail_remove_key=0;FactoryResetController retry_boot(failed_delete);recovered=false;
  assert(retry_boot.recover_on_boot(&recovered)==StoreStatus::Ok&&recovered);

  // Corrupt markers and marker-clear failures remain active storage faults.
  FakeNvm corrupt;assert(corrupt.write(ApplicationNvmKeys::kResetTransactionMarker,&sentinel,1)==StoreStatus::Ok);
  FactoryResetController corrupt_boot(corrupt);recovered=false;
  assert(corrupt_boot.recover_on_boot(&recovered)!=StoreStatus::Ok&&corrupt_boot.marker_active()&&!recovered);
  FakeNvm clear_failure;write_reset_marker(clear_failure,op,ResetStage::KeysCleared);
  clear_failure.fail_remove_key=ApplicationNvmKeys::kResetTransactionMarker;
  FactoryResetController clear_failure_boot(clear_failure);recovered=false;
  assert(clear_failure_boot.recover_on_boot(&recovered)==StoreStatus::WriteFailed);
  assert(clear_failure_boot.marker_active()&&clear_failure.find(ApplicationNvmKeys::kResetTransactionMarker)&&!recovered);
  return 0;
}
