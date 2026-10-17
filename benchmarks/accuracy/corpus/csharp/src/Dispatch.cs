namespace Acme;

/// <summary>An extension method — dispatched statically by receiver type.</summary>
public static class TextExtensions
{
    public static string Shout(this string value) => Text.Normalize(value);
}

/// <summary>The ways a C# call target stops being written at the call site.</summary>
public static class Dispatch
{
    /// <summary>A delegate held in a local.</summary>
    public static string ViaDelegate(string value)
    {
        Func<string, string> f = Text.Normalize;
        return f(value);
    }

    /// <summary>A lambda, whose body is this method's own text.</summary>
    public static string ViaLambda(string value)
    {
        Func<string, string> f = v => Text.Normalize(v);
        return f(value);
    }

    /// <summary>Reflection: the method name is a string.</summary>
    public static object? ViaReflection(object target, string name) =>
        target.GetType().GetMethod(name)?.Invoke(target, null);

    /// <summary>An extension method call.</summary>
    public static string ViaExtension(string value) => value.Shout();

    /// <summary>The plain case, for contrast.</summary>
    public static string Direct(string value) => Text.Normalize(value);
}
