# Vibration acquisition and processing foundation

The sensor-side vibration module and retained
`xiao_mg24_vibration_diagnostic` sketch characterize and process the XIAO MG24
Sense onboard LSM6DS3 without changing production BLE, identity, provisioning,
or NVM behavior. No physical rate or buffer-test output has been supplied or
committed. The configured values in this document are not measured hardware
performance.

## Physical signals and configuration

The accelerometer measures linear acceleration. Its raw output includes
gravity. The gyroscope measures angular velocity, not angular acceleration.
The repository pins Silicon Labs Arduino core 4.0.0 and Seeed Arduino LSM6DS3
2.0.7.

| Signal | Range | Configured ODR | Configured bandwidth | Raw scale |
| --- | ---: | ---: | ---: | ---: |
| acceleration | +/-16 g | 416 Hz | 100 Hz | 16/32768 g/count |
| angular velocity | +/-2000 dps | 416 Hz | not exposed/applied by this library | 2000/32768 dps/count |

At 416 Hz the theoretical Nyquist limit is 208 Hz. The 100 Hz accelerometer
bandwidth is below Nyquist and limits the useful acceleration band. It helps
reduce higher-frequency content but must not be described as proven complete
anti-alias rejection. Raising ODR without reviewing the sensor bandwidth would
increase CPU, I2C, and RAM demands without necessarily adding useful bandwidth.

## Acquisition design

`Lsm6ds3CoherentReader` waits until both accelerometer and gyro data-ready bits
are set, then reads the contiguous register region from `OUTX_L_G` through
`OUTZ_H_XL` in one 12-byte I2C operation. The result is stored as one POD
`ImuRawSample` containing gyro XYZ followed by acceleration XYZ `int16_t`
counts. Named constants isolate register details in the acquisition adapter.

This is preferred over six independent library reads because it reduces I2C
transactions, axis skew, and the chance of combining different sensor update
cycles. It still requires physical rate, missed-sample, and coherence testing.

The library FIFO helper can enable accel and gyro decimation and read FIFO
words, but it hard-codes mode 6 and depends on an implicit gyro-then-accel word
pattern. It exposes no typed frame/tag validation or bounded batch decoder.
Direct FIFO parsing would require a device-revision-aware design and hardware
tests. FIFO is therefore deliberately disabled in this phase.

## Initial sample-rate and window recommendation

The actual library accepts gyro ODRs 13, 26, 52, 104, 208, 416, 833, and 1660
Hz. The accelerometer also accepts higher settings. Plausible choices are:

| ODR | Nyquist | 256-sample duration | 256-bin resolution | Relative load |
| ---: | ---: | ---: | ---: | --- |
| 104 Hz | 52 Hz | 2.462 s | 0.406 Hz | low, but excludes much of the current 100 Hz accel band |
| 208 Hz | 104 Hz | 1.231 s | 0.8125 Hz | moderate; Nyquist barely exceeds configured bandwidth |
| 416 Hz | 208 Hz | 0.615 s | 1.625 Hz | moderate/high; preserves the current configured band |
| 833 Hz | 416.5 Hz | 0.307 s | 3.254 Hz | higher load with no demonstrated benefit at 100 Hz bandwidth |
| 1660 Hz | 830 Hz | 0.154 s | 6.484 Hz | highest listed gyro load and unjustified here |

The initial recommendation remains **416 Hz** because it matches the current
configuration and provides margin above the 100 Hz accelerometer bandwidth.
It is a configured choice pending hardware validation, not a claim of sustained
416-sample/second operation.

At 416 Hz, window tradeoffs are:

| Samples | Duration | Resolution | Six-axis raw RAM |
| ---: | ---: | ---: | ---: |
| 256 | 0.615 s | 1.625 Hz | 3,072 B |
| 512 | 1.231 s | 0.8125 Hz | 6,144 B |
| 1024 | 2.462 s | 0.40625 Hz | 12,288 B |

