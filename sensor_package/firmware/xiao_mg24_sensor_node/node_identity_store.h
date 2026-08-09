#pragma once
#include "nvm_backend.h"

constexpr size_t kNodeIdMaxLength=31;
enum class ProvisioningState:uint8_t { Unprovisioned=0, Provisioned=1, Recovery=2 };
struct NodeIdentity { uint16_t schema_version; ProvisioningState provisioning_state; char node_id[kNodeIdMaxLength+1]; uint32_t generation; };
class NodeIdentityStore {
 public:
  explicit NodeIdentityStore(NvmBackend& backend):backend_(backend){}
  StoreStatus load(NodeIdentity* output) const;
  StoreStatus provision(const char* node_id,NodeIdentity* output);
  static bool valid_node_id(const char* value);
 private: NvmBackend& backend_;
};
