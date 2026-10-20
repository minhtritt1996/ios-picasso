#pragma once
#include <stdint.h>

/*
 * Apple's device tree (ADT) is not a Linux flattened device tree: it is a
 * small header with property and child counts, then fixed-size property
 * records, then the children. Values are little endian.
 *
 *   node  := u32 nprops, u32 nchildren, props[], children[]
 *   prop  := char name[32], u32 (len | flags << 31), value[len] (padded to 4)
 *
 * The shim uses this to fill the board values it discovered from the live
 * device tree into the ADT it hands to xnu, so the kernel does not need
 * hard-coded addresses.
 */

/* Set a property of the node at `path` ("chosen", "arm-io/gic", "cpus/cpu0"). */
int adt_set_u32(uintptr_t adt, const char *path, const char *prop, uint32_t val);
int adt_set_u64(uintptr_t adt, const char *path, const char *prop, uint64_t val);
int adt_set_u64s(uintptr_t adt, const char *path, const char *prop, const uint64_t *vals, int n);

/* Read a property back (for verification). */
int adt_get_u32(uintptr_t adt, const char *path, const char *prop, uint32_t *out);
int adt_get_u64(uintptr_t adt, const char *path, const char *prop, uint64_t *out);
int adt_get_u64s(uintptr_t adt, const char *path, const char *prop, uint64_t *vals, int max);
