//! An accuracy fixture, not a crate worth depending on.
//!
//! Rust writes types down more than Go or Python do, so the ambiguity moves:
//! a trait object's target is a runtime fact, a generic's is known only after
//! monomorphisation, and a macro can generate a call that is in no source
//! line a parser reads.

pub mod dispatch;
pub mod service;
pub mod store;
