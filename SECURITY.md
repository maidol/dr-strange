# Security policy

## Reporting a vulnerability

Please report security issues **privately**, through GitHub's own reporting
form: [Security → Report a
vulnerability](https://github.com/wangyingsm/dr-strange/security/advisories/new).
It opens a private advisory visible only to the maintainers, and it is the
right channel even if you are unsure whether what you found is exploitable.

Please do not open a public issue for a suspected vulnerability.

What helps most, in rough order: the version (`drsg --version`) and platform,
what an attacker would gain, and the smallest input or sequence of commands
that shows the behaviour. A plugin involved in the finding is worth naming —
parsers run as sandboxed wasm, and whether a finding crosses that sandbox
changes its severity a great deal.

## Supported versions

Fixes land on `master` and go out in the next release. Only the latest release
is supported; there are no maintained release branches.

## Scope

Dr Strange runs as a library, a CLI, and a server. The parts most likely to
matter are the ones that read something it did not produce:

- **`drsg serve`** — the JSON-RPC, MCP and WebSocket surfaces, the bearer-token
  gate, and the same-origin fallback for a loopback bind with no token set.
- **The wasm plugin host** — parser plugins are sandboxed and given no
  filesystem or network access; an escape is the highest-severity finding in
  this project.
- **`digest`** — a parser reads whatever a repository contains, which is
  attacker-controlled input whenever the repository is not your own.
- **Mounts and attachments** — `db.mount` is confined to `[server]
  mount_root`; `plane.attach` can name another instance by URL.
- **The on-disk format** — a database file from elsewhere is untrusted input.

## What ships, and how to check it

Release archives carry [SLSA build
provenance](https://slsa.dev/) signed through Sigstore, so a download can be
traced to the workflow run and commit that produced it:

```console
$ gh attestation verify dr-strange-v<version>-<target>.tar.gz --repo wangyingsm/dr-strange
```

Every archive is also published with a `.sha256` beside it, which the install
scripts verify before unpacking — including when a download falls back to a
mirror.
