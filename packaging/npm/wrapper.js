#!/usr/bin/env node
// Hand the process to the platform binary npm already installed.
//
// Each platform package declares its own `os`/`cpu`, so npm unpacks exactly
// one of them and skips the rest. Resolving through `require.resolve` means
// the lookup follows npm's own layout — hoisted, nested, or inside a
// workspace — rather than guessing at a path.
//
// `execFileSync` with `stdio: "inherit"` keeps drsg in charge of the
// terminal: it is an interactive CLI (`plugin install` prompts) and a
// long-running server, and both need the real stdin, not a pipe.

const { execFileSync } = require("node:child_process");

const PLATFORMS = {
  "linux x64": ["drsg-linux-x64", "drsg"],
  "linux arm64": ["drsg-linux-arm64", "drsg"],
  "darwin x64": ["drsg-darwin-x64", "drsg"],
  "darwin arm64": ["drsg-darwin-arm64", "drsg"],
  "win32 x64": ["drsg-win32-x64", "drsg.exe"],
};

const key = `${process.platform} ${process.arch}`;
const entry = PLATFORMS[key];

if (!entry) {
  console.error(
    `drsg: no prebuilt binary for ${key}.\n` +
      `Supported: ${Object.keys(PLATFORMS).join(", ")}.\n` +
      `Build from source instead: https://github.com/wangyingsm/dr-strange#install`,
  );
  process.exit(1);
}

const [pkg, binary] = entry;
let binPath;
try {
  binPath = require.resolve(`${pkg}/bin/${binary}`);
} catch {
  // The platform package is optional, so a failed or skipped install leaves
  // the wrapper with nothing to run rather than failing the whole install.
  console.error(
    `drsg: the binary package ${pkg} is not installed.\n` +
      `This happens when optional dependencies were skipped ` +
      `(--no-optional, --omit=optional, or an offline cache).\n` +
      `Install it directly:  npm install ${pkg}\n` +
      `Or use the standalone installer: https://github.com/wangyingsm/dr-strange#install`,
  );
  process.exit(1);
}

try {
  execFileSync(binPath, process.argv.slice(2), { stdio: "inherit" });
} catch (err) {
  // A non-zero exit from drsg is drsg's answer, not a wrapper failure: pass
  // the code through so scripts and CI see what they would see natively.
  if (typeof err.status === "number") process.exit(err.status);
  if (err.signal) process.exit(1);
  throw err;
}
