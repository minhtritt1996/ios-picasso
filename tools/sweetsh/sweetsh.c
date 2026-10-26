/*
 * sweetsh - a freestanding arm64 Mach-O shell for the picasso XNU port.
 *
 * The iOS rootfs has no usable shell (/bin/sh and /bin/bash are dangling
 * jailbreak links to /var/jb, which does not exist), and there is no sshd, so
 * this binary is the shell: launchd starts it with the accepted TCP socket as
 * fd 0/1/2 (inetdCompatibility), and it forks/execs the binaries the rootfs
 * does have (/bin/dd, /bin/df, /bin/ps, /usr/libexec/*, ...).
 *
 * It is linked -static with no libc: every call below is a raw BSD syscall
 * (numbers from bsd/kern/syscalls.master) issued with "svc #0x80" and the
 * number in x16, the Darwin arm64 convention.
 */

typedef unsigned long u64;
typedef long i64;
typedef unsigned int u32;

#define SYS_exit     1
#define SYS_fork     2
#define SYS_read     3
#define SYS_write    4
#define SYS_open     5
#define SYS_close    6
#define SYS_wait4    7
#define SYS_chdir    12
#define SYS_getpid   20
#define SYS_execve   59
#define SYS_dup2     90
#define SYS_select   93
#define SYS_recvfrom 29
#define SYS_sendto   133
#define SYS_connect  98
#define SYS_socket   97
#define SYS_bind     104
#define SYS_listen   106
#define SYS_accept   30
#define SYS_setsockopt 105

#define SYS_unlink   10
#define SYS_reboot   55
#define SYS_mkdir    136
#define SYS_getdirentries64 344
#define SYS_lseek    199

#define AF_INET      2
#define SOCK_STREAM  1
#define SOCK_DGRAM   2
#define O_WRONLY     1
#define O_DIRECTORY  0x00100000
#define SOL_SOCKET   0xffff
#define SO_REUSEADDR 0x0004

#define O_RDONLY 0
#define O_CREAT  0x0200
#define O_TRUNC  0x0400

static i64
sys3(i64 n, i64 a, i64 b, i64 c)
{
	register i64 x16 __asm__("x16") = n;
	register i64 x0 __asm__("x0") = a;
	register i64 x1 __asm__("x1") = b;
	register i64 x2 __asm__("x2") = c;

	__asm__ volatile("svc #0x80" : "+r"(x0) : "r"(x16), "r"(x1), "r"(x2) : "memory");
	return x0;
}

static i64
sys5(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e)
{
	register i64 x16 __asm__("x16") = n;
	register i64 x0 __asm__("x0") = a;
	register i64 x1 __asm__("x1") = b;
	register i64 x2 __asm__("x2") = c;
	register i64 x3 __asm__("x3") = d;
	register i64 x4 __asm__("x4") = e;

	__asm__ volatile("svc #0x80"
	    : "+r"(x0)
	    : "r"(x16), "r"(x1), "r"(x2), "r"(x3), "r"(x4)
	    : "memory");
	return x0;
}

static i64
sys6(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f)
{
	register i64 x16 __asm__("x16") = n;
	register i64 x0 __asm__("x0") = a;
	register i64 x1 __asm__("x1") = b;
	register i64 x2 __asm__("x2") = c;
	register i64 x3 __asm__("x3") = d;
	register i64 x4 __asm__("x4") = e;
	register i64 x5 __asm__("x5") = f;

	__asm__ volatile("svc #0x80"
	    : "+r"(x0)
	    : "r"(x16), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
	    : "memory");
	return x0;
}

static i64
sys4(i64 n, i64 a, i64 b, i64 c, i64 d)
{
	register i64 x16 __asm__("x16") = n;
	register i64 x0 __asm__("x0") = a;
	register i64 x1 __asm__("x1") = b;
	register i64 x2 __asm__("x2") = c;
	register i64 x3 __asm__("x3") = d;

	__asm__ volatile("svc #0x80"
	    : "+r"(x0)
	    : "r"(x16), "r"(x1), "r"(x2), "r"(x3)
	    : "memory");
	return x0;
}

static u64
strlen_(const char *s)
{
	u64 n = 0;
	while (s[n]) {
		n++;
	}
	return n;
}

