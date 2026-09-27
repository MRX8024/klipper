// Support for loading code into the micro-controller at run-time
//
// Copyright (C) 2026  Maksim Bolgov <maksim8024@gmail.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h> // memcpy
#include "autoconf.h" // CONFIG_WANT_SPI
#include "basecmd.h" // alloc_chunk
#include "board/internal.h" // __DSB
#include "board/irq.h" // irq_disable
#include "board/misc.h" // dynmem_end
#include "board/pgm.h" // READP
#include "command.h" // DECL_COMMAND
#include "loader.h" // loader_lookup_parser
#include "sched.h" // DECL_TASK

// Alignment of the module start address and size
#define LOADER_ALIGN 8


/****************************************************************
 * Module memory
 ****************************************************************/

// The module is allocated from the dynamic memory pool (the pool that
// also holds the oids and the move queue).  Only the memory needed by
// the module (code, data, and bss) is taken from the pool.
static uint8_t *loader_base;
static uint32_t loader_size;

// Return the address the module is (or will be) placed at
static uint8_t *
loader_mem_base(void)
{
    if (loader_base)
        return loader_base;
    return (void*)ALIGN((size_t)alloc_next(), LOADER_ALIGN);
}

// Return the number of bytes that may be allocated at loader_mem_base()
static uint32_t
loader_mem_avail(void)
{
    uint8_t *base = loader_mem_base(), *end = dynmem_end();
    if (loader_base && alloc_next() != loader_base + loader_size)
        // Other allocations follow the module memory - it can't grow
        return loader_size;
    return end > base ? end - base : 0;
}

// Allocate 'size' bytes at loader_mem_base()
static void
loader_mem_alloc(uint32_t size)
{
    if (loader_base && size <= loader_size)
        return;
    uint8_t *base = loader_mem_base();
    alloc_chunk(base + size - (uint8_t*)alloc_next());
    loader_base = base;
    loader_size = size;
}


/****************************************************************
 * Module loading
 ****************************************************************/

enum { LS_EMPTY, LS_LOADING, LS_ACTIVE };

static uint8_t loader_state;
static uint32_t image_size, image_crc;
static const struct loader_module_header *active_module;

// Standard crc32 (same as python's zlib.crc32)
static uint32_t
loader_crc32(uint8_t *buf, uint32_t len)
{
    uint32_t crc = 0xffffffff;
    while (len--) {
        crc ^= *buf++;
        uint_fast8_t i;
        for (i=0; i<8; i++)
            crc = (crc >> 1) ^ (0xedb88320 & -(crc & 1));
    }
    return ~crc;
}

void
command_loader_query(uint32_t *args)
{
    void *base = loader_mem_base();
    sendf("loader_state state=%c avail=%u image_size=%u image_crc=%u"
          " base=%*s", loader_state, loader_mem_avail(), image_size
          , image_crc, (int)sizeof(base), (uint8_t*)&base);
}
DECL_COMMAND_FLAGS(command_loader_query, HF_IN_SHUTDOWN, "loader_query");

void
command_loader_query_exports(uint32_t *args)
{
    uint_fast16_t offset = args[0], count = READP(loader_export_count);
    uint8_t data[32];
    uint_fast8_t len = 0;
    while (offset + len / sizeof(void*) < count
           && len + sizeof(void*) <= sizeof(data)) {
        const void *addr = *READP(loader_export_table[offset
                                                      + len / sizeof(void*)]);
        memcpy(&data[len], &addr, sizeof(addr));
        len += sizeof(addr);
    }
    sendf("loader_exports offset=%hu count=%hu data=%*s"
          , offset, count, len, data);
}
DECL_COMMAND_FLAGS(command_loader_query_exports, HF_IN_SHUTDOWN
                   , "loader_query_exports offset=%hu");

