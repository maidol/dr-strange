// Resolve every call in the C# fixture with Roslyn's semantic model.
//
// This is the strongest oracle available for C#, and the same shape as the
// TypeScript one: the compiler's own answer for what a call expression binds
// to. Its bound is likewise the same — the model answers for the *declared*
// type, so a call through an interface resolves to the interface's method and
// never to an implementation. That is the line the key draws between
// `resolved` and `ambiguous`, so it checks both sides of it.
//
// Emits JSON: [["caller","callee"], ...] in the key's vocabulary,
// `Namespace.Type.Method`.

using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

var dir = args.Length > 0 ? args[0] : ".";
var files = Directory.GetFiles(dir, "*.cs", SearchOption.AllDirectories)
    .Where(f => !f.Contains("/obj/") && !f.Contains("/bin/"))
    .OrderBy(f => f)
    .ToList();

var trees = files
    .Select(f => CSharpSyntaxTree.ParseText(File.ReadAllText(f), path: f))
    .ToList();

var compilation = CSharpCompilation.Create(
    "fixture",
    trees,
    Basic.Reference.Assemblies.Net80.References.All,
    new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));

// A fixture that does not compile would make every answer below meaningless,
// so say so rather than reporting an empty graph.
var errors = compilation.GetDiagnostics()
    .Where(d => d.Severity == DiagnosticSeverity.Error)
    .Select(d => d.ToString())
    .ToList();
if (errors.Count > 0)
{
    Console.Error.WriteLine("the fixture does not compile:");
    foreach (var e in errors.Take(20)) Console.Error.WriteLine("  " + e);
    return 1;
}

static string? Name(ISymbol? symbol)
{
    if (symbol is not IMethodSymbol method) return null;
    // An extension method called as `value.Shout()` reduces to the instance
    // form; the key names the static method that declares it.
    method = method.ReducedFrom ?? method;
    var type = method.ContainingType?.ToDisplayString();
    return type is null ? null : $"{type}.{method.Name}";
}

var edges = new SortedSet<string>(StringComparer.Ordinal);
foreach (var tree in trees)
{
    var model = compilation.GetSemanticModel(tree);
    foreach (var call in tree.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>())
    {
        var callee = Name(model.GetSymbolInfo(call).Symbol);
        if (callee is null) continue;

        // The caller is the member whose body this call sits in. A lambda is
        // part of the method that declares it, as every other key reads it.
        var owner = call.Ancestors()
            .FirstOrDefault(a => a is MethodDeclarationSyntax or PropertyDeclarationSyntax);
        if (owner is null) continue;
        var caller = Name(model.GetDeclaredSymbol(owner));
        if (caller is null || caller == callee) continue;

        edges.Add(JsonSerializer.Serialize(new[] { caller, callee }));
    }
}

Console.WriteLine("[" + string.Join(",", edges) + "]");
return 0;
