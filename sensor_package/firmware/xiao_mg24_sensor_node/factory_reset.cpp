#include "factory_reset.h"
#include "application_nvm_keys.h"

StoreStatus FactoryResetController::prepare(ResetScope scope,uint32_t now,uint32_t ttl,ResetChallenge* out){
  if(!out||ttl<1000||ttl>300000||challenge_.pending)return StoreStatus::InvalidArgument;nonce_=nonce_*1664525u+1013904223u+now;challenge_={true,scope,nonce_,now+ttl};*out=challenge_;return StoreStatus::Ok;
}
ResetResult FactoryResetController::confirm(ResetScope scope,uint32_t token,uint32_t now){
  ResetResult r={StoreStatus::InvalidArgument,0,0};if(!challenge_.pending||challenge_.scope!=scope||challenge_.token!=token)return r;
  if((int32_t)(now-challenge_.expires_at_ms)>=0){challenge_.pending=false;r.status=StoreStatus::InvalidArgument;return r;}challenge_.pending=false;
  const uint32_t* keys=scope==ResetScope::ConfigurationOnly?ApplicationNvmKeys::kConfigurationReset:ApplicationNvmKeys::kApplicationFactoryReset;
  size_t count=scope==ResetScope::ConfigurationOnly?ApplicationNvmKeys::kConfigurationResetCount:ApplicationNvmKeys::kApplicationFactoryResetCount;
  for(size_t i=0;i<count;i++){StoreStatus s=backend_.remove(keys[i]);if(s==StoreStatus::Ok){uint8_t byte;size_t size=0;StoreStatus check=backend_.read(keys[i],&byte,1,&size);if(check==StoreStatus::NotFound)r.deleted_count++;else r.failed_count++;}else r.failed_count++;}
  r.status=r.failed_count?StoreStatus::WriteFailed:StoreStatus::Ok;return r;
}