static void
puts_(const char *s)
{
#ifdef SWEETSH_UDP_ONLY
	extern void udp_emit(const char *s, int n);
	udp_emit(s, (int)strlen_(s));
#else
	sys3(SYS_write, 1, (i64)s, (i64)strlen_(s));
#endif
}

static void
putb_(const char *b, int n)
{
#ifdef SWEETSH_UDP_ONLY
	extern void udp_emit(const char *s, int n);
	udp_emit(b, n);
#else
	sys3(SYS_write, 1, (i64)b, (i64)n);
#endif
}

static void
putn_(i64 v)
{
	char buf[24];
	int i = 23;

	buf[i--] = 0;
	if (v == 0) {
		buf[i--] = '0';
	}
	while (v > 0 && i >= 0) {
		buf[i--] = '0' + (v % 10);
		v /= 10;
	}
	puts_(&buf[i + 1]);
}

static int
streq(const char *a, const char *b)
{
	while (*a && *a == *b) {
		a++;
		b++;
	}
	return *a == *b;
}

static int
startswith(const char *a, const char *b)
{
	while (*b) {
		if (*a++ != *b++) {
			return 0;
		}
	}
	return 1;
}

/* Split a line into argv in place. */
static int
split(char *line, char **argv, int max)
{
	int argc = 0;

	while (*line) {
		while (*line == ' ' || *line == '\t') {
			line++;
		}
		if (*line == 0) {
			break;
		}
		if (argc == max - 1) {
			break;
		}
		argv[argc++] = line;
		while (*line && *line != ' ' && *line != '\t') {
			line++;
		}
		if (*line) {
			*line++ = 0;
		}
	}
	argv[argc] = 0;
	return argc;
}

/* Read one line; returns its length, 0 on EOF. */
static int
readline(char *buf, int max)
{
	int n = 0;

	for (;;) {
		char c;
		i64 r = sys3(SYS_read, 0, (i64)&c, 1);

		if (r <= 0) {
			return -1;              /* EOF/error: never exit on this */
		}
		if (c == '\r') {
			continue;
		}
		if (c == '\n') {
			puts_("\r\n");
			buf[n] = 0;
			return n;
		}
		if ((c == 8 || c == 127) && n > 0) {     /* backspace */
			n--;
			puts_("\b \b");
			continue;
		}
		if (n < max - 1 && c >= 32) {
			buf[n++] = c;
			sys3(SYS_write, 1, (i64)&c, 1);
		}
	}
}

/* Write a line to /dev/console when it exists: that is how this binary shows up
 * in the kernel console ring, which the host reads through the USB driver. */
static void
console(const char *s)
{
#ifdef SWEETSH_QUIET
	(void)s;                        /* quiet build: writing to /dev/console and
	                                 * sending datagrams both go through kernel
	                                 * paths that have wedged the USB driver */
	return;
#else
	i64 fd = sys3(SYS_open, (i64)"/dev/console", O_WRONLY, 0);

	if (fd >= 0) {
		sys3(SYS_write, fd, (i64)s, (i64)strlen_(s));
		sys3(SYS_close, fd, 0, 0);
	}
#endif
}

struct sockaddr_in {
	unsigned char sin_len;
	unsigned char sin_family;
	unsigned short sin_port;
	unsigned int sin_addr;
	char pad[8];
};

/* select(0, NULL, NULL, NULL, &tv) is the portable way to sleep without libc. */
static void
sleep_ms(long ms)
{
	struct timeval { long sec; long usec; } tv = { ms / 1000, (ms % 1000) * 1000 };
	register long x16 __asm__("x16") = SYS_select;
	register long x0 __asm__("x0") = 0;
	register long x1 __asm__("x1") = 0;
	register long x2 __asm__("x2") = 0;
	register long x3 __asm__("x3") = 0;
	register long x4 __asm__("x4") = (long)&tv;

	__asm__ volatile("svc #0x80"
	    : "+r"(x0)
	    : "r"(x16), "r"(x1), "r"(x2), "r"(x3), "r"(x4)
	    : "memory");
}

