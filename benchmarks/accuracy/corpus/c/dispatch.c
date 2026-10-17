#include "store.h"

/* A function pointer held in a local — the name is a value, not a call. */
char *via_fn_pointer(const char *value) {
    char *(*f)(const char *) = normalize;
    return f(value);
}

/* A macro that expands to a call: the call is in no line a parser reads. */
#define NORMALIZED(v) normalize(v)

char *via_macro(const char *value) {
    return NORMALIZED(value);
}

/* The plain case, for contrast. */
char *direct(const char *value) {
    return normalize(value);
}
