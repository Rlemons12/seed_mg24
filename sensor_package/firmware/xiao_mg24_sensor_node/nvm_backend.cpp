#include "nvm_backend.h"
#ifdef ARDUINO
#include "nvm3_default.h"
#include "nvm3_default_config.h"
#include "nvm3_generic.h"

static StoreStatus map_status(sl_status_t s,StoreStatus failure){
  if(s==SL_STATUS_OK) return StoreStatus::Ok;
#ifdef SL_STATUS_NOT_FOUND
  if(s==SL_STATUS_NOT_FOUND) return StoreStatus::NotFound;
#endif
  if(s==ECODE_NVM3_ERR_KEY_NOT_FOUND) return StoreStatus::NotFound;
  return failure;
}
// The Arduino core initializes the default instance before setup(); its EEPROM
// implementation also consumes nvm3_defaultHandle directly. Calling
// nvm3_initDefault() is not linkable in the XIAO ble_silabs variant.
StoreStatus SiliconLabsNvm3Backend::initialize(){ return nvm3_defaultHandle?StoreStatus::Ok:StoreStatus::StorageUnavailable; }
StoreStatus SiliconLabsNvm3Backend::read(uint32_t key,uint8_t* data,size_t cap,size_t* size){
  if(!data||!size) return StoreStatus::InvalidArgument; uint32_t type=0; size_t n=0;
  StoreStatus info=map_status(nvm3_getObjectInfo(nvm3_defaultHandle,key,&type,&n),StoreStatus::ReadFailed); if(info!=StoreStatus::Ok)return info;
  if(type!=NVM3_OBJECTTYPE_DATA) return StoreStatus::Corrupt; if(n>cap)return StoreStatus::SizeExceeded;
  StoreStatus result=map_status(nvm3_readData(nvm3_defaultHandle,key,data,n),StoreStatus::ReadFailed); if(result==StoreStatus::Ok)*size=n; return result;
}
StoreStatus SiliconLabsNvm3Backend::write(uint32_t key,const uint8_t* data,size_t size){
  if(!data||size==0||size>NVM3_DEFAULT_MAX_OBJECT_SIZE)return StoreStatus::SizeExceeded;
  StoreStatus result=map_status(nvm3_writeData(nvm3_defaultHandle,key,data,size),StoreStatus::WriteFailed);
  if(result==StoreStatus::Ok&&nvm3_repackNeeded(nvm3_defaultHandle)) map_status(nvm3_repack(nvm3_defaultHandle),StoreStatus::WriteFailed);
  return result;
}
StoreStatus SiliconLabsNvm3Backend::remove(uint32_t key){
  StoreStatus result=map_status(nvm3_deleteObject(nvm3_defaultHandle,key),StoreStatus::WriteFailed);
  return result==StoreStatus::NotFound?StoreStatus::Ok:result;
}
#endif