static void
beacon(const char *msg)
{
	struct sockaddr_in sa;
#ifdef SWEETSH_QUIET
	(void)msg;
	(void)sa;
	return;
#endif
	i64 fd = sys3(SYS_socket, AF_INET, SOCK_DGRAM, 0);

	if (fd < 0) {
		return;
	}
	for (int i = 0; i < (int)sizeof(sa); i++) {
		((char *)&sa)[i] = 0;
	}
	sa.sin_len = sizeof(sa);
	sa.sin_family = AF_INET;
	sa.sin_port = (unsigned short)((5140 >> 8) | ((5140 & 0xff) << 8));
	sa.sin_addr = ((192u << 24) | (168u << 16) | (7u << 8) | 1u);   /* 192.168.7.1 */
	if (sys3(SYS_connect, fd, (i64)&sa, sizeof(sa)) == 0) {
		sys3(SYS_write, fd, (i64)msg, (i64)strlen_(msg));
	}
	sys3(SYS_close, fd, 0, 0);
}

static void
beacon_num(const char *tag, i64 v)
{
	char buf[64];
	int i = 0;

	while (*tag && i < 40) {
		buf[i++] = *tag++;
	}
	buf[i++] = ' ';
	if (v < 0) {
		buf[i++] = '-';
		v = -v;
	}
	char digits[16];
	int d = 0;
	if (v == 0) {
		digits[d++] = '0';
	}
	while (v > 0 && d < 15) {
		digits[d++] = '0' + (int)(v % 10);
		v /= 10;
	}
	while (d > 0 && i < 60) {
		buf[i++] = digits[--d];
	}
	buf[i++] = 0;
	beacon(buf);
}

static int
listen_4444(void)
{
	struct sockaddr_in sa;
	int fd = (int)sys3(SYS_socket, AF_INET, SOCK_STREAM, 0);

	if (fd < 0) {
		console("sweetsh: socket failed\r\n");
		return -1;
	}
	/* socket/connect/write are the only 3-argument calls this binary makes.
	 * setsockopt(2) takes five arguments and was being issued through the
	 * four-argument wrapper, so the kernel copied a bogus length from x4 and
	 * the call wedged the box; SO_REUSEADDR is not needed here anyway. */
	for (int i = 0; i < (int)sizeof(sa); i++) {
		((char *)&sa)[i] = 0;
	}
	sa.sin_len = sizeof(sa);
	sa.sin_family = AF_INET;
	sa.sin_port = (unsigned short)((4444 >> 8) | ((4444 & 0xff) << 8));   /* htons */
	sa.sin_addr = 0;                                                      /* INADDR_ANY */
	i64 berr = sys3(SYS_bind, fd, (i64)&sa, sizeof(sa));
	if (berr != 0) {
		beacon_num("sweetsh: bind err", berr);
		console("sweetsh: bind 4444 failed (in use?)\r\n");
		sys3(SYS_close, fd, 0, 0);
		return -1;
	}
	beacon("sweetsh: bound 4444\n");
	if (sys3(SYS_listen, fd, 4, 0) != 0) {
		console("sweetsh: listen failed\r\n");
		return -1;
	}
	return fd;
}

static void
banner(void)
{
	puts_("\r\nsweetsh - picasso XNU port, freestanding arm64 static binary\r\n"
	    "builtins: ls [dir], cat <file>, w <file> <off> <hex>, reboot, echo,\r\n"
	    "          cd <dir>, rm, mkdir, help, exit\r\n"
	    "anything else is exec'd as /bin, /sbin, /usr/bin, /usr/libexec or a path.\r\n\r\n");
}

/* getdirentries64 returns __DARWIN_STRUCT_DIRENTRY (bsd/sys/dirent.h):
 * d_ino(8) d_seekoff(8) d_reclen(2) d_namlen(2) d_type(1) then the name, so
 * the header is 21 bytes - the 32-bit-inode layout (reclen at 8) prints one
 * nameless "? " line and stops. */
