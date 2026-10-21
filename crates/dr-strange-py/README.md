# drsg-embedded

The [dr-strange](https://github.com/wangyingsm/dr-strange) graph engine inside
your own process: no server, no socket, no port. One file on disk, opened
directly.

```python
import asyncio
import drsg_embedded as drsg

async def main():
    db = await drsg.open("graph.drsg")
    print(await db.planes())
    print(await db.context("myrepo", "Service.Save"))
    print(await db.impact("myrepo", "normalize", depth=3))
    print(await db.deployed("myrepo"))

asyncio.run(main())
```

**Every method is a coroutine**, and every one does its work on a blocking
thread rather than on the event loop. A point lookup here is microseconds of
CPU, not I/O to wait on, so running it inline would stall every other task for
exactly as long as it takes.

**Calls name their plane.** A plane handle borrows the database it came from
and a Python object cannot hold a borrow, so the surface is the one the MCP
server and the CLI already use — `db.context("myrepo", "Service.Save")`.

## What this is and is not

This wraps the **engine**: planes, nodes, edges, and the agent verbs over a
graph that already exists. It does not digest a repository — that needs the
sandboxed wasm parser plugins, which would put tens of megabytes into every
wheel. Build a plane with the CLI and read it from here:

```console
$ drsg digest ./myrepo --apply --plane myrepo
```

For talking to a running `drsg serve` over JSON-RPC instead, the pure-Python
client is [`drsg`](https://pypi.org/project/drsg/).

## Status

Alpha. The surface above is settled; `cypher`, the change feed and bulk writes
are the next additions.
