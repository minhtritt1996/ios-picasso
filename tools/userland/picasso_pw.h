/* Included into the dropbear translation units that do account lookups
 * (tools/userland/patch-dropbear.py adds the #include).
 *
 * On iOS 16 getpwnam()/getpwuid()/getgrnam() ask opendirectoryd over Mach. That
 * daemon does not run on this port, so a lookup can fail - or block for good,
 * which a "real lookup first, synthesize on failure" wrapper never recovers
 * from. This port is a single-root system, so the entries are synthesized
 * without ever calling the real lookups (same idea as ios-flame's
 * IOSONPIXEL_SINGLE_ROOT build).
 *
 * The login shell is /private/var/root/sh (dash): /usr/local does not exist on
 * this rootfs. Override with -DPICASSO_SHELL=\"...\" if needed.
 *
 * Password authentication stays off: there is no shadow database, so the phone
 * is reached with a public key only.
 */
#ifndef PICASSO_PW_H
#define PICASSO_PW_H

#include <grp.h>
#include <pwd.h>
#include <string.h>
#include <unistd.h>

#ifndef PICASSO_SHELL
#define PICASSO_SHELL "/private/var/root/sh"
#endif

__attribute__((unused))
static struct passwd *picasso_pw_fill(uid_t uid)
{
	static struct passwd pw;
	static char pw_name_root[] = "root";
	static char pw_name_mobile[] = "mobile";
	static char pw_passwd[] = "*";
	static char pw_dir[] = "/private/var/root";
	static char pw_shell[] = PICASSO_SHELL;
	static char pw_gecos[] = "";
	static char pw_class[] = "";

	memset(&pw, 0, sizeof(pw));
	pw.pw_name = uid == 0 ? pw_name_root : pw_name_mobile;
	pw.pw_passwd = pw_passwd;
	pw.pw_uid = uid == 0 ? geteuid() : uid;
	pw.pw_gid = uid == 0 ? getegid() : uid;
	pw.pw_gecos = pw_gecos;
	pw.pw_dir = pw_dir;
	pw.pw_shell = pw_shell;
#ifdef __APPLE__
	pw.pw_class = pw_class;
#else
	(void)pw_class;         /* host test builds: glibc has no pw_class */
#endif
	return &pw;
}

__attribute__((unused))
static struct passwd *picasso_getpwnam(const char *name)
{
	if (name == NULL) {
		return NULL;
	}
	if (strcmp(name, "root") == 0) {
		return picasso_pw_fill(0);
	}
	if (strcmp(name, "mobile") == 0) {
		return picasso_pw_fill(501);
	}
	return NULL;
}

__attribute__((unused))
static struct passwd *picasso_getpwuid(uid_t uid)
{
	if (uid == 0 || uid == 501) {
		return picasso_pw_fill(uid);
	}
	return NULL;
}

/* No "tty" group: sshpty.c then falls back to the user's own gid. */
__attribute__((unused))
static struct group *picasso_getgrnam(const char *name)
{
	(void)name;
	return NULL;
}

#define getpwnam picasso_getpwnam
#define getpwuid picasso_getpwuid
#define getgrnam picasso_getgrnam

#endif /* PICASSO_PW_H */
