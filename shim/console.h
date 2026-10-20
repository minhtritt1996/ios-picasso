#pragma once
#include <stdint.h>

/* The splash framebuffer of the board. These are the defaults; the shim
 * replaces them at boot with what the device tree says (shim/hw.c), and the
 * build can override them for a board whose tree cannot be read. */
#ifndef FB_BASE
#define FB_BASE		0x9c000000UL	/* reserved-memory cont_splash_region */
#endif
#ifndef FB_W
#define FB_W		1080
#endif
#ifndef FB_H
#define FB_H		2400
#endif
#ifndef FB_STRIDE
#define FB_STRIDE	(FB_W * 4)	/* a8r8g8b8, matches mainline simplefb */
#endif

#define RED	0xffff0000U
#define BLUE	0xff0000a0U
#define WHITE	0xffffffffU
#define YELLOW	0xffffff00U
#define GREEN	0xff00ff00U

extern volatile uint32_t *con_fb;

/* Point the console at the real splash framebuffer (the board's geometry is
 * discovered at boot; the values above are the compiled-in defaults). */
void con_setup(uintptr_t base, uint32_t w, uint32_t h, uint32_t stride);

void con_clear(uint32_t bg);
void con_skip(void);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
void con_dec(const char *label, uint64_t v);
/* label, then "a=xxxxxxxx b=xxxxxxxx" style pairs of 32-bit values */
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);
