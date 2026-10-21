//! In-process Python bindings for the engine: a database opened inside the
//! caller's own process, with no server and no socket.
//!
//! **Every method is a coroutine, and every coroutine does its work on a
//! blocking thread.** A point lookup here is microseconds of CPU rather than
//! I/O to wait on, so running it on the event loop would stall every other
//! task for exactly as long as it takes; `spawn_blocking` keeps the loop free
//! and makes the `await` mean what it says.
//!
//! The surface is keyed by plane **name**, not by a handle. A `PlaneHandle`
//! borrows the database it came from, and a Python object cannot hold a
//! borrow — so the shape here is the one the MCP server and the CLI already
//! use, where every call names its plane.
//!
//! This wraps the engine alone. Digesting a repository needs the plugin host
//! (wasmtime), which would put tens of megabytes into every wheel, so a plane
//! is built by `drsg digest` and read from here.

use std::path::PathBuf;
use std::sync::Arc;

use dr_strange_core::{Database, compact, json, ops};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyList};

/// An engine error, as a Python exception. The message is the engine's own —
/// it says what it could not do and usually what to do instead, and
/// paraphrasing it here would only lose that.
fn engine_err(e: dr_strange_core::Error) -> PyErr {
    PyRuntimeError::new_err(e.to_string())
}

/// Run blocking engine work off the event loop, as a Python awaitable.
///
/// One helper rather than the same five lines per method: a `spawn_blocking`
/// that someone forgets is a method that quietly blocks the loop, and that is
/// not visible from the Python side at all.
fn awaitable<'py, T, F>(py: Python<'py>, work: F) -> PyResult<Bound<'py, PyAny>>
where
    F: FnOnce() -> PyResult<T> + Send + 'static,
    T: for<'a> IntoPyObject<'a> + Send + 'static,
{
    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        tokio::task::spawn_blocking(work)
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("the engine thread panicked: {e}")))?
    })
}

/// An engine answer on its way to Python.
///
/// The blocking thread has no GIL and cannot build Python objects, and a
/// `Bound` is neither `Send` nor `'static`, so the conversion has to happen
/// when the future resolves. Carrying the `serde_json::Value` across is what
/// lets a caller receive dicts and lists rather than a JSON string to parse a
/// second time.
struct Json(serde_json::Value);

impl<'py> IntoPyObject<'py> for Json {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        to_py(py, &self.0)
    }
}

/// A `serde_json::Value` as Python data, so a caller gets dicts and lists
/// rather than a JSON string to parse again.
fn to_py<'py>(py: Python<'py>, v: &serde_json::Value) -> PyResult<Bound<'py, PyAny>> {
    use serde_json::Value;
    Ok(match v {
        Value::Null => py.None().into_bound(py),
        Value::Bool(b) => b.into_pyobject(py)?.to_owned().into_any(),
        Value::Number(n) => match (n.as_i64(), n.as_f64()) {
            (Some(i), _) => i.into_pyobject(py)?.into_any(),
            (None, Some(f)) => f.into_pyobject(py)?.into_any(),
            _ => return Err(PyValueError::new_err(format!("{n} is not a Python number"))),
        },
        Value::String(s) => s.into_pyobject(py)?.into_any(),
        Value::Array(items) => {
            let out = PyList::empty(py);
            for item in items {
                out.append(to_py(py, item)?)?;
            }
            out.into_any()
        }
        Value::Object(fields) => {
            let out = PyDict::new(py);
            for (k, item) in fields {
                out.set_item(k, to_py(py, item)?)?;
            }
            out.into_any()
        }
    })
}

/// Python data as a `serde_json::Value`, which is the form every conversion in
/// `dr_strange_core::json` already takes — so the property rules (what counts
/// as a vector, how a described property is spelled) stay in one place and
/// this does not grow a second opinion about them.
fn from_py(v: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    use serde_json::Value;
    if v.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(b) = v.extract::<bool>() {
        return Ok(Value::Bool(b));
    }
    if let Ok(i) = v.extract::<i64>() {
        return Ok(Value::from(i));
    }
    if let Ok(f) = v.extract::<f64>() {
        return Ok(serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number));
    }
    if let Ok(s) = v.extract::<String>() {
        return Ok(Value::String(s));
    }
    if let Ok(dict) = v.cast::<PyDict>() {
        let mut out = serde_json::Map::new();
        for (k, item) in dict.iter() {
            out.insert(k.extract::<String>()?, from_py(&item)?);
        }
        return Ok(Value::Object(out));
    }
    if let Ok(list) = v.cast::<PyList>() {
        return list
            .iter()
            .map(|i| from_py(&i))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    Err(PyValueError::new_err(format!(
        "{} is not JSON-shaped data",
        v.get_type().name()?
    )))
}

