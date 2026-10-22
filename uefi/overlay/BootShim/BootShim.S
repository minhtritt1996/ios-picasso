_Head:
  adr   x1, _Payload    /* Set _Entry address */
  b     _Start          /* Jump to the real code */

#if REQUIRES_KERNEL_HEADER == 1
_StackBase:
  .quad FD_BASE         /* Text Offset */

_StackSize:
  .quad FD_SIZE         /* Image Size */
  .quad 0               /* Flags */
  .quad 0               /* Reserved */
  .quad 0               /* Reserved */
  .quad 0               /* Reserved */
  .ascii "ARM\x64"      /* ARM64 Magic */
  .long 0               /* Reserved */
#endif

_Start:
  /*
   * picasso bring-up marker: paint the top of the ABL framebuffer blue as the
   * very first thing the shim does, before it copies the FD anywhere. Seeing it
   * proves the bootloader jumped here; no UART exists on this board.
   */
  ldr   x7, =0xA0000000
  mov   w8, #0x0000FF00          /* ABGR: opaque blue */
  mov   x9, #1080
  mov   x10, #200
_MarkRows:
  mov   x11, x9
_MarkCols:
  str   w8, [x7], #4
  subs  x11, x11, #1
  b.ne  _MarkCols
  subs  x10, x10, #1
  b.ne  _MarkRows

  mov   x4, x1
  ldr   x5, =FD_BASE
  cmp   x4, x5
  beq   _Entry
  ldr   x6,  =FD_SIZE

_CopyLoop:
  ldp   x2, x3, [x4], #16
  stp   x2, x3, [x5], #16
  subs  x6, x6, #16
  b.ne  _CopyLoop
_CopyDone:
  ldr   x5, =FD_BASE

  /* Second marker: the FD copy finished, we are about to jump into SEC. */
  ldr   x7, =0xA0000000 + (1080 * 4 * 100)
  mov   w8, #0xFFFF0000          /* opaque red */
  mov   x9, #1080
  mov   x10, #100
_MarkRows2:
  mov   x11, x9
_MarkCols2:
  str   w8, [x7], #4
  subs  x11, x11, #1
  b.ne  _MarkCols2
  subs  x10, x10, #1
  b.ne  _MarkRows2

_Entry:
  br    x5

_Dead:
  b     _Dead

  /* Literal pools first: _Payload must be the last byte of the file, because
     build_uefi.py concatenates BootShim.bin + FD and the shim copies from
     _Payload. A literal emitted after _Payload shifts the FD and SEC reads
     garbage. */
  .ltorg

.text
.align 4

_Payload:
