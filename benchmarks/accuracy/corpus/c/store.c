#include "store.h"

#include <string.h>

static char buffer[256];

/* One implementation of the vtable's `get`. */
const char *mem_get(Store *self, const char *key) {
    (void)self;
    return key;
}

void mem_put(Store *self, const char *key, const char *value) {
    (void)self;
    (void)key;
    (void)value;
}

static void note(const char *entry) {
    strncpy(buffer, entry, sizeof(buffer) - 1);
}

/* A second implementation, holding another Store — the decorator shape. */
typedef struct {
    Store base;
    Store *inner;
} AuditStore;

const char *audit_get(Store *self, const char *key) {
    AuditStore *a = (AuditStore *)self;
    note("get");
    /* Through the pointer: a parser cannot know which function this is. */
    return a->inner->get(a->inner, key);
}

void audit_put(Store *self, const char *key, const char *value) {
    AuditStore *a = (AuditStore *)self;
    note("put");
    a->inner->put(a->inner, key, value);
}

/* A plain function, called by name — the case that must not be missed. */
char *normalize(const char *value) {
    strncpy(buffer, value, sizeof(buffer) - 1);
    return buffer;
}
