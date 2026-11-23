// crackme1 — "the debugger lies"            *** SPOILERS: author's source ***
//
// Layers of defence, each one silent (no "debugger detected!" message):
//   1. Strings are XOR-encrypted and decoded on the stack only when printed.
//   2. A constructor calls ptrace(PTRACE_TRACEME). Under gdb/strace that fails,
//      and the serial algorithm's seed is quietly poisoned.
//   3. main() scans derive() for 0xCC (int3 software breakpoints) and poisons
//      the seed again if it finds one.
// Poisoned runs still "work"; they just compute a different, wrong serial.
// If you dump the expected serial from memory under a naive debugger, you get a
// key that fails in the real world.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include "crackme1_strings.h"

static volatile uint32_t g_salt = 0;

static void reveal(char *out, const unsigned char *enc, size_t n) {
    for (size_t i = 0; i < n; i++) out[i] = enc[i] ^ (unsigned char)(0x33 + 7 * i);
    out[n] = 0;
}

__attribute__((constructor)) static void guard(void) {
    if (ptrace(PTRACE_TRACEME, 0, 0, 0) == -1) g_salt ^= 0xA5A5A5A5u;
}

__attribute__((noinline)) uint32_t derive(const char *name) {
    uint32_t h = 0x1337C0DEu ^ g_salt;
    size_t n = strlen(name);
    for (size_t i = 0; i < n; i++) {
        h ^= (uint8_t)name[i];
        h = (h << 5) | (h >> 27);
        h *= 0x9E3779B1u;
    }
    return h ^ ((uint32_t)n << 24);
}

static int parse_serial(const char *s, uint32_t *out) {
    if (strlen(s) != 9 || s[4] != '-') return 0;
    char a[5], b[5], *e1, *e2;
    memcpy(a, s, 4); a[4] = 0;
    memcpy(b, s + 5, 4); b[4] = 0;
    unsigned long hi = strtoul(a, &e1, 16), lo = strtoul(b, &e2, 16);
    if (*e1 || *e2) return 0;
    *out = (uint32_t)(hi << 16 | lo);
    return 1;
}

int main(int argc, char **argv) {
    char msg[64];
    if (argc != 3) {
        reveal(msg, E_USAGE, sizeof E_USAGE); puts(msg); return 2;
    }
    const volatile unsigned char *p = (const volatile unsigned char *)(void *)derive;
    for (int i = 0; i < 48; i++)
        if (p[i] == 0xCC) g_salt ^= 0x5A5A0000u;

    size_t n = strlen(argv[1]);
    uint32_t got;
    if (n < 4 || n > 32 || !parse_serial(argv[2], &got) || got != derive(argv[1])) {
        reveal(msg, E_BAD, sizeof E_BAD); puts(msg); return 1;
    }
    reveal(msg, E_OK, sizeof E_OK); printf(msg, argv[1]);
    return 0;
}
