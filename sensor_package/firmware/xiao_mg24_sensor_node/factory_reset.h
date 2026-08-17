#pragma once
#include "nvm_backend.h"

enum class ResetScope : uint8_t { ConfigurationOnly = 1, ApplicationFactory = 2 };
enum class ResetStage : uint8_t { MarkerWritten = 1, KeysCleared = 2 };
struct ResetChallenge {
  bool pending;
  ResetScope scope;
  char challenge[33];
  char operation_id[33];
  char hardware_id[19];
  uint32_t expires_at_ms;
};
struct ResetResult { StoreStatus status; uint8_t deleted_count; uint8_t failed_count; };

class FactoryResetController {
 public:
  explicit FactoryResetController(NvmBackend& backend)
      : backend_(backend), challenge_{}, busy_(false), marker_active_(false), reboot_required_(false) {}
  StoreStatus prepare(ResetScope scope, const char* hardware_id, const char* expected_hardware_id,
                      const char* operation_id, const char* challenge, uint32_t now, uint32_t ttl_ms,
                      ResetChallenge* output);
  ResetResult confirm(ResetScope scope, const char* expected_hardware_id, const char* operation_id,
                      const char* challenge, uint32_t now);
  StoreStatus recover_on_boot(bool* recovery_pending);
  StoreStatus complete_recovery_on_boot(bool unprovisioned_bootstrap_ready);
  void cancel();
  bool busy() const { return busy_ || marker_active_; }
  bool marker_active() const { return marker_active_; }
  bool reboot_required() const { return reboot_required_; }
  const ResetChallenge& pending() const { return challenge_; }
 private:
  NvmBackend& backend_;
  ResetChallenge challenge_;
  bool busy_;
  bool marker_active_;
  bool reboot_required_;
  StoreStatus write_marker(const char* operation_id, ResetStage stage);
  StoreStatus read_marker(char* operation_id, size_t capacity, ResetStage* stage);
  StoreStatus clear_marker();
  bool reset_keys_absent() const;
};