The implemented 256-sample window balances RAM, processing cadence, and useful
frequency resolution. A 512- or 1024-sample window would improve low-frequency
resolution, but it also delays results and increases both acquisition and FFT
storage. Actual machine-speed requirements may justify revisiting this choice.

## Conditioning and time-domain metrics

Raw counts are deterministically converted to g and degrees/second. Each axis
then passes through a configurable first-order IIR high-pass filter:

```text
RC = 1 / (2*pi*cutoff)
dt = 1 / sample_rate
alpha = RC / (RC + dt)
y[n] = alpha * (y[n-1] + x[n] - x[n-1])
```

The diagnostic uses a 2 Hz cutoff. This is a prototype configuration intended
to reject gravity and slow bias while retaining common rotating-machine
content; it is not a universal industrial cutoff. Filter state is initialized
from the first sample of each diagnostic window, avoiding a synthetic gravity
step. The reusable processor retains filter state across contiguous production
windows unless `resetFilterState()` is explicitly called. The diagnostic resets
it because its command-driven windows are separated by unobserved acquisition
gaps.

For each conditioned acceleration and angular-velocity axis the module returns:

- mean;
- RMS;
- peak absolute value;
- peak-to-peak;
- population standard deviation;
- crest factor with safe zero handling;
- population kurtosis with safe zero-variance handling.

Acceleration RMS is therefore dynamic acceleration RMS rather than raw RMS
dominated by static 1 g gravity. Axis metrics remain available separately.
A resultant vector metric is deferred because the semantics of magnitude after
independent axis filtering and mounting/orientation need bench validation.

## FFT

No suitable embedded FFT dependency was already pinned. The module contains a
small in-place radix-2 256-point float FFT with fixed-size storage and no heap
allocation. Each acceleration axis is:

```text
high-pass conditioned -> Hann window -> FFT -> single-sided amplitude spectrum
```

The symmetric Hann window coherent gain is calculated from the actual window
sum. Non-DC amplitudes are scaled as `2 * magnitude / window_sum`, making an
on-bin sinusoid approximately recover its peak amplitude. Off-bin signals will
still exhibit leakage and scalloping loss; these are not calibrated industrial
amplitudes.

Dominant-frequency search starts at the configurable 5 Hz minimum and excludes
DC and lower bins. Five hertz is a prototype diagnostic choice, not a universal
machine threshold. Results include dominant bin, bin-center frequency, and
scaled amplitude for acceleration X/Y/Z. The full spectrum is retained only as
scratch diagnostic state and is not a BLE payload.

## RAM budget

The implemented static data budget is approximately:

| Component | Bytes |
| --- | ---: |
| 256 six-axis raw `int16_t` samples | 3,072 |
| 256 conditioned six-axis float samples | 6,144 |
| FFT real buffer | 1,024 |
| FFT imaginary buffer | 1,024 |
| FFT input scratch | 1,024 |
| Retained 128-bin amplitude spectrum | 512 |
| Six filter states | approximately 96 |
| Window result and configuration | approximately 240 |
| Total vibration static working data | approximately 13,136 |

Hann coefficients are calculated as needed, so no coefficient table is stored.
The compiled non-BLE diagnostic currently reports 32,440 bytes of global RAM
and 229,704 bytes remaining. The unchanged production BLE build previously
reported 31,504 bytes of global RAM and 230,640 bytes remaining; integrating
this module into production will require a fresh map review because those builds
do not currently contain the same globals.

A second 3,072-byte raw acquisition buffer would enable double buffering. It is
not allocated yet. The diagnostic intentionally acquires one window, stops,
and processes it so processing duration can be measured. Production integration
must compare measured processing time against acquisition timing and introduce
double buffering or FIFO only if necessary to prevent gaps.

## Diagnostic commands

Build with the non-BLE stack and the repository-local vibration library:

```powershell
arduino-cli compile `
  --fqbn "SiliconLabs:silabs:xiao_mg24:protocol_stack=none" `
  --libraries sensor_package/firmware/libraries `
  --build-path sensor_package/build_diagnostic_vibration `
  sensor_package/firmware/diagnostics/xiao_mg24_vibration_diagnostic
```

