use crate::store::{normalize, Store};

/// An unrelated type whose `get` shares a name with `Store::get`.
#[derive(Default)]
pub struct Cache {
    hits: Vec<String>,
}

impl Cache {
    pub fn get(&self, idx: usize) -> Option<&String> {
        self.hits.get(idx)
    }
}

/// Holds a trait object: which implementation runs is a runtime fact.
pub struct Service {
    store: Box<dyn Store>,
}

impl Service {
    pub fn new(store: Box<dyn Store>) -> Self {
        Self { store }
    }

    /// A `dyn` call — the ambiguous case.
    pub fn load(&self, key: &str) -> Option<String> {
        self.store.get(key)
    }

    /// A `dyn` call plus a free function whose target is written down.
    pub fn save(&mut self, key: &str, value: &str) {
        self.store.put(key, normalize(value));
    }

    /// Annotated parameter: `Cache::get`, never `Store::get`.
    pub fn warm(&self, cache: &Cache, idx: usize) -> Option<String> {
        cache.get(idx).cloned()
    }

    /// A call on `self` within the same impl.
    pub fn describe(&self) -> String {
        self.label()
    }

    fn label(&self) -> String {
        "service".to_string()
    }
}