void
command_loader_begin(uint32_t *args)
{
    uint32_t size = args[0], mem_size = ALIGN(args[1], LOADER_ALIGN);
    if (loader_state == LS_ACTIVE)
        shutdown("Loadable module already active");
    if (size < sizeof(struct loader_module_header) || size > mem_size)
        shutdown("Invalid loadable module size");
    if (mem_size > loader_mem_avail())
        shutdown("Not enough memory for loadable module");
    loader_mem_alloc(mem_size);
    loader_state = LS_LOADING;
    image_size = size;
    image_crc = 0;
}
DECL_COMMAND(command_loader_begin, "loader_begin size=%u mem_size=%u");

void
command_loader_write(uint32_t *args)
{
    uint32_t offset = args[0];
    uint_fast8_t len = args[1];
    uint8_t *data = command_decode_ptr(args[2]);
    if (loader_state != LS_LOADING || offset > image_size
        || len > image_size - offset)
        shutdown("Invalid loadable module write");
    memcpy(&loader_base[offset], data, len);
}
DECL_COMMAND(command_loader_write, "loader_write offset=%u data=%*s");

// Verify the module header describes an image that fits its memory
static int
loader_check_header(const struct loader_module_header *h)
{
    uint8_t *start = loader_base, *end = start + loader_size;
    uint8_t *image_end = h->image_end, *bss_start = h->bss_start;
    uint8_t *bss_end = h->bss_end;
    return (h->magic == LOADER_MAGIC && h->abi_version == LOADER_ABI_VERSION
            && h->base == start && image_end == start + image_size
            && bss_start >= image_end && bss_end >= bss_start
            && bss_end <= end);
}

void
command_loader_finish(uint32_t *args)
{
    if (loader_state != LS_LOADING)
        shutdown("Invalid loadable module finish");
    uint32_t crc = loader_crc32(loader_base, image_size);
    const struct loader_module_header *h = (void*)loader_base;
    if (crc != args[0] || !loader_check_header(h)) {
        loader_state = LS_EMPTY;
        shutdown("Invalid loadable module image");
    }
    memset(h->bss_start, 0, (uint8_t*)h->bss_end - (uint8_t*)h->bss_start);
    // Make sure the cpu does not execute stale instructions
    __DSB();
    __ISB();
    if (h->run_initfuncs)
        h->run_initfuncs();
    image_crc = crc;
    loader_state = LS_ACTIVE;
    barrier();
    active_module = h;
}
DECL_COMMAND(command_loader_finish, "loader_finish crc=%u");


/****************************************************************
 * Module runtime hooks
 ****************************************************************/

// Find the handler of a command implemented by the active module
const struct command_parser *
loader_lookup_parser(uint_fast16_t cmdid)
{
    const struct loader_module_header *h = active_module;
    if (!h)
        return NULL;
    uint_fast16_t idx = cmdid - h->command_base;
    if (cmdid < h->command_base || idx >= h->command_count)
        return NULL;
    const struct command_parser *cp = &h->commands[idx];
    if (!cp->func)
        return NULL;
    return cp;
}

void
loader_task(void)
{
    const struct loader_module_header *h = active_module;
    if (h && h->run_taskfuncs)
        h->run_taskfuncs();
}
DECL_TASK(loader_task);

void
loader_shutdown(void)
{
    const struct loader_module_header *h = active_module;
    if (h && h->run_shutdownfuncs)
        h->run_shutdownfuncs();
}
DECL_SHUTDOWN(loader_shutdown);


/****************************************************************
 * Firmware functions available to loaded modules
 ****************************************************************/

DECL_LOADER_EXPORT(sched_add_timer);
DECL_LOADER_EXPORT(sched_del_timer);
DECL_LOADER_EXPORT(sched_wake_task);
DECL_LOADER_EXPORT(sched_check_wake);
DECL_LOADER_EXPORT(sched_is_shutdown);
DECL_LOADER_EXPORT(sched_try_shutdown);
DECL_LOADER_EXPORT(sched_shutdown);
DECL_LOADER_EXPORT(command_sendf);
DECL_LOADER_EXPORT(command_decode_ptr);
DECL_LOADER_EXPORT(alloc_chunk);
DECL_LOADER_EXPORT(oid_alloc);
DECL_LOADER_EXPORT(oid_lookup);
DECL_LOADER_EXPORT(oid_next);
DECL_LOADER_EXPORT(move_alloc);
DECL_LOADER_EXPORT(move_free);
DECL_LOADER_EXPORT(move_queue_setup);
DECL_LOADER_EXPORT(move_queue_empty);
DECL_LOADER_EXPORT(move_queue_first);
DECL_LOADER_EXPORT(move_queue_push);
DECL_LOADER_EXPORT(move_queue_pop);
DECL_LOADER_EXPORT(move_queue_clear);
DECL_LOADER_EXPORT(irq_disable);
DECL_LOADER_EXPORT(irq_enable);
DECL_LOADER_EXPORT(irq_save);
DECL_LOADER_EXPORT(irq_restore);
DECL_LOADER_EXPORT(irq_poll);
DECL_LOADER_EXPORT(timer_read_time);
DECL_LOADER_EXPORT(timer_from_us);
DECL_LOADER_EXPORT(timer_is_before);
DECL_LOADER_EXPORT(crc16_ccitt);

#if CONFIG_HAVE_GPIO
#include "board/gpio.h" // gpio_out_setup
#include "trsync.h" // trsync_add_signal
DECL_LOADER_EXPORT(gpio_out_setup);
DECL_LOADER_EXPORT(gpio_out_reset);
DECL_LOADER_EXPORT(gpio_out_toggle_noirq);
DECL_LOADER_EXPORT(gpio_out_toggle);
DECL_LOADER_EXPORT(gpio_out_write);
DECL_LOADER_EXPORT(gpio_in_setup);
DECL_LOADER_EXPORT(gpio_in_reset);
DECL_LOADER_EXPORT(gpio_in_read);
DECL_LOADER_EXPORT(trsync_oid_lookup);
DECL_LOADER_EXPORT(trsync_do_trigger);
DECL_LOADER_EXPORT(trsync_add_signal);
#endif

#if CONFIG_WANT_ADC
DECL_LOADER_EXPORT(gpio_adc_setup);
DECL_LOADER_EXPORT(gpio_adc_sample);
DECL_LOADER_EXPORT(gpio_adc_read);
DECL_LOADER_EXPORT(gpio_adc_cancel_sample);
#endif

#if CONFIG_WANT_HARD_PWM
DECL_LOADER_EXPORT(gpio_pwm_setup);
DECL_LOADER_EXPORT(gpio_pwm_write);
#endif

#if CONFIG_WANT_SPI
#include "spicmds.h" // spidev_transfer
DECL_LOADER_EXPORT(spi_setup);
DECL_LOADER_EXPORT(spi_prepare);
DECL_LOADER_EXPORT(spi_transfer);
DECL_LOADER_EXPORT(spidev_oid_lookup);
DECL_LOADER_EXPORT(spidev_have_cs_pin);
DECL_LOADER_EXPORT(spidev_get_cs_pin);
DECL_LOADER_EXPORT(spidev_transfer);
#endif

#if CONFIG_WANT_I2C
#include "i2ccmds.h" // i2c_dev_read
DECL_LOADER_EXPORT(i2c_setup);
DECL_LOADER_EXPORT(i2c_write);
DECL_LOADER_EXPORT(i2c_read);
DECL_LOADER_EXPORT(i2cdev_oid_lookup);
DECL_LOADER_EXPORT(i2c_dev_read);
DECL_LOADER_EXPORT(i2c_dev_write);
DECL_LOADER_EXPORT(i2c_shutdown_on_err);
#endif

#if CONFIG_NEED_SENSOR_BULK
#include "sensor_bulk.h" // sensor_bulk_report
DECL_LOADER_EXPORT(sensor_bulk_reset);
DECL_LOADER_EXPORT(sensor_bulk_report);
DECL_LOADER_EXPORT(sensor_bulk_status);
#endif
