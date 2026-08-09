#pragma once
#include "nvm_backend.h"

enum class ResetScope:uint8_t { ConfigurationOnly=1, ApplicationFactory=2 };
struct ResetChallenge { bool pending; ResetScope scope; uint32_t token; uint32_t expires_at_ms; };
struct ResetResult { StoreStatus status; uint8_t deleted_count; uint8_t failed_count; };
class FactoryResetController {
 public:
  explicit FactoryResetController(NvmBackend& backend):backend_(backend),challenge_{false,ResetScope::ConfigurationOnly,0,0},nonce_(0x4D473234u){}
  StoreStatus prepare(ResetScope scope,uint32_t now,uint32_t ttl_ms,ResetChallenge* output);
  ResetResult confirm(ResetScope scope,uint32_t token,uint32_t now);
  void cancel(){challenge_.pending=false;}
  const ResetChallenge& pending()const{return challenge_;}
 private:NvmBackend& backend_;ResetChallenge challenge_;uint32_t nonce_;
};
