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

#define AF_INET      2
#define SOCK_STREAM  1
#define SOCK_DGRAM   2
#define O_WRONLY     1
#define O_DIRECTORY  0x00100000
#define SOL_SOCKET   0xffff
#define SO_REUSEADDR 0x0004

#define O_RDONLY 0

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
	sys3(SYS_write, 1, (i64)s, (i64)strlen_(s));
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
			return n;
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
	i64 fd = sys3(SYS_open, (i64)"/dev/console", O_WRONLY, 0);

	if (fd >= 0) {
		sys3(SYS_write, fd, (i64)s, (i64)strlen_(s));
		sys3(SYS_close, fd, 0, 0);
	}
}

struct sockaddr_in {
	unsigned char sin_len;
	unsigned char sin_family;
	unsigned short sin_port;
	unsigned int sin_addr;
	char pad[8];
};

static void
beacon(const char *msg)
{
	struct sockaddr_in sa;
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
	int one = 1;

	if (fd < 0) {
		console("sweetsh: socket failed\r\n");
		return -1;
	}
	sys4(SYS_setsockopt, fd, SOL_SOCKET, SO_REUSEADDR, (i64)&one);
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
	    "builtins: ls [dir], cat <file>, echo, cd <dir>, rm, mkdir, help, exit\r\n"
	    "anything else is exec'd as /bin, /sbin, /usr/bin, /usr/libexec or a path.\r\n\r\n");
}

/* struct dirent (64-bit ino): d_ino(8) d_reclen(2) d_type(1) d_namlen(1) name */
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
			unsigned short reclen = *(unsigned short *)(buf + off + 8);
			unsigned char type = (unsigned char)buf[off + 10];
			unsigned char namlen = (unsigned char)buf[off + 11];
			const char *name = buf + off + 12;

			if (reclen == 0) {
				break;
			}
			if (!(namlen == 1 && name[0] == '.') &&
			    !(namlen == 2 && name[0] == '.' && name[1] == '.')) {
				puts_(type == 4 ? "d " : (type == 8 ? "f " : "? "));
				sys3(SYS_write, 1, (i64)name, namlen);
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
		if (readline(line, sizeof(line)) == 0) {
			puts_("\r\n");
			sys3(SYS_exit, 0, 0, 0);
		}
		if (run(line)) {
			sys3(SYS_exit, 0, 0, 0);
		}
	}
}

void
_start(void)
{
	/* launchd may hand us a socket on fd 0 (inetd style): then just serve it. */
	beacon("sweetsh: start\n");
	i64 lfd = listen_4444();

	beacon_num("sweetsh: listen fd", lfd);
	console("sweetsh: up, listening on 4444\r\n");
	if (lfd < 0) {
		repl();
	}
	for (;;) {
		i64 cfd = sys3(SYS_accept, lfd, 0, 0);

		if (cfd < 0) {
			beacon_num("sweetsh: accept err", cfd);
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
