# Verified XIAO MG24 NVM3 integration

This implementation targets the installed `SiliconLabs:silabs@4.0.0` XIAO MG24 `ble_silabs` variant. The authoritative headers are `nvm3_default.h`, `nvm3_default_config.h`, and `nvm3_generic.h` supplied by that core.

The Arduino runtime initializes `nvm3_defaultHandle` before `setup()`. This is confirmed by the core EEPROM implementation, which directly calls `nvm3_readData`, `nvm3_writeData`, `nvm3_repackNeeded`, and `nvm3_repack` on that handle. Although `nvm3_initDefault()` is declared, it is not linked into this board variant; the application therefore checks the initialized handle instead of initializing a second instance.

Selected APIs:

- `nvm3_getObjectInfo(handle, key, &type, &length)`
- `nvm3_readData(handle, key, destination, length)`
- `nvm3_writeData(handle, key, source, length)`
- `nvm3_deleteObject(handle, key)`
- `nvm3_repackNeeded(handle)` and `nvm3_repack(handle)`

Keys are 20-bit `uint32_t` values (`0x00000-0xFFFFF`). The core explicitly designates `0x00000-0x0FFFF` as the user domain. Its EEPROM emulation maps 10,240 bytes into low keys starting at zero using 254-byte objects, currently through key `0x0028`. The application uses only `0x0FF00-0x0FF05`. Bluetooth stack state is outside the user domain. The default region is 40,960 bytes and the default maximum object is 254 bytes; application records are capped at 244 bytes.

NVM3 documents recovery from interrupted object operations when opening an established instance. The application additionally uses A/B generations, CRC32, and read-back. Mock tests cover logical failures, but real power loss, flash exhaustion/repack behavior, and application-upload retention remain hardware checks.