After a separately authorized upload, open USB serial at 115200 baud:

| Command | Action |
| --- | --- |
| `STATUS` | Configuration, acquisition mode, filter/FFT settings, and RAM buffer size. |
| `RATE_TEST` | Silent 1024-sample acquisition timing test. |
| `BUFFER_TEST` | Alias for one processed 256-sample window. |
| `FEATURE_TEST` | One processed window with time-domain and frequency summaries. |
| `FFT_TEST` | One processed window with feature, FFT, and total processing times. |
| `SPECTRUM` | One window plus a bounded 127-bin acceleration-Z CSV spectrum. |
| `STREAM` / `STOP` | Start/stop raw engineering-unit sample output; printing affects timing. |
| `I2C_TIMING_TEST` | Compare legacy two-transfer and combined repeated-start register reads. |
| `FIFO_TRANSPORT_TEST` | Drain one bounded 16-frame FIFO batch and report throughput/alignment. |
| `FIFO_CONTINUOUS_TEST` | Acquire and process 10 windows through FIFO plus double buffers. |
| `FIFO_CONTINUOUS_LONG_TEST` | Run the same bounded path for 100 windows. |

`FFT_TEST` prints acquisition timing separately from `feature_processing_us`,
`fft_processing_us`, and `total_window_processing_us`. No timing results are
available until this command is physically run.

## Continuous FIFO transport validation

The official Seeed XIAO MG24 Sense schematic and KiCad PCB source leave both
LSM6DS3TR-C interrupt outputs unconnected: U7 pad 4 is the unconnected INT1 net
and U7 pad 9 is the unconnected INT2 net. Therefore the retained diagnostic
does not configure or claim a data-ready/watermark interrupt.

The onboard IMU uses `Wire1` on PB2/SDA1 and PB3/SCL1. This distinction is
important: setting the clock on the external-header `Wire` instance does not
change the IMU bus. Silicon Labs core 4.0.0 provides 64-byte Wire RX/TX
buffers and supports a combined register-address write plus repeated-start
read. The Seeed LSM6DS3 library's normal register helper instead performs two
transfers. The diagnostic's isolated FIFO transport uses the combined form.

The LSM6DS3TR-C FIFO data port consists of `FIFO_DATA_OUT_L` (3Eh) and
`FIFO_DATA_OUT_H` (3Fh). Registers after 3Fh are timestamp registers, not a
linear FIFO aperture, so the safe payload is one two-byte FIFO word per
combined transaction. The reader rejects oversize destinations and short
reads. Batches contain 16 complete frames (96 words), and every batch verifies
pattern zero before and after reading. The explicit frame layout is gyro X/Y/Z
followed by accelerometer X/Y/Z.

Physical validation on the current XIAO MG24 Sense produced 5,760.9 FIFO
words/s for a 96-word batch, 2.40 times the 2,400-word/s fill rate. A 100-window
run captured and processed 25,600 frames with zero sample drops, raw-buffer
overruns, FIFO overruns, short reads, alignment errors, or read errors. The
measured effective rate was 431.297 Hz over 59.356 seconds of device time; a
host clock measured 59.406 seconds. This corroborates that the approximately
431 Hz observation is not primarily polling latency or a gross `micros()`
error, but it does not by itself identify the IMU/timebase tolerance responsible
for the difference from the nominal 416 Hz setting.

These measurements characterize the tested board/toolchain only. They do not
calibrate vibration amplitude or frequency accuracy and do not yet make this
transport part of the production BLE firmware.

## Limitations and deferred work

- Mounting, cable motion, orientation, board resonance, and excitation method
  materially affect results. A bench test is not an industrial qualification.
- Hardware sample rate, jitter, burst-read coherence, noise floor, filter
  response, and processing duration remain unverified.
