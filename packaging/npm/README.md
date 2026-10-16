# Publishing drsg to npm

`npx drsg` is the install path most developers already have, and the one the
official MCP registry wants to see — the registry fetches servers through npm,
pypi, oci, nuget or mcpb, and drsg currently offers it only an OCI image that
cannot do the repository-local `drsg init` flow. npm is what makes that
listing describe the product. See `../mcp-registry/README.md`.

## Shape

Six packages, the way esbuild and swc do it:

- **`drsg`** — a few kilobytes: a `bin` wrapper and nothing else. Its
  `optionalDependencies` name all five platform packages, and npm installs the
  one whose `os`/`cpu` match.
- **`drsg-linux-x64`**, **`drsg-linux-arm64`**, **`drsg-darwin-x64`**,
  **`drsg-darwin-arm64`**, **`drsg-win32-x64`** — one prebuilt binary each,
  50–65 MB.

The alternative, a `postinstall` script that downloads from GitHub, was
rejected twice over: it does not run under `npm ci --ignore-scripts`, and it
would fetch at install time from the host that is slow enough to need
`--mirror` in `scripts/install.sh`. A binary inside the package is served by
npm's CDN, which mainland mirrors replicate — the same problem, solved once.

## Build

```console
$ gh release download v2.21.0 --dir /tmp/rel
$ just npm-build 2.21.0 /tmp/rel
```

Every archive is checked against the SHA-256 published beside it before any
binary is unpacked, whether it came off the network or out of a local
directory. The script writes `packaging/npm/dist/` and publishes nothing.

## Publish

Needs an npm account with publish rights and `npm login`. **Platform packages
first, wrapper last** — the wrapper's `optionalDependencies` pin the exact
version, so publishing it first leaves a window where `npm install drsg`
resolves to packages that do not exist.

```console
$ for p in linux-x64 linux-arm64 darwin-x64 darwin-arm64 win32-x64; do
    (cd packaging/npm/dist/drsg-$p && npm publish --access public)
  done
$ (cd packaging/npm/dist/drsg && npm publish --access public)
```

Then check the thing a reader will actually run:

```console
$ npx --yes drsg@2.21.0 --version
```

## Names

`drsg` and all five platform names were unregistered as of 2026-10-17;
`dr-strange` is taken by an unrelated package and is not an option. Claiming
`drsg` with a first publish is itself worth doing early.

## After the first publish

Three things become true, and none of them is automatic:

1. **The README should say so.** Until the package exists, neither edition
   advertises `npx drsg` — a README promising a command that fails is worse
   than one that omits it.
2. **The MCP registry entry should move to `registryType: "npm"`**, replacing
   the OCI compromise.
3. **Every release publishes the six packages automatically.** `release.yml`
   has an `npm` job that runs after the GitHub release, builds from the same
   artifacts the release was cut from, and publishes with npm provenance.

## The automatic path

The `npm` job needs one thing that is not in this repository: an **`NPM_TOKEN`
secret** (Settings → Secrets and variables → Actions), an automation token
from an npm account with publish rights. Without it the job skips itself and
says so — a fork, or this repository before the token is added, still gets a
complete GitHub release.

What it does, in order: downloads the release artifacts, verifies each against
its own SHA-256, publishes the five platform packages and then the wrapper,
and skips any version already on the registry so re-running a release is safe.
A tag with a hyphen (`v3.0.0-rc.1`) publishes under npm's `next` tag rather
than `latest`, matching how the release job already treats a pre-release.

`just npm-build` remains the manual path, for the first publish and for
anything the job cannot do.