static void
ls_(const char *path)
{
	char buf[1024];
	i64 fd = sys3(SYS_open, (i64)(path ? path : "."), O_RDONLY | O_DIRECTORY, 0);
	i64 pos = 0;

	if (fd < 0) {
		puts_("ls: cannot open ");
		puts_(path ? path : ".");
		puts_("\r\n");
		return;
	}
	for (;;) {
		i64 n = sys4(SYS_getdirentries64, fd, (i64)buf, sizeof(buf), (i64)&pos);

		if (n <= 0) {
			break;
		}
		for (i64 off = 0; off < n;) {
			unsigned short reclen = *(unsigned short *)(buf + off + 16);
			unsigned char type = (unsigned char)buf[off + 20];
			const char *name = buf + off + 21;
			/* The kernel leaves the 16-bit length field zero for this ABI, so
			 * measure the name against what is left of the record instead. */
			unsigned namlen = 0;
			while (namlen < 255 && (unsigned)(name + namlen - buf) < (unsigned)reclen &&
			    name[namlen] != 0) {
				namlen++;
			}

			if (reclen == 0) {
				break;
			}
			if (!(namlen == 1 && name[0] == '.') &&
			    !(namlen == 2 && name[0] == '.' && name[1] == '.')) {
				puts_(type == 4 ? "d " : (type == 8 ? "f " : "? "));
				putb_(name, (int)namlen);
				puts_("\r\n");
			}
			off += reclen;
		}
	}
	sys3(SYS_close, fd, 0, 0);
}

static void
cat_(const char *path)
{
	char buf[1024];
	i64 fd = sys3(SYS_open, (i64)path, O_RDONLY, 0);

	if (fd < 0) {
		puts_("cat: cannot open ");
		puts_(path);
		puts_("\r\n");
		return;
	}
	for (;;) {
		i64 n = sys3(SYS_read, fd, (i64)buf, sizeof(buf));

		if (n <= 0) {
			break;
		}
		sys3(SYS_write, 1, (i64)buf, n);
	}
	sys3(SYS_close, fd, 0, 0);
}

/*
 * Byte-level file patching from inside the booted kernel: `w <path> <offset>
 * <hex>` replaces bytes at an absolute offset of an existing file, which is what
 * `dd conv=notrunc` did from recovery. The file keeps its size and the HFS+
 * catalog is untouched, so this is a safe, idempotent way to swap a binary and
 * reboot - no recovery round trip, no adb.
 */
static u64
hexval(char c)
{
	if (c >= '0' && c <= '9') {
		return (u64)(c - '0');
	}
	if (c >= 'a' && c <= 'f') {
		return (u64)(c - 'a' + 10);
	}
	if (c >= 'A' && c <= 'F') {
		return (u64)(c - 'A' + 10);
	}
	return ~(u64)0;
}

static u64
parse_hex(const char *s)
{
	u64 v = 0;

	if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
		s += 2;
	}
	while (*s) {
		u64 d = hexval(*s++);

		if (d == ~(u64)0) {
			return ~(u64)0;
		}
		v = (v << 4) | d;
	}
	return v;
}

static void
write_(const char *path, const char *soff, const char *hex)
{
	static unsigned char buf[560];
	u64 off = parse_hex(soff);
	unsigned n = 0;

	if (off == ~(u64)0) {
		puts_("w: bad offset\r\n");
		return;
	}
	while (hex[0] && hex[1] && n < sizeof(buf)) {
		u64 hi = hexval(hex[0]), lo = hexval(hex[1]);

		if (hi == ~(u64)0 || lo == ~(u64)0) {
			break;
		}
		buf[n++] = (unsigned char)((hi << 4) | lo);
		hex += 2;
	}
	i64 fd = sys3(SYS_open, (i64)path, O_WRONLY, 0);

	if (fd < 0) {
		puts_("w: cannot open ");
		puts_(path);
		puts_("\r\n");
		return;
	}
	if (sys3(SYS_lseek, fd, (i64)off, 0) < 0) {
		puts_("w: lseek failed\r\n");
		sys3(SYS_close, fd, 0, 0);
		return;
	}
	i64 wrote = sys3(SYS_write, fd, (i64)buf, n);

	sys3(SYS_close, fd, 0, 0);
	if (wrote != (i64)n) {
		puts_("w: short write\r\n");
		return;
	}
	puts_("ok\r\n");
}

/*
 * cp <src> <dst>: create dst and copy src into it.
 *
 * Writing a *new* file is what installing a userland needs, and the VFS can do
 * it from inside the kernel - the HFS+ catalog work that recovery-based
 * installation needs is only for editing the image offline.
 */
