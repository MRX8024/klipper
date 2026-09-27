#ifndef __LOADER_H
#define __LOADER_H

#include <stdint.h> // uint16_t
#include "ctr.h" // DECL_CTR

#define LOADER_MAGIC 0x444f4d4b // "KMOD"
#define LOADER_ABI_VERSION 1

// Header placed at the start of every module image
struct loader_module_header {
    uint32_t magic, abi_version;
    void *base, *image_end, *bss_start, *bss_end;
    void (*run_initfuncs)(void);
    void (*run_taskfuncs)(void);
    void (*run_shutdownfuncs)(void);
    const struct command_parser *commands;
    uint16_t command_base, command_count;
};

// Make a firmware function available to run-time loaded modules
#define DECL_LOADER_EXPORT(SYM)                                 \
    DECL_CTR("DECL_LOADER_EXPORT " __stringify(SYM));           \
    const void *const __PASTE(_loader_export_, SYM) = &(SYM)

// loader.c
const struct command_parser *loader_lookup_parser(uint_fast16_t cmdid);

// out/compile_time_request.c (auto generated file)
extern const void *const *const loader_export_table[];
extern const uint16_t loader_export_count;

#endif // loader.h
