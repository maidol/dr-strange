/* What the Zig client translates: the C client's public header, plus the
 * allocator it hands ownership through. `@cImport` did this inline until Zig
 * removed it in 0.17, so the include list lives in a file the build step
 * translates instead. */
#include <stdlib.h>
#include <drsg.h>
