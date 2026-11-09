/* CIM Text.app launcher (modern Macs, 10.13+).
 *
 * The text edition is a Python program that wants a Terminal window. A bare
 * "CIM Text.command" is refused by Gatekeeper on Ventura and later when it
 * came out of a downloaded zip (scripts cannot be notarized). This tiny
 * Mach-O can be: it is signed with the Developer ID, hardened, notarized
 * and stapled inside the .app, so the double-click is the ordinary
 * "downloaded from the internet, open?" and nothing else.
 *
 * What it does: writes a one-line script into $TMPDIR (a file we create
 * carries no quarantine flag, so Terminal runs it without a word), which
 * execs the real launcher in our Resources folder, and asks Terminal to
 * open it through /usr/bin/open. No Apple Events, so no automation prompt.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libgen.h>
#include <limits.h>
#include <sys/stat.h>
#include <mach-o/dyld.h>

static void sh_quote(const char *in, char *out, size_t cap)
{
    /* 'single quoted', with embedded ' as '\'' */
    size_t n = 0;
    if (n < cap) out[n++] = '\'';
    for (; *in && n + 5 < cap; in++) {
        if (*in == '\'') { memcpy(out + n, "'\\''", 4); n += 4; }
        else out[n++] = *in;
    }
    if (n < cap) out[n++] = '\'';
    out[n < cap ? n : cap - 1] = 0;
}

int main(void)
{
    char exe[PATH_MAX], real[PATH_MAX], res[PATH_MAX], script[PATH_MAX];
    char q[PATH_MAX * 2], tmp[PATH_MAX];
    uint32_t sz = sizeof exe;
    const char *tmpdir;
    FILE *f;

    if (_NSGetExecutablePath(exe, &sz) != 0 || realpath(exe, real) == NULL) {
        fprintf(stderr, "CIM Text: cannot locate myself\n");
        return 1;
    }
    /* .../CIM Text.app/Contents/MacOS/CIM Text -> .../Contents/Resources */
    snprintf(res, sizeof res, "%s/../Resources", dirname(real));
    if (realpath(res, script) == NULL) {
        fprintf(stderr, "CIM Text: no Resources folder\n");
        return 1;
    }
    strlcpy(res, script, sizeof res);
    snprintf(script, sizeof script, "%s/CIM Text.command", res);
    if (access(script, R_OK) != 0) {
        fprintf(stderr, "CIM Text: missing %s\n", script);
        return 1;
    }

    tmpdir = getenv("TMPDIR");
    if (tmpdir == NULL || *tmpdir == 0) tmpdir = "/tmp";
    snprintf(tmp, sizeof tmp, "%s/CIM Text.command", tmpdir);
    f = fopen(tmp, "w");
    if (f == NULL) {
        perror("CIM Text: temp script");
        return 1;
    }
    sh_quote(script, q, sizeof q);
    fprintf(f, "#!/bin/sh\nexec /bin/sh %s\n", q);
    fclose(f);
    chmod(tmp, 0755);

    execl("/usr/bin/open", "open", "-a", "Terminal", tmp, (char *)NULL);
    perror("CIM Text: open");
    return 1;
}
