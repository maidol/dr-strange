/// The trait a service depends on — the indirection a `dyn` call goes through.
pub trait Store {
    fn get(&self, key: &str) -> Option<String>;
    fn put(&mut self, key: &str, value: String);
}

/// One implementation.
#[derive(Default)]
pub struct MemStore {
    data: std::collections::HashMap<String, String>,
}

impl Store for MemStore {
    fn get(&self, key: &str) -> Option<String> {
        self.data.get(key).cloned()
    }

    fn put(&mut self, key: &str, value: String) {
        self.data.insert(key.to_string(), value);
    }
}

/// A second implementation, so a trait call has more than one possible target.
pub struct AuditStore<S: Store> {
    inner: S,
    log: Vec<String>,
}

impl<S: Store> AuditStore<S> {
    pub fn new(inner: S) -> Self {
        Self { inner, log: Vec::new() }
    }

    fn note(&mut self, entry: &str) {
        self.log.push(entry.to_string());
    }
}

impl<S: Store> Store for AuditStore<S> {
    fn get(&self, key: &str) -> Option<String> {
        // A call on the generic parameter: the target is known only once S is
        // chosen, which a parser reading this file cannot do.
        self.inner.get(key)
    }

    fn put(&mut self, key: &str, value: String) {
        self.note("put");
        self.inner.put(key, value);
    }
}

/// A free function, called by name — the case that must not be missed.
pub fn normalize(value: &str) -> String {
    value.trim().to_string()
}
