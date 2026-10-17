use crate::store::normalize;

/// A closure stored in a variable, then called.
pub fn via_closure(value: &str) -> String {
    let f = |v: &str| normalize(v);
    f(value)
}

/// A function pointer — the name is a value, not a call site.
pub fn via_fn_pointer(value: &str) -> String {
    let f: fn(&str) -> String = normalize;
    f(value)
}

/// A generic call bounded by a trait: the target depends on the instantiation.
pub fn via_generic<T: ToString>(value: T) -> String {
    value.to_string()
}

/// A macro that expands to a call. The call is in no line a parser reads.
macro_rules! normalized {
    ($v:expr) => {
        normalize($v)
    };
}

pub fn via_macro(value: &str) -> String {
    normalized!(value)
}