/// One node to write: its key, its labels, and its properties.
struct NodeSpec {
    key: String,
    labels: Vec<String>,
    props: dr_strange_core::Properties,
}

/// One edge to write, by the keys of its endpoints.
struct EdgeSpec {
    src: String,
    dst: String,
    ty: String,
    props: dr_strange_core::Properties,
}

/// A required string field, named in the error when it is missing — a
/// `KeyError` from inside a loop says nothing about which row was wrong.
fn field(row: &Bound<'_, PyDict>, name: &str, what: &str) -> PyResult<String> {
    row.get_item(name)?
        .ok_or_else(|| PyValueError::new_err(format!("{what} needs `{name}`")))?
        .extract()
}

fn properties_of(row: &Bound<'_, PyDict>) -> PyResult<dr_strange_core::Properties> {
    match row.get_item("properties")? {
        None => Ok(dr_strange_core::Properties::new()),
        Some(p) => json::json_to_properties(&from_py(&p)?).map_err(engine_err),
    }
}

/// An open database, shared across tasks.
///
/// Cheap to clone and safe to use from several at once: the engine takes a
/// shared lock for reads and serialises its single writer itself, so nothing
/// here holds the GIL while the engine works.
#[pyclass(name = "Database", frozen)]
struct PyDatabase {
    inner: Arc<Database>,
}

impl PyDatabase {
    fn db(&self) -> Arc<Database> {
        Arc::clone(&self.inner)
    }
}

#[pymethods]
impl PyDatabase {
    /// Create a plane. Fails if one of that name exists.
    fn create_plane<'py>(&self, py: Python<'py>, name: String) -> PyResult<Bound<'py, PyAny>> {
        let db = self.db();
        awaitable(py, move || {
            db.create_plane(&name, dr_strange_core::Properties::new())
                .map(|_| ())
                .map_err(engine_err)
        })
    }

    /// Every plane, as `{"name": str, "nodes": int, "edges": int}`.
    fn planes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let db = self.db();
        awaitable(py, move || {
            let mut out = Vec::new();
            for (_, name) in db.planes().map_err(engine_err)? {
                let plane = db.plane(&name).map_err(engine_err)?;
                let counters = plane.counters().map_err(engine_err)?;
                out.push(serde_json::json!({
                    "name": name,
                    "nodes": counters.nodes,
                    "edges": counters.edges,
                }));
            }
            Ok(Json(serde_json::Value::Array(out)))
        })
    }

    /// Write nodes, each `{"key": str, "labels": [str], "properties": {...}}`.
    /// Returns how many were written.
    fn write_nodes<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        nodes: Vec<Py<PyDict>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let specs: Vec<NodeSpec> = nodes
            .iter()
            .map(|row| {
                let row = row.bind(py);
                Ok(NodeSpec {
                    key: field(row, "key", "a node")?,
                    labels: match row.get_item("labels")? {
                        Some(l) => l.extract()?,
                        None => Vec::new(),
                    },
                    props: properties_of(row)?,
                })
            })
            .collect::<PyResult<_>>()?;
        let db = self.db();
        awaitable(py, move || {
            let plane = db.plane(&plane).map_err(engine_err)?;
            let mut txn = plane.write().map_err(engine_err)?;
            let mut written = 0usize;
            for spec in &specs {
                let labels: Vec<&str> = spec.labels.iter().map(String::as_str).collect();
                txn.create_node_with_key(&spec.key, &labels, spec.props.clone())
                    .map_err(engine_err)?;
                written += 1;
            }
            txn.commit().map_err(engine_err)?;
            Ok(written)
        })
    }

    /// Write edges, each `{"src": str, "dst": str, "type": str,
    /// "properties": {...}}`, resolved by the endpoints' keys. An edge whose
    /// endpoint is not in the plane is an error, not a silent drop.
    fn write_edges<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        edges: Vec<Py<PyDict>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let specs: Vec<EdgeSpec> = edges
            .iter()
            .map(|row| {
                let row = row.bind(py);
                Ok(EdgeSpec {
                    src: field(row, "src", "an edge")?,
                    dst: field(row, "dst", "an edge")?,
                    ty: field(row, "type", "an edge")?,
                    props: properties_of(row)?,
                })
            })
            .collect::<PyResult<_>>()?;
        let db = self.db();
        awaitable(py, move || {
            let plane = db.plane(&plane).map_err(engine_err)?;
            let mut txn = plane.write().map_err(engine_err)?;
            let mut written = 0usize;
            for spec in &specs {
                let find = |key: &str| -> PyResult<dr_strange_core::NodeId> {
                    plane
                        .node_by_key(key)
                        .map_err(engine_err)?
                        .map(|n| n.id)
                        .ok_or_else(|| {
                            PyValueError::new_err(format!("no node is keyed `{key}` in this plane"))
                        })
                };
                txn.create_edge(
                    find(&spec.src)?,
                    find(&spec.dst)?,
                    &spec.ty,
                    spec.props.clone(),
                )
                .map_err(engine_err)?;
                written += 1;
            }
            txn.commit().map_err(engine_err)?;
            Ok(written)
        })
    }

    /// One node by its key, or `None`.
    fn get_node<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        key: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let db = self.db();
        awaitable(py, move || {
            let plane = db.plane(&plane).map_err(engine_err)?;
            let node = plane.node_by_key(&key).map_err(engine_err)?;
            Ok(node.map(|n| Json(json::node_to_json(&n))))
        })
    }

    /// What a symbol is, who calls it, what it calls — the primary agent verb.
    fn context<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        name: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.verb(py, plane, move |p| compact::context(p, &name))
    }

    /// One symbol's properties, without its edges.
    fn describe<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        name: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.verb(py, plane, move |p| compact::describe(p, &name))
    }

    /// The shortest recorded call path from one symbol to another.
    fn trace<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        from: String,
        to: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.verb(py, plane, move |p| compact::trace(p, &from, &to))
    }

    /// The region around a symbol: labels, edge types, hubs.
    #[pyo3(signature = (plane, name, depth = 2))]
    fn fathom<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        name: String,
        depth: usize,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.verb(py, plane, move |p| compact::fathom(p, &name, depth))
    }

    /// A repository's history at a glance, over the `_git` plane beside this
    /// one.
    #[pyo3(signature = (plane, limit = None))]
    fn history<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        limit: Option<usize>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let db = self.db();
        awaitable(py, move || {
            let name = compact::history_plane_name(&plane);
            let plane = db
                .plane(&name)
                .or_else(|_| db.plane(&plane))
                .map_err(engine_err)?;
            compact::history(&plane, limit).map_err(engine_err)
        })
    }

    /// Everything reaching a symbol through incoming structural edges.
    #[pyo3(signature = (plane, name, depth = 3))]
    fn impact<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        name: String,
        depth: usize,
    ) -> PyResult<Bound<'py, PyAny>> {
        let db = self.db();
        awaitable(py, move || {
            compact::impact(
                &db,
                compact::Impact {
                    plane: &plane,
                    name: &name,
                    depth,
                    // This plane alone: an embedded caller has one database,
                    // and nothing here serves another drsg to ask.
                    across: None,
                    remote: None,
                    instance: None,
                },
            )
            .map_err(engine_err)
        })
    }

    /// What commit a running thing was built from, and what runs a commit —
    /// the ops plane joined to the history beside it.
    #[pyo3(signature = (plane, target = None))]
    fn deployed<'py>(
        &self,
        py: Python<'py>,
        plane: String,
        target: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let db = self.db();
        awaitable(py, move || {
            let (ops_plane, history) = ops::planes(
                &db,
                &ops::Asked {
                    plane: &plane,
                    ops: None,
                    history: None,
                },
            )
            .map_err(engine_err)?;
            ops::deployed(&ops_plane, history.as_ref(), target.as_deref()).map_err(engine_err)
        })
    }

    fn __repr__(&self) -> String {
        "<drsg_embedded.Database>".to_string()
    }
}

