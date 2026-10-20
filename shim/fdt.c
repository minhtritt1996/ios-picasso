/*
 * A small flattened-device-tree reader for the SWEET shim.
 *
 * The bootloader hands us the tree it booted with (x0 at entry). On picasso
 * the static tree has `reg = <0 0 0 0>` for /memory because the bootloader
 * fills the RAM banks in at boot, so the only way to learn the real memory
 * layout is to read the tree we were given - not the copy in the boot image.
 *
 * The wire format is big-endian and 4-byte aligned; see the FDT spec.
 */
#include "fdt.h"
#include <stddef.h>

#define FDT_MAGIC	0xd00dfeed
#define FDT_BEGIN_NODE	1
#define FDT_END_NODE	2
#define FDT_PROP	3
#define FDT_NOP		4
#define FDT_END		9

static uint32_t be32(uintptr_t a)
{
	return __builtin_bswap32(*(volatile uint32_t *)a);
}

static int streq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

static uintptr_t align4(uintptr_t a)
{
	return (a + 3) & ~3UL;
}

static uintptr_t skip_str(uintptr_t p)
{
	/* byte loads are fine on Device memory as long as they are aligned to 1 */
	while (*(volatile char *)p)
		p++;
	return p + 1;
}

int fdt_magic_ok(uintptr_t dtb)
{
	return dtb && be32(dtb) == FDT_MAGIC;
}

int fdt_init(uintptr_t dtb, struct fdt *f)
{
	if (!dtb || be32(dtb) != FDT_MAGIC)
		return 0;
	f->base = dtb;
	f->struct_off = dtb + be32(dtb + 8);		/* off_dt_struct */
	f->strings = dtb + be32(dtb + 12);		/* off_dt_strings */
	f->strings_size = be32(dtb + 32);
	f->struct_size = be32(dtb + 36);
	if (f->struct_size == 0 || f->strings_size == 0)
		return 0;
	return 1;
}

void fdt_iter_start(const struct fdt *f, struct fdt_iter *it)
{
	it->cursor = f->struct_off;
	it->depth = 0;
}

int fdt_iter_next(const struct fdt *f, struct fdt_iter *it, struct fdt_node *out)
{
	uintptr_t p = it->cursor;
	uintptr_t end = f->struct_off + f->struct_size;

	while (p < end) {
		uint32_t tok = be32(p);
		switch (tok) {
		case FDT_BEGIN_NODE: {
			const char *name = (const char *)(p + 4);
			uintptr_t props = align4(skip_str(p + 4));
			if (it->depth < FDT_MAX_DEPTH) {
				it->names[it->depth] = name;
				it->props[it->depth] = props;
			}
			out->name = name;
			out->props = props;
			out->parent_props = it->depth > 0 ? it->props[it->depth - 1] : 0;
			it->depth++;
			it->cursor = props;
			return 1;
		}
		case FDT_END_NODE:
			it->depth--;
			p += 4;
			it->cursor = p;
			if (it->depth < 0)
				return 0;
			continue;
		case FDT_PROP: {
			/* token(4) len(4) nameoff(4) data[len] */
			uint32_t len = be32(p + 4);
			p = align4(p + 12 + len);
			it->cursor = p;
			continue;
		}
		case FDT_NOP:
			p += 4;
			it->cursor = p;
			continue;
		default:	/* FDT_END or garbage */
			return 0;
		}
	}
	return 0;
}

/* Build "/a/b/c" from the iterator's name stack and compare with path. */
static int path_matches(const struct fdt_iter *it, const char *path)
{
	const char *p = path;
	int d = 0;

	while (*p == '/')
		p++;
	for (d = 0; d < it->depth; d++) {
		const char *n = it->names[d];
		while (*n) {
			if (*p != *n)
				return 0;
			p++, n++;
		}
		if (d + 1 < it->depth) {
			if (*p != '/')
				return 0;
			while (*p == '/')
				p++;
		}
	}
	while (*p == '/')
		p++;
	return *p == 0;
}

int fdt_find_path(const struct fdt *f, const char *path, struct fdt_node *out)
{
	struct fdt_iter it;

	fdt_iter_start(f, &it);
	while (fdt_iter_next(f, &it, out)) {
		if (path_matches(&it, path))
			return 1;
	}
	return 0;
}

