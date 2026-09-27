# Klipper build system
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Output directory
OUT=out/

# Kconfig includes
export KCONFIG_CONFIG     := $(CURDIR)/.config
-include $(KCONFIG_CONFIG)

# Common command definitions
CC=$(CROSS_PREFIX)gcc
AS=$(CROSS_PREFIX)as
LD=$(CROSS_PREFIX)ld
OBJCOPY=$(CROSS_PREFIX)objcopy
OBJDUMP=$(CROSS_PREFIX)objdump
STRIP=$(CROSS_PREFIX)strip
CPP=cpp
PYTHON=python3

# Source files
src-y =
dirs-y = src

# Default compiler flags
cc-option=$(shell if test -z "`$(1) $(2) -S -o /dev/null -xc /dev/null 2>&1`" \
    ; then echo "$(2)"; else echo "$(3)"; fi ;)

CFLAGS := -iquote $(OUT) -iquote src -iquote $(OUT)board-generic/ \
		-std=gnu11 -O2 -MD -Wall \
		-Wold-style-definition $(call cc-option,$(CC),-Wtype-limits,) \
    -ffunction-sections -fdata-sections -fno-delete-null-pointer-checks
CFLAGS += -flto=auto -fwhole-program -fno-use-linker-plugin -ggdb3

OBJS_klipper.elf = $(patsubst %.c, $(OUT)src/%.o,$(src-y))
OBJS_klipper.elf += $(OUT)compile_time_request.o
CFLAGS_klipper.elf = $(CFLAGS) -Wl,--gc-sections

CPPFLAGS = -I$(OUT) -P -MD -MT $@

# Default targets
target-y := $(OUT)klipper.elf

all:

# Run with "make V=1" to see the actual compile commands
ifdef V
Q=
else
Q=@
MAKEFLAGS += --no-print-directory
endif

# Include board specific makefile
include src/Makefile
-include src/$(patsubst "%",%,$(CONFIG_BOARD_DIRECTORY))/Makefile

################ Main build rules

$(OUT)%.o: %.c $(OUT)autoconf.h
	@echo "  Compiling $@"
	$(Q)$(CC) $(CFLAGS) -c $< -o $@

$(OUT)%.ld: %.lds.S $(OUT)autoconf.h
	@echo "  Preprocessing $@"
	$(Q)$(CPP) -I$(OUT) -P -MD -MT $@ $< -o $@

$(OUT)klipper.elf: $(OBJS_klipper.elf)
	@echo "  Linking $@"
	$(Q)$(CC) $(OBJS_klipper.elf) $(CFLAGS_klipper.elf) -o $@
	$(Q)scripts/check-gcc.sh $@ $(OUT)compile_time_request.o

################ Compile time requests

# Hashes of the firmware interface used by run-time loaded code
LOADER_ABI=$(if $(CONFIG_WANT_LOADER),$(OUT)loader_abi.json)

$(OUT)%.o.ctr: $(OUT)%.o
	$(Q)$(OBJCOPY) -j '.compile_time_request' -O binary $^ $@

$(OUT)compile_time_request.o: $(patsubst %.c, $(OUT)src/%.o.ctr,$(src-y)) $(LOADER_ABI) ./scripts/buildcommands.py
	@echo "  Building $@"
	$(Q)cat $(patsubst %.c, $(OUT)src/%.o.ctr,$(src-y)) | tr -s '\0' '\n' > $(OUT)compile_time_request.txt
	$(Q)$(PYTHON) lib/kconfiglib/savedefconfig.py --kconfig src/Kconfig --out $(OUT)defconfig
	$(Q)$(PYTHON) ./scripts/buildcommands.py -d $(OUT)klipper.dict -k $(OUT)defconfig $(if $(LOADER_ABI),-a $(LOADER_ABI)) -t "$(CC);$(AS);$(LD);$(OBJCOPY);$(OBJDUMP);$(STRIP)" $(OUT)compile_time_request.txt $(OUT)compile_time_request.c
	$(Q)$(CC) $(CFLAGS) -c $(OUT)compile_time_request.c -o $@

################ Run-time loaded modules

$(OUT)loader_abi.json: src/loader.c $(OUT)autoconf.h ./scripts/loaderabi.py
	@echo "  Building $@"
	$(Q)$(CC) $(filter-out -MD,$(CFLAGS)) -E -MD -MP -MT $@ -MF $(OUT)loader_abi.dep src/loader.c -o $(OUT)loader_abi.i
	$(Q)$(CC) $(filter-out -MD,$(CFLAGS)) -fsyntax-only -aux-info $(OUT)loader_abi.aux src/loader.c
	$(Q)$(PYTHON) ./scripts/loaderabi.py $(OUT)loader_abi.i $(OUT)loader_abi.aux $@

# Not a .d file (create-board-link removes those)
-include $(OUT)loader_abi.dep