static void
cp_(const char *src, const char *dst)
{
	static char buf[8192];
	i64 in = sys3(SYS_open, (i64)src, O_RDONLY, 0);

	if (in < 0) {
		puts_("cp: cannot open ");
		puts_(src);
		puts_("\r\n");
		return;
	}
	i64 out = sys3(SYS_open, (i64)dst, O_WRONLY | O_CREAT | O_TRUNC, 0755);

	if (out < 0) {
		puts_("cp: cannot create ");
		puts_(dst);
		puts_("\r\n");
		sys3(SYS_close, in, 0, 0);
		return;
	}
	for (;;) {
		i64 n = sys3(SYS_read, in, (i64)buf, sizeof(buf));

		if (n <= 0) {
			break;
		}
		if (sys3(SYS_write, out, (i64)buf, n) != n) {
			puts_("cp: short write\r\n");
			break;
		}
	}
	sys3(SYS_close, in, 0, 0);
	sys3(SYS_close, out, 0, 0);
	puts_("ok\r\n");
}

/* mkdir -p: create every missing component of PATH. */
static void
mkdir_p(char *path)
{
	for (char *p = path + 1; ; p++) {
		if (*p == '/' || *p == 0) {
			char saved = *p;

			*p = 0;
			if (path[0]) {
				sys3(SYS_mkdir, (i64)path, 0755, 0);
			}
			*p = saved;
			if (saved == 0) {
				break;
			}
		}
	}
	puts_("ok\r\n");
}

static int
run(char *line)
{
	char *argv[16];
	static char *envp[] = { "PATH=/bin:/sbin:/usr/bin:/usr/libexec", 0 };
	int argc = split(line, argv, 16);

	if (argc == 0) {
		return 0;
	}
	if (streq(argv[0], "exit")) {
		return 1;
	}
	if (streq(argv[0], "help")) {
		banner();
		return 0;
	}
	if (streq(argv[0], "ls")) {
		ls_(argc > 1 ? argv[1] : 0);
		return 0;
	}
	if (streq(argv[0], "w")) {
		if (argc < 4) {
			puts_("usage: w <path> <offset> <hex>\r\n");
		} else {
			write_(argv[1], argv[2], argv[3]);
		}
		return 0;
	}
	if (streq(argv[0], "reboot")) {
		sys3(SYS_reboot, 0, 0, 0);      /* RB_AUTOBOOT */
		puts_("reboot failed\r\n");
		return 0;
	}
	if (streq(argv[0], "cp")) {
		if (argc < 3) {
			puts_("usage: cp <src> <dst>\r\n");
		} else {
			cp_(argv[1], argv[2]);
		}
		return 0;
	}
	if (streq(argv[0], "mkdir")) {
		if (argc < 2) {
			puts_("usage: mkdir <dir>\r\n");
		} else {
			mkdir_p(argv[1]);
		}
		return 0;
	}
	if (streq(argv[0], "cat")) {
		if (argc < 2) {
			puts_("usage: cat <path>\r\n");
		} else {
			cat_(argv[1]);
		}
		return 0;
	}
	if (streq(argv[0], "echo")) {
		for (int i = 1; i < argc; i++) {
			if (i > 1) {
				puts_(" ");
			}
			puts_(argv[i]);
		}
		puts_("\r\n");
		return 0;
	}
	if (streq(argv[0], "rm")) {
		if (argc > 1 && sys3(SYS_unlink, (i64)argv[1], 0, 0) != 0) {
			puts_("rm: failed\r\n");
		}
		return 0;
	}
	if (streq(argv[0], "mkdir")) {
		if (argc > 1 && sys3(SYS_mkdir, (i64)argv[1], 0755, 0) != 0) {
			puts_("mkdir: failed\r\n");
		}
		return 0;
	}
	if (streq(argv[0], "cd")) {
		i64 r = sys3(SYS_chdir, (i64)(argc > 1 ? argv[1] : "/"), 0, 0);
		if (r != 0) {
			puts_("cd: failed\r\n");
		}
		return 0;
	}
	/* Try the bare name in the usual directories, then as given. */
	static const char *dirs[] = { "/bin/", "/sbin/", "/usr/bin/", "/usr/libexec/", "" };
	char path[256];

	for (int d = 0; d < 5; d++) {
		int i = 0;
		const char *p = dirs[d];

		while (*p && i < 200) {
			path[i++] = *p++;
		}
		const char *a = argv[0];
		while (*a && i < 250) {
			path[i++] = *a++;
		}
		path[i] = 0;
		if (sys3(SYS_open, (i64)path, O_RDONLY, 0) < 0) {
			continue;
		}
		i64 pid = sys3(SYS_fork, 0, 0, 0);
		if (pid == 0) {
			sys3(SYS_execve, (i64)path, (i64)argv, (i64)envp);
			puts_("exec failed: ");
			puts_(path);
			puts_("\r\n");
			sys3(SYS_exit, 1, 0, 0);
		}
		if (pid > 0) {
			i64 status = 0;
			sys4(SYS_wait4, pid, (i64)&status, 0, 0);
			if (status != 0) {
				puts_("[exit ");
				putn_(status);
				puts_("]\r\n");
			}
			return 0;
		}
		puts_("fork failed\r\n");
		return 0;
	}
	puts_(argv[0]);
	puts_(": not found\r\n");
	return 0;
}

