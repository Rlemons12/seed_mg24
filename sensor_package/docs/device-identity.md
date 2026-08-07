# Device identity

`node_id` permanently identifies an MG24 board. It is distinct from an attached sensor's `device_id`, editable `display_name`, BLE address, and BLE advertised name. Compile-time identity is the initial production method: maintain an external deployment inventory and protected backup, refuse duplicate assignments, and never generate identity at build or boot.

For replacement, retire the failed board record, restore the intended node identity deliberately, flash, verify no other node advertises that identity, and restore only configuration compatible with the reported schema. The ignored local header is a working copy—not the authoritative identity backup.
