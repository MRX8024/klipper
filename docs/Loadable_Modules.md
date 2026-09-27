# Loadable modules

This document describes how micro-controller code is loaded into the
micro-controller at run-time. A loadable module is regular Klipper
micro-controller C code (for example, `src/sensor_adxl345.c`) that the
host compiles when Klipper starts and uploads into the
micro-controller ram. The module's commands, responses, tasks, and
timers work the same as they would if the code were built into the
firmware.

The loader is available on the rp2040 and rp2350.

## Firmware

The loader is enabled with "Support loading code modules at run-time"
(`CONFIG_WANT_LOADER`) in `make menuconfig`. When it is enabled, the
"Optional features" menu is available, so that code which is loaded
at run-time (such as "Support adxl accelerometers") can be removed
from the firmware.

### Exported functions

A module may only call firmware functions that are exported with
`DECL_LOADER_EXPORT()` (see `src/loader.c`). The declaration stores the
address of the function in a constant, and `scripts/buildcommands.py`
creates a `loader_export_table[]` from these constants. The exported
names are stored, in table order, in the `loader_exports` field of the
data dictionary. The micro-controller reports the addresses of the
exported functions with the `loader_query_exports` command.

### Memory

The module (its code, data, and bss) is allocated from the dynamic
memory pool, which also holds the oids and the move queue. Only the
memory needed by the module is used. The module must be loaded before
the micro-controller is configured.

### Commands

* `loader_query`: Reports the loader state (0=empty, 1=loading,
  2=active), the address the module is (or will be) placed at, the
  number of bytes that may be allocated at that address, and the size
  and crc32 of the loaded image.
* `loader_query_exports offset=<n>`: Reports the addresses of the
  exported functions starting at the given table index.
* `loader_begin size=<n> mem_size=<n>`: Starts the upload of an image
  of `size` bytes and allocates `mem_size` bytes (the image and its
  bss).
* `loader_write offset=<n> data=<data>`: Stores image data.
* `loader_finish crc=<n>`: Validates the image and its header, clears
  the module's bss, runs the module's init functions, and activates
  the module.

A module can be loaded once after each micro-controller reset.

### Module image

The image starts with a `struct loader_module_header` (see
`src/loader.h`) that contains the address the image was linked for,
the end of the image and of its bss, the module's init, task, and
shutdown functions, and the module's command table.

Module commands use message ids that follow the ids of the firmware.
If a command id is not in the firmware's command table, the command is
dispatched to the module's command table. The module's task and
shutdown functions are called from the loader's `DECL_TASK()` and
`DECL_SHUTDOWN()` functions.

## Host

Modules are declared with `[mcu_module]` config sections (see the
[config reference](Config_Reference.md#mcu_module)). For example:

```
[mcu_module adxl345]
sources: src/sensor_adxl345.c, src/sensor_bulk.c
```

All modules of a micro-controller are built into a single image during
the "klippy:mcu_identify" phase (`klippy/mcu_loader.py`):

1. The firmware build configuration is recreated from the `kconfig`
   field of the micro-controller's data dictionary.
2. The image address and the addresses of the exported functions are
   queried from the micro-controller.
3. `make module` compiles the module sources with the firmware
   compiler flags. `scripts/buildmodule.py` processes the module's
   compile time requests: new commands and responses get message ids
   that follow the firmware's ids, messages that the firmware already
   defines keep their firmware ids, and new static strings get ids
   that follow the firmware's static strings. It generates the
   module's command table, message encoders, call lists, and header,
   along with a linker script that places the image at the queried
   address and defines the addresses of the exported functions. It is
   an error for a module to define a command that the firmware already
   implements.
4. The linked image is uploaded, and the messages of the module are
   added to the host's message parser.

The build is done in a directory of each micro-controller in
`~/.cache/klipper/modules/`, and the build output is written to
`build.log` in that directory.

If the micro-controller is already running the same image, the image
is not uploaded again. If it is running a different image, or it is
already configured, Klipper performs a `FIRMWARE_RESTART` before the
image is loaded.

## Firmware interface check

A module is compiled with the host's copy of the firmware headers.
`scripts/loaderabi.py` calculates a hash for each exported function
from its prototype (from the `-aux-info` output of gcc) and the
definitions of all the types reachable from it (structs, unions,
enums, and typedefs). An additional hash covers the types used by the
module glue code. Comments, parameter names, and unrelated
declarations do not change the hashes. Macros are not covered.

The firmware build stores its hashes in the `loader_abi` field of the
data dictionary. The host calculates the hashes of its own sources and
compares the hashes of the exported functions the module uses. If any
differ, Klipper reports an "MCU Protocol error" that lists the changed
functions.

## Module code

Modules use the regular micro-controller declarations:
`DECL_COMMAND()`, `sendf()`, `output()`, `shutdown()`, `DECL_TASK()`,
`DECL_INIT()`, `DECL_SHUTDOWN()`, `DECL_CONSTANT()`, and
`DECL_ENUMERATION()`. `DECL_INIT()` functions run when the module is
loaded. Modules may not declare interrupt handlers
(`DECL_ARMCM_IRQ()`) or initial pins (`DECL_INITIAL_PINS()`).

Code that is not available in the firmware (such as
`src/sensor_bulk.c`) may be included in the module's list of sources.
Modules run from ram and call firmware functions using long calls. The
libgcc and libc code a module uses is linked into the module.
