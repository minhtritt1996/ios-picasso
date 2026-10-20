/*
 * Read/write the Apple device tree (ADT) that the shim hands to xnu.
 *
 * The ADT is Apple's own layout, not the Linux flattened device tree:
 * counts, then property records, then children. Values are little endian.
 */
#include "adt.h"
#include <stddef.h>

#define ADT_NAME_LEN	32

static uint32_t le32(uintptr_t a)
{
	return *(volatile uint32_t *)a;
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

/* The address just past this node's property list. */
static uintptr_t adt_props_end(uintptr_t node)
{
	uint32_t nprops = le32(node);
	uintptr_t p = node + 8;

	for (uint32_t i = 0; i < nprops; i++) {
		uint32_t len = le32(p + ADT_NAME_LEN) & 0x7fffffff;
		p = align4(p + ADT_NAME_LEN + 4 + len);
	}
	return p;
}

/* The address just past this node and its whole subtree. */
static uintptr_t adt_skip(uintptr_t node)
{
	uint32_t nprops = le32(node);
	uint32_t nchildren = le32(node + 4);
	uintptr_t p = node + 8;

	for (uint32_t i = 0; i < nprops; i++) {
		uint32_t len = le32(p + ADT_NAME_LEN) & 0x7fffffff;
		p = align4(p + ADT_NAME_LEN + 4 + len);
	}
	for (uint32_t k = 0; k < nchildren; k++)
		p = adt_skip(p);
	return p;
}

/* The node's name: the value of its "name" property. */
static const char *adt_node_name(uintptr_t node)
{
	uint32_t nprops = le32(node);
	uintptr_t p = node + 8;

	for (uint32_t i = 0; i < nprops; i++) {
		uint32_t len = le32(p + ADT_NAME_LEN) & 0x7fffffff;
		if (streq((const char *)p, "name"))
			return (const char *)(p + ADT_NAME_LEN + 4);
		p = align4(p + ADT_NAME_LEN + 4 + len);
	}
	return NULL;
}

/* Walk the children of a node, skipping n of them. */
static uintptr_t adt_child(uintptr_t node, uint32_t index)
{
	uint32_t nchildren = le32(node + 4);
	uintptr_t p = adt_props_end(node);

	for (uint32_t i = 0; i < nchildren; i++) {
		uintptr_t child = p;
		p = adt_skip(child);
		if (i == index)
			return child;
	}
	return 0;
}

/* Find a node by a slash path below the root ("arm-io/gic", "cpus/cpu0"). */
static uintptr_t adt_find(uintptr_t adt, const char *path)
{
	uintptr_t node = adt;
	const char *p = path;

	while (*p) {
		while (*p == '/')
			p++;
		if (!*p)
			break;
		char comp[64];
		unsigned i = 0;
		while (*p && *p != '/' && i + 1 < sizeof(comp))
			comp[i++] = *p++;
		comp[i] = 0;
		uint32_t nchildren = le32(node + 4);
		uintptr_t found = 0;
		for (uint32_t k = 0; k < nchildren; k++) {
			uintptr_t child = adt_child(node, k);
			if (!child)
				break;
			const char *nm = adt_node_name(child);
			if (nm && streq(nm, comp)) {
				found = child;
				break;
			}
		}
		if (!found)
			return 0;
		node = found;
	}
	return node;
}

/* Find a property of a node; returns the value address and its length. */
static uintptr_t adt_prop(uintptr_t node, const char *name, uint32_t *len)
{
	uint32_t nprops = le32(node);
	uintptr_t p = node + 8;

	for (uint32_t i = 0; i < nprops; i++) {
		uint32_t l = le32(p + ADT_NAME_LEN) & 0x7fffffff;
		if (streq((const char *)p, name)) {
			*len = l;
			return p + ADT_NAME_LEN + 4;
		}
		p = align4(p + ADT_NAME_LEN + 4 + l);
	}
	return 0;
}

static int adt_set_raw(uintptr_t adt, const char *path, const char *prop, const void *data, uint32_t len)
{
	uintptr_t node = adt_find(adt, path);
	uint32_t plen;

	if (!node)
		return 0;
	uintptr_t val = adt_prop(node, prop, &plen);
	if (!val || plen != len)
		return 0;
	for (uint32_t i = 0; i < len; i++)
		*(volatile uint8_t *)(val + i) = ((const uint8_t *)data)[i];
	return 1;
}

int adt_set_u32(uintptr_t adt, const char *path, const char *prop, uint32_t val)
{
	return adt_set_raw(adt, path, prop, &val, 4);
}

int adt_set_u64(uintptr_t adt, const char *path, const char *prop, uint64_t val)
{
	return adt_set_raw(adt, path, prop, &val, 8);
}

int adt_set_u64s(uintptr_t adt, const char *path, const char *prop, const uint64_t *vals, int n)
{
	uint8_t buf[64];
	int i;

	if (n < 1 || n > 8)
		return 0;
	for (i = 0; i < n; i++) {
		buf[i * 8 + 0] = (uint8_t)(vals[i]);
		buf[i * 8 + 1] = (uint8_t)(vals[i] >> 8);
		buf[i * 8 + 2] = (uint8_t)(vals[i] >> 16);
		buf[i * 8 + 3] = (uint8_t)(vals[i] >> 24);
		buf[i * 8 + 4] = (uint8_t)(vals[i] >> 32);
		buf[i * 8 + 5] = (uint8_t)(vals[i] >> 40);
		buf[i * 8 + 6] = (uint8_t)(vals[i] >> 48);
		buf[i * 8 + 7] = (uint8_t)(vals[i] >> 56);
	}
	return adt_set_raw(adt, path, prop, buf, (uint32_t)n * 8);
}

int adt_get_u32(uintptr_t adt, const char *path, const char *prop, uint32_t *out)
{
	uintptr_t node = adt_find(adt, path);
	uint32_t plen;

	if (!node)
		return 0;
	uintptr_t val = adt_prop(node, prop, &plen);
	if (!val || plen != 4)
		return 0;
	*out = le32(val);
	return 1;
}

int adt_get_u64(uintptr_t adt, const char *path, const char *prop, uint64_t *out)
{
	uintptr_t node = adt_find(adt, path);
	uint32_t plen;

	if (!node)
		return 0;
	uintptr_t val = adt_prop(node, prop, &plen);
	if (!val || plen != 8)
		return 0;
	*out = (uint64_t)le32(val) | ((uint64_t)le32(val + 4) << 32);
	return 1;
}

int adt_get_u64s(uintptr_t adt, const char *path, const char *prop, uint64_t *vals, int max)
{
	uintptr_t node = adt_find(adt, path);
	uint32_t plen;

	if (!node)
		return 0;
	uintptr_t val = adt_prop(node, prop, &plen);
	if (!val || plen % 8 || plen / 8 > (uint32_t)max)
		return 0;
	for (uint32_t i = 0; i < plen / 8; i++)
		vals[i] = (uint64_t)le32(val + i * 8) | ((uint64_t)le32(val + i * 8 + 4) << 32);
	return (int)(plen / 8);
}
