#pragma once
#include <stddef.h>
#include <stdint.h>
#include "persistent_record.h"

class NvmBackend {
 public:
  virtual ~NvmBackend() {}
  virtual StoreStatus initialize() = 0;
  virtual StoreStatus read(uint32_t key, uint8_t* data, size_t capacity, size_t* size) = 0;
  virtual StoreStatus write(uint32_t key, const uint8_t* data, size_t size) = 0;
  virtual StoreStatus remove(uint32_t key) = 0;
};

#ifdef ARDUINO
class SiliconLabsNvm3Backend : public NvmBackend {
 public:
  StoreStatus initialize() override;
  StoreStatus read(uint32_t key,uint8_t* data,size_t capacity,size_t* size) override;
  StoreStatus write(uint32_t key,const uint8_t* data,size_t size) override;
  StoreStatus remove(uint32_t key) override;
};
#endif