# The host (klippy/mcu_loader.py) provides MODULE_SRCS along with the
# sources.txt, firmware.dict, and loader.json files in $(MODULE_OUT)
MODULE_OUT=$(OUT)module/
MODULE_OBJS=$(patsubst %.c,$(MODULE_OUT)obj%.o,$(abspath $(MODULE_SRCS)))
MODULE_INPUTS=$(MODULE_OUT)sources.txt $(MODULE_OUT)firmware.dict \
    $(MODULE_OUT)loader.json

MODULE_LDFLAGS += -Wl,--build-id=none
MODULE_LDFLAGS += $(shell $(LD) --no-warn-rwx-segments -v >/dev/null 2>&1 \
    && echo -Wl,--no-warn-rwx-segments)
CFLAGS_module = $(CFLAGS) $(MODULE_CFLAGS)
CFLAGS_module.elf = $(CFLAGS_module) $(MODULE_LDFLAGS) -nostdlib \
    -Wl,--gc-sections -Wl,-T,$(MODULE_OUT)module.ld

$(MODULE_OUT)obj%.o: %.c $(OUT)autoconf.h
	@echo "  Compiling $@"
	$(Q)mkdir -p $(dir $@)
	$(Q)$(CC) $(CFLAGS_module) -c $< -o $@

$(MODULE_OUT)module_ctr.c: $(patsubst %,%.ctr,$(MODULE_OBJS)) $(MODULE_INPUTS) ./scripts/buildmodule.py ./scripts/buildcommands.py
	@echo "  Building $@"
	$(Q)cat $(patsubst %,%.ctr,$(MODULE_OBJS)) /dev/null | tr -s '\0' '\n' > $(MODULE_OUT)compile_time_request.txt
	$(Q)$(PYTHON) ./scripts/buildmodule.py -d $(MODULE_OUT)firmware.dict -i $(MODULE_OUT)loader.json -o $(MODULE_OUT)module.json -l $(MODULE_OUT)module.ld $(MODULE_OUT)compile_time_request.txt $@

$(MODULE_OUT)module_ctr.o: $(MODULE_OUT)module_ctr.c
	$(Q)$(CC) $(CFLAGS_module) -c $< -o $@

$(MODULE_OUT)module.elf: $(MODULE_OBJS) $(MODULE_OUT)module_ctr.o
	@echo "  Linking $@"
	$(Q)$(CC) $(MODULE_OBJS) $(MODULE_OUT)module_ctr.o $(CFLAGS_module.elf) $(MODULE_LIBS) -o $@
	$(Q)$(PYTHON) ./scripts/buildmodule.py -i $(MODULE_OUT)loader.json -c $@ -s $(MODULE_OUT)module_imports.json

$(MODULE_OUT)module.bin: $(MODULE_OUT)module.elf
	@echo "  Creating module image $@"
	$(Q)$(OBJCOPY) -O binary -j .text $< $@

module: $(MODULE_OUT)module.bin $(OUT)loader_abi.json

-include $(patsubst %.o,%.d,$(MODULE_OBJS))

################ Auto generation of "board/" include file link

create-board-link:
	@echo "  Creating symbolic link $(OUT)board"
	$(Q)mkdir -p $(addprefix $(OUT), $(dirs-y))
	$(Q)rm -f $(OUT)*.d $(patsubst %,$(OUT)%/*.d,$(dirs-y))
	$(Q)rm -f $(OUT)board
	$(Q)ln -sf $(CURDIR)/src/$(CONFIG_BOARD_DIRECTORY) $(OUT)board
	$(Q)mkdir -p $(OUT)board-generic
	$(Q)rm -f $(OUT)board-generic/board
	$(Q)ln -sf $(CURDIR)/src/generic $(OUT)board-generic/board

# Hack to rebuild OUT directory and reload make dependencies on Kconfig change
$(OUT)board-link: $(KCONFIG_CONFIG)
	$(Q)mkdir -p $(OUT)
	$(Q)echo "# Makefile board-link rule" > $@
	$(Q)$(MAKE) create-board-link
include $(OUT)board-link

################ Kconfig rules

$(OUT)autoconf.h: $(KCONFIG_CONFIG)
	@echo "  Building $@"
	$(Q)mkdir -p $(OUT)
	$(Q) KCONFIG_AUTOHEADER=$@ $(PYTHON) lib/kconfiglib/genconfig.py src/Kconfig

$(KCONFIG_CONFIG) olddefconfig: src/Kconfig
	$(Q)$(PYTHON) lib/kconfiglib/olddefconfig.py src/Kconfig

menuconfig:
	$(Q)$(PYTHON) lib/kconfiglib/menuconfig.py src/Kconfig

################ Generic rules

# Make definitions
.PHONY : all clean distclean olddefconfig menuconfig create-board-link module FORCE
.DELETE_ON_ERROR:

all: $(target-y)

clean:
	$(Q)rm -rf $(OUT)

distclean: clean
	$(Q)rm -f .config .config.old

-include $(OUT)*.d $(patsubst %,$(OUT)%/*.d,$(dirs-y))
