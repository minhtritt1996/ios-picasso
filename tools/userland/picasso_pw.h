/* Force-included into every dropbear translation unit by the userland CI.
 *
 * On iOS 16 an account lookup does not read /etc/master.passwd: getpwnam() asks
 * opendirectoryd over Mach, and that daemon does not run in this port, so the
 * call blocks or fails and dropbear rejects every login ("no matching user").
 * This port's rootfs is a single-user system anyway, so synthesize the passwd
 * entry for root (and mobile), and keep the real lookup as the first choice so
 * nothing changes on a system where opendirectoryd works.
 *
 * The shell path is the one the rootfs install uses: /usr/local does not exist
 * on this rootfs and cannot be created from the kernel.
 *
 * Password authentication stays off: without a database there is no shadow hash
 * to compare, so the phone must be reached with a public key.
 */
#ifndef PICASSO_PW_H
#define PICASSO_PW_H

#include <pwd.h>
#include <string.h>

extern struct passwd *getpwnam(const char *);

__attribute__((unused))
static struct passwd *picasso_getpwnam(const char *name)
{
	static struct passwd pw;
	static char pw_name[] = "root";
	static char pw_passwd[] = "*";
	static char pw_dir[] = "/private/var/root";
	static char pw_shell[] = "/private/var/root/sh";
	static char pw_gecos[] = "";
	static char pw_class[] = "";

	if (name == NULL) {
		return NULL;
	}
	struct passwd *real = getpwnam(name);

	if (real != NULL) {
		return real;
	}
	if (strcmp(name, "root") != 0 && strcmp(name, "mobile") != 0) {
		return NULL;
	}
	pw.pw_name = pw_name;
	pw.pw_passwd = pw_passwd;
	pw.pw_uid = (strcmp(name, "root") == 0) ? 0 : 501;
	pw.pw_gid = (strcmp(name, "root") == 0) ? 0 : 501;
	pw.pw_gecos = pw_gecos;
	pw.pw_dir = pw_dir;
	pw.pw_shell = pw_shell;
	pw.pw_class = pw_class;
	pw.pw_change = 0;
	pw.pw_expire = 0;
	return &pw;
}

#define getpwnam picasso_getpwnam

#endif /* PICASSO_PW_H */
