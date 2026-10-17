using System.Collections.Generic;

namespace Acme;

/// <summary>The interface a service depends on.</summary>
public interface IStore
{
    string? Get(string key);
    void Put(string key, string value);
}

/// <summary>One implementation.</summary>
public class MemStore : IStore
{
    private readonly Dictionary<string, string> _data = new();

    public string? Get(string key) => _data.TryGetValue(key, out var v) ? v : null;

    public void Put(string key, string value) => _data[key] = value;
}

/// <summary>A decorator holding the interface it decorates.</summary>
public class AuditStore : IStore
{
    private readonly IStore _inner;
    private readonly List<string> _log = new();

    public AuditStore(IStore inner) => _inner = inner;

    public string? Get(string key)
    {
        Note("get");
        return _inner.Get(key);
    }

    public void Put(string key, string value)
    {
        Note("put");
        _inner.Put(key, value);
    }

    private void Note(string entry) => _log.Add(entry);
}

/// <summary>A static helper, called by name — the case that must not be missed.</summary>
public static class Text
{
    public static string Normalize(string value) => value.Trim();
}
