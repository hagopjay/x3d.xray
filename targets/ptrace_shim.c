// LD_PRELOAD probe: neutralise ptrace() anti-debug AND log every call to a file.
// This is the article's "probe that injects into the binary and records via logs".
//   gcc -shared -fPIC -O2 -o ptrace_shim.so ptrace_shim.c
//   LD_PRELOAD=./ptrace_shim.so ./target ...
// Works because the target imports ptrace dynamically (see `nm -D`).
// It would NOT work against a raw `syscall` instruction. Use gdb `catch syscall ptrace` then.
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ptrace.h>
#include <unistd.h>

long ptrace(enum __ptrace_request req, ...) {
    const char *log = getenv("SHIM_LOG") ? getenv("SHIM_LOG") : "/dev/null";
    FILE *f = fopen(log, "a");
    if (f) {
        fprintf(f, "[shim pid=%d] ptrace(req=%d) intercepted -> returning 0\n", getpid(), (int)req);
        fclose(f);
    }
    return 0;  // "yes, tracing you is fine, nobody else is attached"
}
