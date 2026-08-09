#pragma once

#include <stddef.h>
#include <stdint.h>

enum class StoreStatus : uint8_t {
  Ok, NotFound, Unprovisioned, Corrupt, UnsupportedVersion, InvalidArgument,
  SizeExceeded, StorageUnavailable, ReadFailed, WriteFailed, ReadbackFailed,
  IntegrityFailed, GenerationConflict, MigrationRequired, RecoveredFromPrevious
};
enum class PersistentRecordType : uint8_t { Identity = 1, Configuration = 2, StoreMetadata = 3 };

struct RecordView {
  PersistentRecordType type;
  uint32_t generation;
  uint16_t flags;
  const uint8_t* payload;
  uint16_t payload_size;
};

constexpr size_t kPersistentHeaderSize = 24;
constexpr size_t kPersistentMaxPayload = 220;
constexpr size_t kPersistentMaxRecord = kPersistentHeaderSize + kPersistentMaxPayload;

uint32_t persistent_crc32(const uint8_t* data, size_t size);
bool generation_newer(uint32_t candidate, uint32_t reference);
StoreStatus encode_persistent_record(PersistentRecordType type, uint32_t generation, uint16_t flags,
                                     const uint8_t* payload, uint16_t payload_size,
                                     uint8_t* output, size_t capacity, size_t* output_size);
StoreStatus decode_persistent_record(const uint8_t* record, size_t size, PersistentRecordType expected, RecordView* output);
const char* store_status_name(StoreStatus status);