impl PyDatabase {
    /// The shape every text verb shares: resolve the plane, run, return its
    /// compact answer.
    fn verb<'py, F>(&self, py: Python<'py>, plane: String, run: F) -> PyResult<Bound<'py, PyAny>>
    where
        F: FnOnce(&dr_strange_core::PlaneHandle<'_>) -> dr_strange_core::Result<String>
            + Send
            + 'static,
    {
        let db = self.db();
        awaitable(py, move || {
            let plane = db.plane(&plane).map_err(engine_err)?;
            run(&plane).map_err(engine_err)
        })
    }
}

/// Open the database at `path`, creating it if it is not there.
#[pyfunction]
#[pyo3(signature = (path, retain_commits = None))]
fn open(py: Python<'_>, path: PathBuf, retain_commits: Option<u64>) -> PyResult<Bound<'_, PyAny>> {
    awaitable(py, move || {
        let db = Database::open(&path).map_err(engine_err)?;
        // Retention is a property of the handle rather than of the file, so
        // every opener has to set it — the CLI learned that the hard way.
        db.set_retention(retain_commits);
        Ok(PyDatabase {
            inner: Arc::new(db),
        })
    })
}

/// A database held entirely in memory, for tests and examples.
#[pyfunction]
fn open_memory(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    awaitable(py, move || {
        Ok(PyDatabase {
            inner: Arc::new(Database::in_memory().map_err(engine_err)?),
        })
    })
}

#[pymodule]
fn drsg_embedded(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<PyDatabase>()?;
    m.add_function(wrap_pyfunction!(open, m)?)?;
    m.add_function(wrap_pyfunction!(open_memory, m)?)?;
    Ok(())
}
