# System architecture

```mermaid
flowchart LR
  SP[sensor_package\nMG24 firmware] <-->|shared_protocol| GW[gateway\nRaspberry Pi BLE/API/SQLite]
  GW --> UI[Browser dashboard]
```

The sensor package owns code executing on or directly programming the MG24. The gateway owns Raspberry Pi runtime and presentation. Shared protocol schemas are the sole cross-component data contract.
