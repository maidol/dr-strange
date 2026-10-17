/* An accuracy fixture, not a library worth linking.
 *
 * C has no interfaces, so the indirection is explicit: a struct of function
 * pointers, which is how C codebases from the kernel down express the same
 * idea. A parser can see the struct; it cannot see which function was
 * assigned to it. */
#ifndef STORE_H
#define STORE_H

typedef struct Store Store;

/* The vtable: the C version of "the target is a runtime fact". */
struct Store {
    const char *(*get)(Store *self, const char *key);
    void (*put)(Store *self, const char *key, const char *value);
};

const char *mem_get(Store *self, const char *key);
void mem_put(Store *self, const char *key, const char *value);

const char *audit_get(Store *self, const char *key);
void audit_put(Store *self, const char *key, const char *value);

char *normalize(const char *value);

#endif
