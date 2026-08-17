#include "persistent_record.h"

#include <string.h>

static const uint32_t kMagic = 0x4E335247u; // "N3RG" encoded little-endian.
static const uint8_t kEnvelopeVersion = 1;
static void put16(uint8_t* p, uint16_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); }
static void put32(uint8_t* p, uint32_t v) { for (uint8_t i=0;i<4;i++) p[i]=(uint8_t)(v>>(8*i)); }
static uint16_t get16(const uint8_t* p) { return (uint16_t)p[0] | ((uint16_t)p[1]<<8); }
static uint32_t get32(const uint8_t* p) { return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24); }

uint32_t persistent_crc32(const uint8_t* data, size_t size) {
  uint32_t crc=0xFFFFFFFFu;
  for(size_t i=0;i<size;i++){ crc^=data[i]; for(uint8_t b=0;b<8;b++) crc=(crc>>1)^((crc&1)?0xEDB88320u:0u); }
  return crc^0xFFFFFFFFu;
}
bool generation_newer(uint32_t a,uint32_t b){ uint32_t d=a-b; return d!=0 && d<0x80000000u; }

StoreStatus encode_persistent_record(PersistentRecordType type,uint32_t generation,uint16_t flags,const uint8_t* payload,
 uint16_t payload_size,uint8_t* out,size_t cap,size_t* out_size){
  if(!out||!out_size||(!payload&&payload_size)) return StoreStatus::InvalidArgument;
  if(payload_size>kPersistentMaxPayload||cap<kPersistentHeaderSize+payload_size) return StoreStatus::SizeExceeded;
  if(type!=PersistentRecordType::Identity&&type!=PersistentRecordType::Configuration&&type!=PersistentRecordType::StoreMetadata) return StoreStatus::InvalidArgument;
  if(flags!=0) return StoreStatus::InvalidArgument;
  memset(out,0,kPersistentHeaderSize+payload_size); put32(out,kMagic); out[4]=(uint8_t)type; out[5]=kEnvelopeVersion;
  put16(out+6,(uint16_t)kPersistentHeaderSize); put16(out+8,payload_size); put16(out+10,flags); put32(out+12,generation);
  put32(out+16,persistent_crc32(payload,payload_size)); if(payload_size) memcpy(out+kPersistentHeaderSize,payload,payload_size);
  put32(out+20,persistent_crc32(out,20)); *out_size=kPersistentHeaderSize+payload_size; return StoreStatus::Ok;
}
StoreStatus decode_persistent_record(const uint8_t* r,size_t size,PersistentRecordType expected,RecordView* out){
  if(!r||!out) return StoreStatus::InvalidArgument;
  if(size<kPersistentHeaderSize) return StoreStatus::Corrupt;
  if(get32(r)!=kMagic) return StoreStatus::Corrupt;
  if(r[5]!=kEnvelopeVersion) return StoreStatus::UnsupportedVersion;
  if(r[4]!=(uint8_t)expected) return StoreStatus::Corrupt;
  if(get16(r+6)!=kPersistentHeaderSize||get16(r+10)!=0) return StoreStatus::Corrupt;
  uint16_t n=get16(r+8); if(n>kPersistentMaxPayload||size!=kPersistentHeaderSize+n) return StoreStatus::Corrupt;
  if(get32(r+20)!=persistent_crc32(r,20)) return StoreStatus::IntegrityFailed;
  if(get32(r+16)!=persistent_crc32(r+kPersistentHeaderSize,n)) return StoreStatus::IntegrityFailed;
  out->type=expected; out->generation=get32(r+12); out->flags=0; out->payload=r+kPersistentHeaderSize; out->payload_size=n; return StoreStatus::Ok;
}
const char* store_status_name(StoreStatus s){
  static const char* names[]={"ok","not_found","unprovisioned","corrupt","unsupported_version","invalid_argument","size_exceeded","storage_unavailable","read_failed","write_failed","readback_failed","integrity_failed","generation_conflict","migration_required","recovered_from_previous"};
  uint8_t i=(uint8_t)s; return i<sizeof(names)/sizeof(names[0])?names[i]:"unknown";
}
