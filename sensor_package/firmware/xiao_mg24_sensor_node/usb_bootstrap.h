#pragma once
#ifdef ARDUINO
#include <Arduino.h>
#include "configuration_store.h"
#include "factory_reset.h"
#include "node_identity_store.h"

constexpr size_t kBootstrapMaxLine=768;
constexpr size_t kBootstrapMaxRequestId=40;
constexpr const char* kBootstrapPrefix="MG24BOOT1 ";

class UsbBootstrapProtocol {
 public:
  UsbBootstrapProtocol(NodeIdentityStore& identities,PersistentConfigurationStore& configurations,FactoryResetController& reset)
    :identities_(identities),configurations_(configurations),reset_(reset),length_(0),overflow_(false){}
  void poll(Stream& serial,uint32_t now);
  bool handle_line(const char* line,Stream& serial,uint32_t now);
 private:
  NodeIdentityStore& identities_;PersistentConfigurationStore& configurations_;FactoryResetController& reset_;
  char line_[kBootstrapMaxLine+1];size_t length_;bool overflow_;
  void respond(Stream& serial,const char* request_id,const char* action,const char* status,const char* result_json,const char* error_code=nullptr);
};
#endif