static void
repl(void)
{
	char line[512];

	banner();
	for (;;) {
		puts_("# ");
		if (readline(line, sizeof(line)) < 0) {
			puts_("\r\n");
			return;                 /* caller decides: child exits, PID 1 waits */
		}
		if (run(line)) {
			sys3(SYS_exit, 0, 0, 0);
		}
	}
}

/*
 * XNU hands a static binary a stack pointer that is only 8-byte aligned
 * (sp = 0x16fdffc98 in one crash), and the first 16-byte store of the generated
 * prologue then raises an alignment fault, which arrives as SIGBUS with
 * pc = base+4. Align sp before any compiler-generated code runs.
 */
void cstart(void);

__attribute__((naked, noreturn, aligned(16))) void
_start(void)
{
	/* AND (immediate) cannot name SP, so bounce through x9. */
	__asm__ volatile(
		"mov x9, sp\n\t"
		"and x9, x9, #0xfffffffffffffff0\n\t"
		"mov sp, x9\n\t"
		"bl  cstart\n\t"
		"brk #1\n\t");
}

/* Accept connections forever; each one gets a child with the socket on
 * fd 0/1/2 and the REPL on the other end. */
/* UDP mode: one command per datagram to 192.168.7.2:4444, one datagram back.
 * The TCP path (bind/listen/accept) has wedged the USB driver within seconds on
 * this port while UDP traffic has always been fine, so the shell serves its REPL
 * over datagrams instead: the command is executed with stdout collected into the
 * reply buffer. */
static char udp_out[1200];
static int udp_out_len;

static void
udp_begin(void)
{
	udp_out_len = 0;
}

void
udp_emit(const char *s, int n)
{
	for (int i = 0; i < n && udp_out_len < (int)sizeof(udp_out) - 1; i++) {
		udp_out[udp_out_len++] = s[i];
	}
}

static void
serve_udp(void)
{
	struct sockaddr_in sa;
	i64 fd = -1;

	for (;;) {
		fd = sys3(SYS_socket, AF_INET, SOCK_DGRAM, 0);
		if (fd < 0) {
			sleep_ms(500);
			continue;
		}
		for (int i = 0; i < (int)sizeof(sa); i++) {
			((char *)&sa)[i] = 0;
		}
		sa.sin_len = sizeof(sa);
		sa.sin_family = AF_INET;
		sa.sin_port = (unsigned short)((4444 >> 8) | ((4444 & 0xff) << 8));
		sa.sin_addr = 0;
		if (sys3(SYS_bind, fd, (i64)&sa, sizeof(sa)) == 0) {
			break;
		}
		sys3(SYS_close, fd, 0, 0);
		sleep_ms(500);
	}
	for (;;) {
		char cmd[1200];
		struct sockaddr_in from;
		i64 fromlen = sizeof(from);
		i64 n = sys6(SYS_recvfrom, fd, (i64)cmd, sizeof(cmd) - 1, 0,
		    (i64)&from, (i64)&fromlen);

		if (n <= 0) {
			sleep_ms(20);
			continue;
		}
		cmd[n] = 0;
		char *nl = cmd;
		while (*nl && *nl != '\n' && *nl != '\r') {
			nl++;
		}
		*nl = 0;
		udp_begin();
		(void)run(cmd);                 /* output goes to udp_out */
		sys6(SYS_sendto, fd, (i64)udp_out, udp_out_len, 0, (i64)&from, fromlen);
	}
}

