// Resolve every call in the TypeScript fixture with the compiler's own checker.
//
// This is the strongest oracle in the suite after Go's: `tsc` is the authority
// on what a call expression binds to, and it is the same authority an editor
// uses. Its bound is the mirror of Go's RTA — the checker answers for the
// *declared* type, so a call through an interface resolves to the interface's
// method and never to an implementation. That is exactly the line the key
// draws between `resolved` and `ambiguous`, which is why it can check both.
//
// Emits JSON: [["caller", "callee"], ...] in the key's vocabulary.

import ts from "typescript";
import { readdirSync } from "node:fs";
import { basename, join, resolve } from "node:path";

const dir = process.argv[2];
// Absolute throughout: the compiler normalises the file names it reports, and
// comparing those against relative paths silently matched almost nothing.
const files = readdirSync(dir)
  .filter((f) => f.endsWith(".ts"))
  .map((f) => resolve(join(dir, f)));
const inCorpus = new Set(files);

const program = ts.createProgram(files, {
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  strict: true,
  skipLibCheck: true,
});
const checker = program.getTypeChecker();

/** `module.Class.method` or `module.function` for a declaration node. */
function qualify(node) {
  const file = node.getSourceFile();
  if (!file || !inCorpus.has(resolve(file.fileName))) return null;
  const mod = basename(file.fileName, ".ts");
  const parts = [];
  for (let n = node; n; n = n.parent) {
    if (ts.isMethodDeclaration(n) || ts.isFunctionDeclaration(n) || ts.isMethodSignature(n)) {
      if (n.name) parts.unshift(n.name.getText());
    } else if (ts.isClassDeclaration(n) || ts.isInterfaceDeclaration(n)) {
      if (n.name) parts.unshift(n.name.getText());
    }
  }
  return parts.length ? `${mod}.${parts.join(".")}` : null;
}

const edges = new Set();

for (const file of program.getSourceFiles()) {
  if (!inCorpus.has(resolve(file.fileName))) continue;
  const walk = (node) => {
    if (ts.isCallExpression(node)) {
      const caller = qualify(node);
      let sym = checker.getSymbolAtLocation(
        ts.isPropertyAccessExpression(node.expression)
          ? node.expression.name
          : node.expression,
      );
      // An imported name resolves to its import specifier first; the original
      // declaration is one alias hop away, and that is what the key names.
      if (sym && sym.flags & ts.SymbolFlags.Alias) {
        sym = checker.getAliasedSymbol(sym);
      }
      const decl = sym?.declarations?.[0];
      const callee = decl ? qualify(decl) : null;
      if (caller && callee && caller !== callee) edges.add(JSON.stringify([caller, callee]));
    }
    ts.forEachChild(node, walk);
  };
  walk(file);
}

console.log(JSON.stringify([...edges].map(JSON.parse), null, 0));
