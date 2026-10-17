namespace Acme;

/// <summary>An unrelated class whose Get shares a name with IStore.Get.</summary>
public class Cache
{
    private readonly Dictionary<int, string> _hits = new();

    public string? Get(int key) => _hits.TryGetValue(key, out var v) ? v : null;
}

/// <summary>Constructor injection: the field's type is the interface.</summary>
public class Service
{
    private readonly IStore _store;

    public Service(IStore store) => _store = store;

    /// <summary>Through the interface — the implementation is a runtime fact.</summary>
    public string? Load(string key) => _store.Get(key);

    /// <summary>Through the interface, plus a static whose target is written down.</summary>
    public void Save(string key, string value) => _store.Put(key, Text.Normalize(value));

    /// <summary>The parameter is typed Cache, so the target is knowable.</summary>
    public string? Warm(Cache cache, int key) => cache.Get(key);

    /// <summary>A call on this, within the same class.</summary>
    public string Describe() => Label();

    private string Label() => "service";
}