- The first-order filter is not a claim of complete anti-alias protection.
- Vibration velocity RMS is deferred. Naive cumulative acceleration integration
  would drift; a validated band-limited integration design in m/s is required
  before presenting mm/s RMS.
- Angular acceleration is a noisy derivative of angular velocity and is not
  implemented as a primary metric.
- Bearing-fault diagnosis, imbalance/misalignment classification, ISO
  10816/20816 severity classes, alarm thresholds, and calibrated condition
  limits are not implemented.
- BLE/shared-protocol fields, gateway persistence, and dashboard presentation
  remain deferred.

The next phase should be physical vibration bench validation. It should measure
sustained acquisition, missed samples, noise, processing time, and response to
known excitation before integrating this pipeline into production firmware or
designing its BLE representation.

## Continuous acquisition investigation

Physical validation measured approximately 431.6 samples/second and 36.8 ms of
blocking DSP per window. A two-buffer `VibrationDoubleBuffer` now implements
explicit `FREE`, `FILLING`, `READY`, and `PROCESSING` ownership. It never
overwrites a ready/processing buffer and counts captured/dropped samples,
completed/processed windows, swaps, and overruns. Each window records start/end
microseconds, count, and effective rate.

The processor accepts both configured and effective rates. Per-window rate is
calculated as `(N-1)*1e6/(end-start)` and an 80/20 exponential smoother drives
filter coefficients and FFT bin scaling. Configured 416 Hz remains separate
metadata.

The XIAO MG24 Arduino variant and repository do not identify a routed LSM6DS3
INT1/INT2 pin. Although the core implements GPIO interrupts, no interrupt pin
was invented and no I2C operation was moved into an ISR. Cooperative processing
hooks service acquisition between bounded filtering, feature, and FFT chunks.

The 10-window hardware test failed continuity. The I2C status-plus-sample
service averaged about 1.45 ms. Servicing every sample inflated DSP from 36.8 ms
to about 1.25 seconds/window, causing approximately 2,840 drops and ten buffer
overruns per test. Requesting 400 kHz through `Wire.setClock()` did not
materially alter the measured service time. The maximum compute-only block was
under 0.2 ms; bus service throughput, not DSP chunk size, was the limiting
factor. A 100-window run was intentionally skipped after the acceptance target
failed.

Host and device timing over a roughly 13-second continuous test differed by
about 0.6%. This rules out a gross `micros()` scale error but does not isolate
the configured-416-versus-measured-431.6 discrepancy. IMU ODR tolerance,
timebase tolerance, and methodology remain candidates; known-frequency
calibration is still required.

### FIFO validation

The bounded `FIFO_TEST` configured continuous FIFO operation at 400 Hz with
32 six-word gyro+accel frames and a 192-word watermark. Hardware showed:

- watermark at exactly 192 words;
- pattern zero before and after complete frames;
- repeatable gyro-X/Y/Z then accel-X/Y/Z word semantics under stationary gravity;
- no bounded-test overrun;
- 173 ms to read 192 words through the library's two single-byte reads;
- 107 ms using one two-byte register-region read per word;
- 282 words already queued after the optimized drain.

That first FIFO experiment used the external/header `Wire` bus and is retained
here as historical diagnostic evidence. Follow-up board-file and physical work
identified the onboard LSM6DS3 bus as `Wire1` (`PB2`/`PB3`) and established that
INT1/INT2 are not routed. The corrected bounded transport drains 96 words (16
frames) per batch at about 5,760.9 words/second, approximately 2.40 times the
2,400-word/second fill rate. A 100-window hardware run captured 25,600 samples
with zero drops, buffer/FIFO overruns, alignment errors, short reads, or read
errors. Maximum observed occupancy was 144 words (24 frames).

Production therefore uses the validated `Wire1` FIFO transport plus double
buffering; it does not use an invented interrupt path. The nominal 416 Hz versus
measured approximately 431.3 Hz discrepancy remains unresolved, so configured
and effective rates remain separate and effective timing drives FFT bin scaling.

Diagnostic evidence is stored under ignored `test_output/vibration_bench/`.
