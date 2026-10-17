#include "store.h"

#include <stdio.h>

/* An unrelated struct with its own `get` — the same-name trap. */
typedef struct {
    const char *(*get)(int key);
} Cache;

static const char *cache_get(int key) {
    (void)key;
    return "cached";
}

typedef struct {
    Store *store;
} Service;

/* Through the function pointer: ambiguous. */
const char *service_load(Service *s, const char *key) {
    return s->store->get(s->store, key);
}

/* Through the pointer, plus a direct call whose target is written down. */
void service_save(Service *s, const char *key, const char *value) {
    s->store->put(s->store, key, normalize(value));
}

/* A direct call to a static in this file. */
const char *service_warm(Cache *c, int key) {
    (void)c;
    return cache_get(key);
}

const char *service_label(void) {
    return "service";
}

/* A direct call to another function in this file. */
void service_describe(Service *s) {
    (void)s;
    printf("%s\n", service_label());
}
