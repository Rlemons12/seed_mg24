#include "node_identity_store.h"
#include "application_nvm_keys.h"
#include <string.h>

struct SlotIdentity { StoreStatus status; NodeIdentity value; };
static SlotIdentity read_identity(NvmBackend& b,uint32_t key){
  SlotIdentity x={StoreStatus::NotFound,{}}; uint8_t bytes[kPersistentMaxRecord]; size_t n=0; StoreStatus s=b.read(key,bytes,sizeof(bytes),&n);
  if(s!=StoreStatus::Ok){x.status=s;return x;} RecordView v; s=decode_persistent_record(bytes,n,PersistentRecordType::Identity,&v);
  if(s!=StoreStatus::Ok){x.status=s;return x;} if(v.payload_size<4){x.status=StoreStatus::Corrupt;return x;}
  uint8_t len=v.payload[3]; if(v.payload[0]!=1||v.payload[1]!=0||len==0||len>kNodeIdMaxLength||v.payload_size!=(uint16_t)(4+len)){x.status=v.payload[0]!=1?StoreStatus::UnsupportedVersion:StoreStatus::Corrupt;return x;}
  x.value.schema_version=1; x.value.provisioning_state=(ProvisioningState)v.payload[2]; memcpy(x.value.node_id,v.payload+4,len); x.value.node_id[len]='\0'; x.value.generation=v.generation;
  if(!NodeIdentityStore::valid_node_id(x.value.node_id)||x.value.provisioning_state!=ProvisioningState::Provisioned){x.status=StoreStatus::Corrupt;return x;} x.status=StoreStatus::Ok; return x;
}
bool NodeIdentityStore::valid_node_id(const char* s){ if(!s)return false; size_t n=strlen(s); if(n==0||n>kNodeIdMaxLength||s[0]=='-'||s[n-1]=='-')return false; for(size_t i=0;i<n;i++){char c=s[i];if(!((c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='-')||(c=='-'&&i&&s[i-1]=='-'))return false;}return true; }
StoreStatus NodeIdentityStore::load(NodeIdentity* out) const{
  if(!out)return StoreStatus::InvalidArgument;
  SlotIdentity a=read_identity(backend_,ApplicationNvmKeys::kIdentitySlotA),b=read_identity(backend_,ApplicationNvmKeys::kIdentitySlotB);
  bool av=a.status==StoreStatus::Ok,bv=b.status==StoreStatus::Ok; if(!av&&!bv){if(a.status==StoreStatus::NotFound&&b.status==StoreStatus::NotFound)return StoreStatus::Unprovisioned;return StoreStatus::Corrupt;}
  if(av&&bv&&!generation_newer(a.value.generation,b.value.generation)&&!generation_newer(b.value.generation,a.value.generation)&&a.value.generation==b.value.generation)return StoreStatus::GenerationConflict;
  *out=(bv&&(!av||generation_newer(b.value.generation,a.value.generation)))?b.value:a.value;
  if(av&&bv)return StoreStatus::Ok;
  return (av?b.status:a.status)==StoreStatus::NotFound?StoreStatus::Ok:StoreStatus::RecoveredFromPrevious;
}
StoreStatus NodeIdentityStore::provision(const char* id,NodeIdentity* out){
  if(!valid_node_id(id)||!out)return StoreStatus::InvalidArgument;
  NodeIdentity current={}; StoreStatus old=load(&current); if(old==StoreStatus::Ok||old==StoreStatus::RecoveredFromPrevious)return StoreStatus::InvalidArgument; if(old!=StoreStatus::Unprovisioned)return old;
  uint8_t payload[4+kNodeIdMaxLength]; size_t len=strlen(id); payload[0]=1;payload[1]=0;payload[2]=(uint8_t)ProvisioningState::Provisioned;payload[3]=(uint8_t)len;memcpy(payload+4,id,len);
  uint8_t record[kPersistentMaxRecord];size_t n=0;StoreStatus s=encode_persistent_record(PersistentRecordType::Identity,1,0,payload,(uint16_t)(4+len),record,sizeof(record),&n);if(s!=StoreStatus::Ok)return s;
  s=backend_.write(ApplicationNvmKeys::kIdentitySlotA,record,n);if(s!=StoreStatus::Ok)return s; SlotIdentity verify=read_identity(backend_,ApplicationNvmKeys::kIdentitySlotA);if(verify.status!=StoreStatus::Ok||strcmp(verify.value.node_id,id))return StoreStatus::ReadbackFailed;
  s=encode_persistent_record(PersistentRecordType::Identity,2,0,payload,(uint16_t)(4+len),record,sizeof(record),&n);if(s!=StoreStatus::Ok)return s;s=backend_.write(ApplicationNvmKeys::kIdentitySlotB,record,n);if(s!=StoreStatus::Ok)return s;verify=read_identity(backend_,ApplicationNvmKeys::kIdentitySlotB);if(verify.status!=StoreStatus::Ok||verify.value.generation!=2||strcmp(verify.value.node_id,id))return StoreStatus::ReadbackFailed;*out=verify.value;return StoreStatus::Ok;
}
