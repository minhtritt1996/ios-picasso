#pragma once
#include <stdint.h>

/*
 * Minimal flattened-device-tree reader for the SWEET shim.
 *
 * The bootloader hands us the device tree it booted with (x0 at entry). On
 * picasso the static tree has `reg = <0 0 0 0>` for /memory because the
 * bootloader fills the RAM banks in at boot, so the only way to learn the
 * real memory layout is to read the tree we were given - not a copy in the
 * boot image.
 *
 * Everything is big-endian on the wire, like every other FDT.
 */

struct fdt {
	uintptr_t base;		/* the tree itself */
	uintptr_t struct_off;	/* start of the structure block */
	uintptr_t struct_size;
	uintptr_t strings;	/* start of the strings block */
	uintptr_t strings_size;
};

/* One node: its name, the start of its property list and its parent. */
struct fdt_node {
	const char *name;
	uintptr_t props;
	uintptr_t parent_props;
};

#define FDT_MAX_DEPTH	24

/* Flat walk over every node, in tree order. */
struct fdt_iter {
	uintptr_t cursor;
	int depth;
	const char *names[FDT_MAX_DEPTH];
	uintptr_t props[FDT_MAX_DEPTH];
};

int fdt_init(uintptr_t dtb, struct fdt *f);

/* Next node in tree order; returns 0 at the end. */
void fdt_iter_start(const struct fdt *f, struct fdt_iter *it);
int fdt_iter_next(const struct fdt *f, struct fdt_iter *it, struct fdt_node *out);

/* Find a node by absolute path ("/reserved-memory/cont_splash_region"). */
int fdt_find_path(const struct fdt *f, const char *path, struct fdt_node *out);

/* Find a node whose "compatible" contains `compat`. */
int fdt_find_compatible(const struct fdt *f, const char *compat, struct fdt_node *out);

/* Property lookup inside one node. Returns 1 and the value pointer if found. */
int fdt_prop(const struct fdt *f, uintptr_t props, const char *name, uintptr_t *val, uint32_t *len);
int fdt_prop_u32(const struct fdt *f, uintptr_t props, const char *name, uint32_t *out);
int fdt_prop_u64(const struct fdt *f, uintptr_t props, const char *name, uint64_t *out);
const char *fdt_prop_str(const struct fdt *f, uintptr_t props, const char *name);

/* "reg" decoding: the parent's #address-cells / #size-cells apply. */
int fdt_reg_cells(const struct fdt *f, uintptr_t props, uint32_t *ac, uint32_t *sc);
int fdt_reg_cells_of(const struct fdt *f, const struct fdt_node *node, uint32_t *ac, uint32_t *sc);
int fdt_reg(const struct fdt *f, uintptr_t props, uint32_t ac, uint32_t sc,
    unsigned index, uint64_t *base, uint64_t *size);

/* Read a u32/u64 (cell-size agnostic) property of /chosen. */
int fdt_chosen_u64(uintptr_t fdt, const char *prop, uint64_t *out);