int fdt_find_compatible(const struct fdt *f, const char *compat, struct fdt_node *out)
{
	struct fdt_iter it;

	fdt_iter_start(f, &it);
	while (fdt_iter_next(f, &it, out)) {
		uintptr_t val;
		uint32_t len;
		if (!fdt_prop(f, out->props, "compatible", &val, &len))
			continue;
		/* a list of NUL-separated strings */
		uintptr_t p = val, end = val + len;
		while (p < end) {
			const char *s = (const char *)p;
			if (streq(s, compat)) {
				return 1;
			}
			p = skip_str(p);
		}
	}
	return 0;
}

int fdt_prop(const struct fdt *f, uintptr_t props, const char *name, uintptr_t *val, uint32_t *len)
{
	uintptr_t p = props;

	for (;;) {
		uint32_t tok = be32(p);
		if (tok == FDT_PROP) {
			uint32_t plen = be32(p + 4);
			uint32_t nameoff = be32(p + 8);
			if (streq((const char *)(f->strings + nameoff), name)) {
				*val = p + 12;
				*len = plen;
				return 1;
			}
			p = align4(p + 12 + plen);
			continue;
		}
		if (tok == FDT_NOP) {
			p += 4;
			continue;
		}
		return 0;	/* BEGIN_NODE, END_NODE or END */
	}
}

int fdt_prop_u32(const struct fdt *f, uintptr_t props, const char *name, uint32_t *out)
{
	uintptr_t val;
	uint32_t len;

	if (!fdt_prop(f, props, name, &val, &len) || len != 4)
		return 0;
	*out = be32(val);
	return 1;
}

int fdt_prop_u64(const struct fdt *f, uintptr_t props, const char *name, uint64_t *out)
{
	uintptr_t val;
	uint32_t len;

	if (!fdt_prop(f, props, name, &val, &len) || len != 8)
		return 0;
	*out = (uint64_t)be32(val) << 32 | be32(val + 4);
	return 1;
}

const char *fdt_prop_str(const struct fdt *f, uintptr_t props, const char *name)
{
	uintptr_t val;
	uint32_t len;

	if (!fdt_prop(f, props, name, &val, &len))
		return NULL;
	return (const char *)val;
}

int fdt_reg_cells(const struct fdt *f, uintptr_t props, uint32_t *ac, uint32_t *sc)
{
	(void)f;
	(void)props;
	/*
	 * The cells come from the parent, but a bare property list has no link
	 * to it; callers that need the parent's values pass them in explicitly
	 * (see fdt_reg_parent). Defaults are the root's: 2 address, 1 size.
	 */
	*ac = 2;
	*sc = 1;
	return 1;
}

/* The parent's #address-cells / #size-cells, as the FDT spec requires. */
int fdt_reg_cells_of(const struct fdt *f, const struct fdt_node *node, uint32_t *ac, uint32_t *sc)
{
	*ac = 2;
	*sc = 1;
	if (node == NULL || node->parent_props == 0)
		return 1;
	uint32_t a, s;
	if (fdt_prop_u32(f, node->parent_props, "#address-cells", &a))
		*ac = a;
	if (fdt_prop_u32(f, node->parent_props, "#size-cells", &s))
		*sc = s;
	return 1;
}

int fdt_reg(const struct fdt *f, uintptr_t props, uint32_t ac, uint32_t sc,
    unsigned index, uint64_t *base, uint64_t *size)
{
	uintptr_t val;
	uint32_t len, n, i;

	if (!fdt_prop(f, props, "reg", &val, &len))
		return 0;
	n = len / 4;
	if (ac + sc == 0 || n % (ac + sc))
		return 0;
	if ((index + 1) * (ac + sc) > n)
		return 0;
	uint64_t b = 0, s = 0;
	for (i = 0; i < ac; i++)
		b = (b << 32) | be32(val + (index * (ac + sc) + i) * 4);
	for (i = 0; i < sc; i++)
		s = (s << 32) | be32(val + (index * (ac + sc) + ac + i) * 4);
	*base = b;
	*size = s;
	return 1;
}

int fdt_chosen_u64(uintptr_t fdt, const char *prop, uint64_t *out)
{
	struct fdt f;
	struct fdt_node node;
	uintptr_t val;
	uint32_t len;

	if (!fdt_init(fdt, &f))
		return 0;
	if (!fdt_find_path(&f, "/chosen", &node))
		return 0;
	if (!fdt_prop(&f, node.props, prop, &val, &len))
		return 0;
	if (len == 4) {
		*out = be32(val);
		return 1;
	}
	if (len == 8) {
		*out = (uint64_t)be32(val) << 32 | be32(val + 4);
		return 1;
	}
	return 0;
}