static void
serve(i64 lfd)
{
	for (;;) {
		i64 cfd = sys3(SYS_accept, lfd, 0, 0);

		if (cfd < 0) {
			sleep_ms(50);
			continue;
		}
		beacon("sweetsh: client connected\n");
		i64 pid = sys3(SYS_fork, 0, 0, 0);
		if (pid == 0) {
			sys3(SYS_close, lfd, 0, 0);
			sys3(SYS_dup2, cfd, 0, 0);
			sys3(SYS_dup2, cfd, 1, 0);
			sys3(SYS_dup2, cfd, 2, 0);
			if (cfd > 2) {
				sys3(SYS_close, cfd, 0, 0);
			}
			repl();
			sys3(SYS_exit, 0, 0, 0);
		}
		sys3(SYS_close, cfd, 0, 0);
	}
}

/*
 * Install this running image over another path (the launchd carrier) and reboot.
 *
 * The carrier is the file launchd executes, so replacing it normally needs a
 * recovery round trip: the shell cannot rewrite its own running image safely.
 * Instead the new build is copied into a spare file (/bin/ps), executed from
 * there with the argument "update", and that fresh process has no image in the
 * way - it overwrites the carrier and reboots.
 */
static int
running_from(const char *path)
{
	char hdr[16];
	i64 fd = sys3(SYS_open, (i64)path, O_RDONLY, 0);
	if (fd < 0) {
		return 0;
	}
	i64 n = sys3(SYS_read, fd, (i64)hdr, sizeof(hdr));
	sys3(SYS_close, fd, 0, 0);
	if (n != (i64)sizeof(hdr)) {
		return 0;
	}
	const char *self = (const char *)0x100000000;   /* our mapped Mach-O header */
	for (int i = 0; i < (int)sizeof(hdr); i++) {
		if (hdr[i] != self[i]) {
			return 0;
		}
	}
	return 1;
}

static void
install_self(const char *dst)
{
	static char buf[24000];
	i64 fd = sys3(SYS_open, (i64)"/bin/ps", O_RDONLY, 0);
	if (fd < 0) {
		puts_("update: cannot open /bin/ps\r\n");
		return;
	}
	i64 n = sys3(SYS_read, fd, (i64)buf, sizeof(buf));
	sys3(SYS_close, fd, 0, 0);
	if (n <= 0) {
		puts_("update: empty source\r\n");
		return;
	}
	fd = sys3(SYS_open, (i64)dst, O_WRONLY, 0);
	if (fd < 0) {
		puts_("update: cannot open dst\r\n");
		return;
	}
	i64 w = sys3(SYS_write, fd, (i64)buf, n);
	sys3(SYS_close, fd, 0, 0);
	puts_(w == n ? "update: written, rebooting\r\n" : "update: short write\r\n");
	sys3(SYS_reboot, 0, 0, 0);              /* RB_AUTOBOOT */
}

void
cstart(void)
{
	/* As PID 1 this process must never exit: the kernel panics with
	 * "initproc failed ... handshake structure not initialized" if it does.
	 * It also runs before the network stack has an interface, so binding can
	 * fail at first - retry instead of falling through to a REPL that would
	 * hit EOF on the console and exit. */
#ifdef SWEETSH_UDP_ONLY
	/* Am I running from the spare file rather than from the carrier? Compare the
	 * first bytes of my own mapped image with the file: if they match, this
	 * process was started from /bin/ps to install itself, and its image is not in
	 * the way, so it can safely overwrite the carrier. */
	if (running_from("/bin/ps")) {
		install_self("/System/Library/CoreServices/SpringBoard.app/SpringBoard");
	}
	for (;;) {
		serve_udp();            /* never returns */
	}
#endif
#ifdef SWEETSH_DELAY_MS
	/* test build: stay inert for a while so a wedge can be attributed to the
	 * process existing rather than to its socket calls */
	sleep_ms(SWEETSH_DELAY_MS);
#endif
	for (;;) {
		i64 lfd = listen_4444();

		if (lfd >= 0) {
			beacon_num("sweetsh: listening", 4444);
			console("sweetsh: up, listening on 4444\r\n");
			serve(lfd);             /* never returns */
		}
		console("sweetsh: bind 4444 failed, retrying\r\n");
		beacon_num("sweetsh: bind err", lfd);
		sleep_ms(1000);
	}
}
