#include "persistent_telemetry_journal.h"

#include <math.h>
#include <string.h>

#include "application_nvm_keys.h"
#include "persistent_record.h"

namespace {
constexpr uint32_t kJournalMagic = 0x4A544D47u;  // GMTJ
constexpr uint8_t kJournalVersion = 1;

bool generation_before(uint32_t left, uint32_t right) {
  return left != right && static_cast<int32_t>(left - right) < 0;
}

bool same_boot_id(const char* left, const char* right) {
  if (!left || !right) return false;
  for (uint8_t i = 0; i < 16; ++i) {
    char a = left[i], b = right[i];
    if (a >= 'A' && a <= 'F') a = static_cast<char>(a - 'A' + 'a');
    if (b >= 'A' && b <= 'F') b = static_cast<char>(b - 'A' + 'a');
    if (a != b) return false;
  }
  return left[16] == '\0' && right[16] == '\0';
}
}

struct PersistentTelemetryJournal::Batch {
  uint32_t magic;
  uint32_t generation;
  uint8_t version;
  uint8_t count;
  uint16_t reserved;
  PersistentTelemetrySummary entries[kPersistentTelemetryBatchSize];
  uint32_t crc32;
};

PersistentTelemetryJournal::PersistentTelemetryJournal(NvmBackend& backend)
    : backend_(backend), ordered_count_(0), head_entry_(0), next_generation_(1),
      staging_count_(0), dropped_count_(0), last_status_(StoreStatus::NotFound) {
  memset(ordered_keys_, 0, sizeof(ordered_keys_));
  memset(staging_, 0, sizeof(staging_));
}

StoreStatus PersistentTelemetryJournal::read_batch(uint32_t key, Batch* batch) const {
  if (!batch) return StoreStatus::InvalidArgument;
  size_t size = 0;
  StoreStatus status = backend_.read(key, reinterpret_cast<uint8_t*>(batch), sizeof(Batch), &size);
  if (status != StoreStatus::Ok) return status;
  if (size != sizeof(Batch) || batch->magic != kJournalMagic || batch->version != kJournalVersion ||
      batch->count == 0 || batch->count > kPersistentTelemetryBatchSize) return StoreStatus::Corrupt;
  const uint32_t expected = persistent_crc32(reinterpret_cast<const uint8_t*>(batch), sizeof(Batch) - sizeof(uint32_t));
  if (expected != batch->crc32) return StoreStatus::IntegrityFailed;
  for (uint8_t i = 0; i < batch->count; ++i) if (!valid_summary(batch->entries[i])) return StoreStatus::Corrupt;
  return StoreStatus::Ok;
}

bool PersistentTelemetryJournal::valid_summary(const PersistentTelemetrySummary& summary) const {
  if (summary.boot_id[16] != '\0' || summary.sample_count == 0) return false;
  for (uint8_t i = 0; i < 16; ++i) {
    const char c = summary.boot_id[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  }
  if (!isfinite(summary.battery_voltage)) return false;
  for (uint8_t i = 0; i < 3; ++i) if (!isfinite(summary.acceleration[i]) || !isfinite(summary.angular_velocity[i])) return false;
  return true;
}

StoreStatus PersistentTelemetryJournal::initialize() {
  ordered_count_ = 0; head_entry_ = 0; staging_count_ = 0; next_generation_ = 1;
  Batch batches[kPersistentTelemetrySlotCount];
  uint32_t keys[kPersistentTelemetrySlotCount];
  uint8_t valid = 0;
  for (uint8_t i = 0; i < kPersistentTelemetrySlotCount; ++i) {
    const uint32_t key = ApplicationNvmKeys::kTelemetryJournalSlots[i];
    StoreStatus status = read_batch(key, &batches[valid]);
    if (status == StoreStatus::Ok) keys[valid++] = key;
    else if (status != StoreStatus::NotFound) backend_.remove(key);
  }
  for (uint8_t i = 1; i < valid; ++i) {
    Batch batch = batches[i]; uint32_t key = keys[i]; uint8_t j = i;
    while (j > 0 && generation_before(batch.generation, batches[j - 1].generation)) {
      batches[j] = batches[j - 1]; keys[j] = keys[j - 1]; --j;
    }
    batches[j] = batch; keys[j] = key;
  }
  for (uint8_t i = 0; i < valid; ++i) ordered_keys_[i] = keys[i];
  ordered_count_ = valid;
  if (valid) next_generation_ = batches[valid - 1].generation + 1;
  last_status_ = StoreStatus::Ok;
  return last_status_;
}

StoreStatus PersistentTelemetryJournal::append(const PersistentTelemetrySummary& summary) {
  if (!valid_summary(summary)) return last_status_ = StoreStatus::InvalidArgument;
  if (staging_count_ >= kPersistentTelemetryBatchSize) {
    StoreStatus pending = commit_staging();
    if (pending != StoreStatus::Ok) { ++dropped_count_; return last_status_ = pending; }
  }
  staging_[staging_count_++] = summary;
  if (staging_count_ < kPersistentTelemetryBatchSize) return last_status_ = StoreStatus::Ok;
  return commit_staging();
}

void PersistentTelemetryJournal::remove_oldest_slot() {
  if (!ordered_count_) return;
  Batch oldest = {};
  if (read_batch(ordered_keys_[0], &oldest) == StoreStatus::Ok) dropped_count_ += oldest.count;
  backend_.remove(ordered_keys_[0]);
  for (uint8_t i = 1; i < ordered_count_; ++i) ordered_keys_[i - 1] = ordered_keys_[i];
  --ordered_count_; head_entry_ = 0;
}

StoreStatus PersistentTelemetryJournal::commit_staging() {
  if (!staging_count_) return last_status_ = StoreStatus::Ok;
  if (ordered_count_ == kPersistentTelemetrySlotCount) remove_oldest_slot();
  uint32_t key = 0;
  for (uint8_t candidate = 0; candidate < kPersistentTelemetrySlotCount && !key; ++candidate) {
    const uint32_t possible = ApplicationNvmKeys::kTelemetryJournalSlots[candidate];
    bool in_use = false;
    for (uint8_t existing = 0; existing < ordered_count_; ++existing) {
      if (ordered_keys_[existing] == possible) { in_use = true; break; }
    }
    if (!in_use) key = possible;
  }
  if (!key) return last_status_ = StoreStatus::StorageUnavailable;
  Batch batch = {};
  batch.magic = kJournalMagic; batch.generation = next_generation_++; batch.version = kJournalVersion;
  batch.count = staging_count_;
  memcpy(batch.entries, staging_, staging_count_ * sizeof(PersistentTelemetrySummary));
  batch.crc32 = persistent_crc32(reinterpret_cast<const uint8_t*>(&batch), sizeof(Batch) - sizeof(uint32_t));
  StoreStatus status = backend_.write(key, reinterpret_cast<const uint8_t*>(&batch), sizeof(Batch));
  if (status != StoreStatus::Ok) return last_status_ = status;
  Batch verified = {};
  status = read_batch(key, &verified);
  if (status != StoreStatus::Ok || verified.generation != batch.generation) return last_status_ = StoreStatus::ReadbackFailed;
  ordered_keys_[ordered_count_++] = key;
  staging_count_ = 0; memset(staging_, 0, sizeof(staging_));
  return last_status_ = StoreStatus::Ok;
}

bool PersistentTelemetryJournal::peek(PersistentTelemetrySummary* summary) const {
  if (!summary || !ordered_count_) return false;
  Batch batch = {};
  if (read_batch(ordered_keys_[0], &batch) != StoreStatus::Ok || head_entry_ >= batch.count) return false;
  *summary = batch.entries[head_entry_];
  return true;
}

StoreStatus PersistentTelemetryJournal::acknowledge(const char* boot_id, uint32_t sequence_number, bool* matched) {
  if (matched) *matched = false;
  while (ordered_count_) {
    PersistentTelemetrySummary head = {};
    if (!peek(&head) || !same_boot_id(head.boot_id, boot_id) || head.sequence_number > sequence_number) break;
    if (matched) *matched = true;
    Batch batch = {};
    StoreStatus status = read_batch(ordered_keys_[0], &batch);
    if (status != StoreStatus::Ok) return last_status_ = status;
    ++head_entry_;
    if (head_entry_ >= batch.count) {
      status = backend_.remove(ordered_keys_[0]);
      if (status != StoreStatus::Ok && status != StoreStatus::NotFound) return last_status_ = status;
      for (uint8_t i = 1; i < ordered_count_; ++i) ordered_keys_[i - 1] = ordered_keys_[i];
      --ordered_count_; head_entry_ = 0;
    }
  }
  return last_status_ = StoreStatus::Ok;
}

void PersistentTelemetryJournal::discard_staging() {
  staging_count_ = 0; memset(staging_, 0, sizeof(staging_));
}

uint8_t PersistentTelemetryJournal::persisted_count() const {
  uint8_t count = 0;
  for (uint8_t i = 0; i < ordered_count_; ++i) {
    Batch batch = {};
    if (read_batch(ordered_keys_[i], &batch) == StoreStatus::Ok) count += batch.count;
  }
  return count > head_entry_ ? count - head_entry_ : 0;
}
