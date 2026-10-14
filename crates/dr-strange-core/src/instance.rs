//! What one process serves: its own database, and the databases it mounts
//! (issue #44).
//!
//! A repository is digested into its own store, and an operator ends up with
//! one per repository. Serving each from its own process costs a port, a
//! process and a copy of the cache apiece, and a client configured for one
//! port reaches one repository — so this holds several open at once and
//! resolves a plane name across all of them.
//!
//! Consolidating into one store would answer the same question and is not what
//! this does: the stores already exist, re-digesting them is hours of parsing
//! and embedding to produce what is on disk, and one store has **one writer
//! slot**, so every repository's `serve watch` fold would queue behind every
//! other's. Separate stores write in parallel.
//!
//! Resolution hands back an owned [`Arc<Database>`] rather than a borrow:
//! [`PlaneHandle`](crate::PlaneHandle) borrows the database it reads, and a
//! table that can change while the process runs cannot lend one out. A caller
//! keeps the `Arc` for as long as it holds handles from it.

use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use crate::api::{Database, PlaneHandle};
use crate::error::{Error, Result};
use crate::types::{PropDesc, PropValue, Properties};

/// The default plane every database carries, skipped when a mount is read.
///
/// It exists from creation whether or not anything was written to it, so every
/// mount would collide on it and the first mount would win a name that means
/// nothing. A digested repository's planes are named after the repository.
const SKIPPED: &str = "startup";

/// Separates a mount's prefix from a plane's own name, where `--as` gave one.
pub const PREFIX_SEP: char = '/';

/// A database this process serves besides its own.
#[derive(Clone, Debug)]
pub struct Mount {
    /// The name this mount is known by — its directory's, or what `--as` said.
    pub name: String,
    /// Where it was opened from.
    pub path: PathBuf,
    /// The names its planes answer to here, which carry `name/` when the
    /// mount was given a prefix.
    pub planes: Vec<String>,
    /// Whether those names carry the prefix.
    pub prefixed: bool,
    db: Arc<Database>,
}

impl Mount {
    /// The database itself, for a caller that already knows which mount holds
    /// the plane it wants.
    pub fn database(&self) -> &Arc<Database> {
        &self.db
    }

    /// The name this mount's own database knows `served` by, or `None` when
    /// the name is not one this mount could answer for at all.
    ///
    /// A prefixed mount answers only for names carrying its prefix — without
    /// that, a bare name would be looked up inside it and a plane there could
    /// answer for a name it was deliberately given a prefix to keep apart.
    fn serves(&self, served: &str) -> Option<String> {
        match self.prefixed {
            true => served
                .strip_prefix(&format!("{}{PREFIX_SEP}", self.name))
                .filter(|inner| !inner.is_empty())
                .map(str::to_string),
            false => (served != SKIPPED).then(|| served.to_string()),
        }
    }
}

/// Which database answers for a plane, and under which name there.
///
/// The `Arc` is handed over rather than a borrow because
/// [`PlaneHandle`](crate::PlaneHandle) borrows the database it reads, and a
/// mount table that can change while the process runs cannot lend one out. A
/// caller keeps this for as long as it holds handles from it.
pub struct Serving {
    /// The database to read.
    pub db: Arc<Database>,
    /// The plane's name *in that database*, which differs from the name asked
    /// for only on a prefixed mount.
    pub plane: String,
    /// The mount it came from, or `None` for this process's own database.
    pub mount: Option<String>,
}

/// One plane this instance serves, and where it comes from.
#[derive(Debug)]
pub struct Served {
    /// The name it answers to.
    pub plane: String,
    /// The mount holding it, or `None` for this process's own database.
    pub mount: Option<String>,
}

/// A request to mount a database.
pub struct Mounting<'a> {
    /// The directory to open.
    pub path: &'a Path,
    /// The name to serve it under, prefixing its planes. `None` names the
    /// mount after its directory and serves its planes under their own names.
    pub as_prefix: Option<&'a str>,
    /// The directory a mounted path must sit under. `None` is unconfined,
    /// which is a process already running as the user whose files these are —
    /// the CLI, the stdio server. A shared server always passes a grant, or a
    /// caller holding its token could open a database anywhere on the host
    /// and then read the trees its planes name.
    pub grant: Option<&'a Path>,
    /// Record this mount on the host, so a restart serves it again.
    ///
    /// Asked for rather than always: a mount named on the command line is
    /// that invocation's, and remembering it would mean a later `drsg serve`
    /// mounting a database nobody asked it for. A mount made over the wire
    /// has no command line to be named on again, so it is remembered.
    pub remember: bool,
}

/// What a mount brought.
#[derive(Debug)]
pub struct Mounted {
    /// The mount as it is now recorded.
    pub mount: Mount,
    /// Planes held but not served: `startup`, always.
    pub skipped: Vec<String>,
}

/// The databases one process serves.
/// Called after a database is mounted, with the mount just added.
///
/// How `serve watch` gives each mounted repository its own watcher: a mount
/// may arrive at boot (replayed), on the command line, or over the wire hours
/// later, and all three go through [`Instance::mount`] — so one hook covers
/// them where three call sites would have drifted.
pub type OnMount = Arc<dyn Fn(&Mount) + Send + Sync>;

pub struct Instance {
    host: Arc<Database>,
    mounts: RwLock<Vec<Mount>>,
    on_mount: RwLock<Option<OnMount>>,
}

impl Instance {
    /// An instance serving one database and nothing else — what every process
    /// that never mounts anything has.
    pub fn new(host: Arc<Database>) -> Self {
        Self {
            host,
            mounts: RwLock::new(Vec::new()),
            on_mount: RwLock::new(None),
        }
    }

    /// Run `f` for every database mounted from now on.
    ///
    /// Set before anything is mounted — a replay at startup is a mount like
    /// any other, and a hook installed after it would miss exactly the
    /// mounts an operator already asked for.
    pub fn on_mount(&self, f: OnMount) {
        *self
            .on_mount
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(f);
    }

    /// This process's own database: where a plane is created, and what the
    /// instance-wide reads (`db.stats`, a snapshot) are about.
    pub fn host(&self) -> &Arc<Database> {
        &self.host
    }

    /// The database serving `plane`, and the name that database knows it by
    /// — this process's own, or a mount's.
    ///
    /// The host is asked first, so a plane here is never shadowed by a mount;
    /// a mount whose plane name would do that is refused at [`Self::mount`]
    /// rather than silently losing.
    ///
    /// Each mount is asked its *own* database rather than the plane list
    /// recorded at mount time: that list is what collisions and listings are
    /// computed from, and a plane renamed or dropped through this instance
    /// would leave it answering for a name that is no longer there.
    pub fn serving(&self, plane: &str) -> Result<Serving> {
        if self.host.plane(plane).is_ok() {
            return Ok(Serving {
                db: self.host.clone(),
                plane: plane.to_string(),
                mount: None,
            });
        }
        for m in self.read().iter() {
            if let Some(inner) = m.serves(plane)
                && m.db.plane(&inner).is_ok()
            {
                return Ok(Serving {
                    db: m.db.clone(),
                    plane: inner,
                    mount: Some(m.name.clone()),
                });
            }
        }
        Err(Error::NotFound(format!("plane '{plane}'")))
    }

    /// Every plane this instance serves, the host's first.
    pub fn planes(&self) -> Result<Vec<Served>> {
        let mut out: Vec<Served> = self
            .host
            .planes()?
            .into_iter()
            .map(|(_, plane)| Served { plane, mount: None })
            .collect();
        for m in self.read().iter() {
            for plane in &m.planes {
                out.push(Served {
                    plane: plane.clone(),
                    mount: Some(m.name.clone()),
                });
            }
        }
        Ok(out)
    }

    /// What this instance mounts, in the order it was mounted.
    pub fn mounts(&self) -> Vec<Mount> {
        self.read().clone()
    }

    /// Open a database and serve its planes alongside this process's own.
    ///
    /// Needs a storage backend: there is no path to open without one.
    ///
    /// Refused whole rather than partly: a plane name already served would
    /// otherwise be shadowed or silently dropped, and which of the two
    /// happened is not something a reader should have to discover. The error
    /// names every clash and the `--as` prefix that resolves them.
    #[cfg(any(feature = "redb-backend", feature = "native-backend"))]
    pub fn mount(&self, req: Mounting<'_>) -> Result<Mounted> {
        let path = req.path;
        // A mount serves what is already there. `Database::open` creates on
        // demand, which is right for `--db` and wrong here: a mistyped path,
        // or a recorded one whose repository has moved, would otherwise be
        // answered with a brand-new empty database rather than with the
        // reason — and `--across all` would then list a plane-less graph as
        // one it read.
        if !path.exists() {
            return Err(Error::NotFound(format!(
                "no database at {} — mounting opens one, it does not create it",
                path.display()
            )));
        }
        if let Some(grant) = req.grant
            && !dir_within(grant, path)
        {
            return Err(Error::InvalidArgument(format!(
                "{} is not under {}, the directory this server may mount from — \
                 set `[server] mount_root` to widen it",
                path.display(),
                grant.display()
            )));
        }
        let name = match req.as_prefix {
            Some(prefix) => prefix.to_string(),
            None => mount_name(path)?,
        };
        if name.is_empty() || name.contains(PREFIX_SEP) {
            return Err(Error::InvalidArgument(format!(
                "'{name}' is not a mount name: it must be non-empty and hold no '{PREFIX_SEP}'"
            )));
        }
        if self.read().iter().any(|m| m.name == name) {
            return Err(Error::Conflict(format!(
                "this instance already mounts '{name}'"
            )));
        }

        // Opening takes the directory's exclusive lock, so a database another
        // process holds is refused here, by the engine, with its own message.
        let db = Arc::new(Database::open(path)?);
        let prefixed = req.as_prefix.is_some();
        let (mut planes, mut skipped) = (Vec::new(), Vec::new());
        for (id, plane) in db.planes()? {
            if id == crate::types::PlaneId::STARTUP || plane == SKIPPED {
                skipped.push(plane);
                continue;
            }
            planes.push(match prefixed {
                true => format!("{name}{PREFIX_SEP}{plane}"),
                false => plane,
            });
        }
        let clashes: Vec<&String> = planes
            .iter()
            .filter(|p| self.serving(p).is_ok())
            .collect::<Vec<_>>();
        if !clashes.is_empty() {
            let named: Vec<&str> = clashes.iter().map(|p| p.as_str()).collect();
            return Err(Error::Conflict(format!(
                "{} already serves {}: mount it under a prefix with `--as <name>`",
                match clashes.len() {
                    1 => "this instance",
                    _ => "this instance already",
                },
                named.join(", ")
            )));
        }

        let mount = Mount {
            name,
            path: path.to_path_buf(),
            planes,
            prefixed,
            db,
        };
        // Recorded before it is served, so a mount that answers a request was
        // one a restart will serve again.
        if req.remember {
            Remembered::remember(&self.host.plane(SKIPPED)?, &mount)?;
        }
        self.write().push(mount.clone());
        tracing::info!(
            name = %mount.name,
            path = %mount.path.display(),
            planes = mount.planes.len(),
            "mounted a database"
        );
        // After it is served, so a hook that reads the plane finds it there.
        let hook = self
            .on_mount
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        if let Some(f) = hook {
            f(&mount);
        }
        Ok(Mounted { mount, skipped })
    }

    /// Stop serving a mount, releasing its database's lock once the last
    /// handle to it drops — which is immediately, unless a call is still
    /// running against it.
    pub fn unmount(&self, name: &str) -> Result<Option<Mount>> {
        let mut mounts = self.write();
        let Some(at) = mounts.iter().position(|m| m.name == name) else {
            // Forget it anyway: a recorded mount whose directory has gone was
            // never served this run, and must still be forgettable.
            Remembered::forget(&self.host.plane(SKIPPED)?, name)?;
            return Ok(None);
        };
        let gone = mounts.remove(at);
        drop(mounts);
        Remembered::forget(&self.host.plane(SKIPPED)?, name)?;
        gone.db.save_sidecars();
        tracing::info!(name, path = %gone.path.display(), "unmounted a database");
        Ok(Some(gone))
    }

    /// Serve again what was recorded, and say what could not be.
    ///
    /// A recorded directory may have moved, been deleted, or be held by
    /// another process — none of which should stop a server from starting, so
    /// each failure is returned for the caller to log rather than raised.
    ///
    /// Needs a storage backend, since replaying is mounting.
    #[cfg(any(feature = "redb-backend", feature = "native-backend"))]
    pub fn replay(&self, grant: Option<&Path>) -> Result<Vec<(String, Error)>> {
        let mut failed = Vec::new();
        for rec in Remembered::of(&self.host.plane(SKIPPED)?)? {
            let req = Mounting {
                path: Path::new(&rec.path),
                as_prefix: rec.prefixed.then_some(rec.name.as_str()),
                grant,
                // Already recorded; writing it again on every boot would be
                // the same row rewritten for nothing.
                remember: false,
            };
            if let Err(e) = self.mount(req) {
                failed.push((rec.name, e));
            }
        }
        Ok(failed)
    }

    /// Persist every mounted database's vector and keyword sidecars, as a
    /// server does for its own at shutdown.
    pub fn save_sidecars(&self) {
        self.host.save_sidecars();
        for m in self.read().iter() {
            m.db.save_sidecars();
        }
    }

    fn read(&self) -> std::sync::RwLockReadGuard<'_, Vec<Mount>> {
        self.mounts
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn write(&self) -> std::sync::RwLockWriteGuard<'_, Vec<Mount>> {
        self.mounts
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

/// The property a mount list is recorded under, on the host's own default
/// plane.
///
/// The host's `startup` plane rather than a side table, for the reason
/// `attachments` lives on a plane (arch/09 §2): a plane's own map is already
/// durable, already readable by anything that reads a plane, and already what
/// `plane.set_props` edits. `startup` because a mount is a fact about the
/// *instance*, not about any one of its planes, and that is the plane every
/// database has.
pub const MOUNTS: &str = "mounts";

/// What the property means, for anything that reads the plane's own map.
const WHAT_IT_IS: &str = "the databases this process also serves — a name, the \
                          directory it was opened from, and whether its planes \
                          carry the name as a prefix";

/// A mount as it is written down: enough to open it again, and nothing about
/// the run that made it.
#[derive(Debug, Clone)]
pub struct Remembered {
    pub name: String,
    pub path: String,
    pub prefixed: bool,
}

impl Remembered {
    /// What `plane` records, in the order mounted.
    pub fn of(plane: &PlaneHandle<'_>) -> Result<Vec<Self>> {
        Ok(Self::read(&plane.properties()?))
    }

    fn read(props: &Properties) -> Vec<Self> {
        let Some(PropDesc {
            value: PropValue::List(items),
            ..
        }) = props.get(MOUNTS)
        else {
            return Vec::new();
        };
        items.iter().filter_map(Self::from_prop).collect()
    }

    fn from_prop(v: &PropValue) -> Option<Self> {
        let PropValue::Map(m) = v else { return None };
        let text = |k: &str| match m.get(k).map(|p| &p.value) {
            Some(PropValue::Str(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        };
        Some(Self {
            name: text("name")?,
            path: text("path")?,
            prefixed: matches!(
                m.get("prefixed").map(|p| &p.value),
                Some(PropValue::Bool(true))
            ),
        })
    }

    fn to_prop(&self) -> PropValue {
        let mut m = Properties::new();
        m.insert(
            "name".into(),
            PropDesc::new(PropValue::Str(self.name.clone())),
        );
        m.insert(
            "path".into(),
            PropDesc::new(PropValue::Str(self.path.clone())),
        );
        if self.prefixed {
            m.insert("prefixed".into(), PropDesc::new(PropValue::Bool(true)));
        }
        PropValue::Map(m)
    }

    fn write(plane: &PlaneHandle<'_>, edit: impl FnOnce(&mut Vec<Self>)) -> Result<()> {
        plane.update_properties(|props| {
            let mut have = Self::read(props);
            edit(&mut have);
            let items = have.iter().map(Self::to_prop).collect();
            props.insert(
                MOUNTS.into(),
                PropDesc::described(WHAT_IT_IS, PropValue::List(items)),
            );
        })
    }

    /// Write `mount` down, replacing any record of the same name. Read and
    /// write happen under one write lock, so two mounts cannot lose each
    /// other.
    ///
    /// Only [`Instance::mount`] records, and that needs a backend to open
    /// what it records.
    #[cfg(any(feature = "redb-backend", feature = "native-backend"))]
    fn remember(plane: &PlaneHandle<'_>, mount: &Mount) -> Result<()> {
        let rec = Self {
            name: mount.name.clone(),
            path: mount.path.display().to_string(),
            prefixed: mount.prefixed,
        };
        Self::write(plane, |have| {
            match have.iter_mut().find(|r| r.name == rec.name) {
                Some(slot) => *slot = rec,
                None => have.push(rec),
            }
        })
    }

    fn forget(plane: &PlaneHandle<'_>, name: &str) -> Result<()> {
        Self::write(plane, |have| have.retain(|r| r.name != name))
    }
}

/// The name a mount takes from its own path: the directory's own name, or its
/// parent's where the database is a conventional `graph.drsg` inside the
/// repository it holds.
#[cfg(any(feature = "redb-backend", feature = "native-backend"))]
fn mount_name(path: &Path) -> Result<String> {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| Error::InvalidArgument(format!("{} names no directory", path.display())))?;
    let Some(stem) = name.strip_suffix(".drsg") else {
        return Ok(name.to_string());
    };
    // `repo/graph.drsg` is the layout `drsg init` writes, so the repository is
    // what the operator means by that mount, not the file inside it.
    if stem == "graph"
        && let Some(parent) = path
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            .filter(|n| !n.is_empty())
    {
        return Ok(parent.to_string());
    }
    Ok(stem.to_string())
}

/// Whether `dir` resolves to `root` or somewhere beneath it.
///
/// Both sides are canonicalized, so a symlink out of the granted directory is
/// judged by where it lands rather than by how it is spelled; a path that does
/// not exist is not within anything.
pub fn dir_within(root: &Path, dir: &Path) -> bool {
    match (root.canonicalize(), dir.canonicalize()) {
        (Ok(root), Ok(dir)) => dir.starts_with(&root),
        _ => false,
    }
}

#[cfg(all(test, any(feature = "redb-backend", feature = "native-backend")))]
mod tests {
    use super::*;
    use crate::types::Properties;

    /// A database on disk holding one named plane with one node in it.
    fn store(dir: &Path, plane: &str) -> PathBuf {
        let path = dir.join(plane);
        let db = Database::open(&path).unwrap();
        let p = db.create_plane(plane, Properties::new()).unwrap();
        let mut txn = p.write().unwrap();
        txn.create_node_with_key(&format!("{plane}::thing"), &["Thing"], Properties::new())
            .unwrap();
        txn.commit().unwrap();
        drop(db);
        path
    }

    fn host() -> Instance {
        Instance::new(Arc::new(Database::in_memory().unwrap()))
    }

    fn mounting<'a>(path: &'a Path, grant: Option<&'a Path>) -> Mounting<'a> {
        Mounting {
            path,
            as_prefix: None,
            grant,
            remember: false,
        }
    }

    #[test]
    fn a_mounts_planes_answer_under_their_own_names() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let inst = host();

        let mounted = inst.mount(mounting(&orders, None)).unwrap();
        assert_eq!(mounted.mount.name, "svc-orders");
        assert_eq!(mounted.mount.planes, ["svc-orders"]);
        // Every database carries `startup`; it is held and not served.
        assert_eq!(mounted.skipped, ["startup"]);

        let at = inst.serving("svc-orders").unwrap();
        assert_eq!(at.mount.as_deref(), Some("svc-orders"));
        assert_eq!(at.plane, "svc-orders");
        assert!(at.db.plane(&at.plane).is_ok());

        let served: Vec<(String, Option<String>)> = inst
            .planes()
            .unwrap()
            .into_iter()
            .map(|s| (s.plane, s.mount))
            .collect();
        assert!(served.contains(&("startup".into(), None)), "{served:?}");
        assert!(
            served.contains(&("svc-orders".into(), Some("svc-orders".into()))),
            "{served:?}"
        );
    }

    /// The host is asked first, so nothing a mount brings can shadow a plane
    /// this process holds itself.
    #[test]
    fn a_plane_name_already_served_refuses_the_whole_mount() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let inst = host();
        inst.host
            .create_plane("svc-orders", Properties::new())
            .unwrap();

        let err = inst.mount(mounting(&orders, None)).unwrap_err().to_string();
        assert!(err.contains("svc-orders"), "{err}");
        assert!(err.contains("--as"), "it names the remedy: {err}");
        assert!(inst.mounts().is_empty(), "nothing was recorded");
    }

    /// `--as` is that remedy: the same database, under a prefix, with the
    /// plane inside it still asked for by its own name.
    #[test]
    fn a_prefix_keeps_a_clashing_mount_apart() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let inst = host();
        inst.host
            .create_plane("svc-orders", Properties::new())
            .unwrap();

        let mounted = inst
            .mount(Mounting {
                path: &orders,
                as_prefix: Some("far"),
                grant: None,
                remember: false,
            })
            .unwrap();
        assert_eq!(mounted.mount.planes, ["far/svc-orders"]);
        // The host's own plane of that name is untouched.
        let mine = inst.serving("svc-orders").unwrap();
        assert!(mine.mount.is_none(), "the host still answers for its own");
        assert!(Arc::ptr_eq(&mine.db, inst.host()));
        // And the prefixed name reaches the mount, under its own name there.
        let far = inst.serving("far/svc-orders").unwrap();
        assert_eq!(
            (far.mount.as_deref(), far.plane.as_str()),
            (Some("far"), "svc-orders")
        );
        assert!(far.db.plane(&far.plane).is_ok());
        // A prefixed mount answers for nothing else: the bare name is the
        // host's, and a name under no prefix reaches no mount at all.
        assert!(inst.serving("far/nope").is_err());
    }

    #[test]
    fn a_path_outside_the_grant_is_refused_by_name() {
        let tmp = tempfile::tempdir().unwrap();
        let granted = tmp.path().join("repos");
        std::fs::create_dir_all(&granted).unwrap();
        let outside = store(tmp.path(), "elsewhere");
        let inst = host();

        let err = inst
            .mount(mounting(&outside, Some(&granted)))
            .unwrap_err()
            .to_string();
        assert!(err.contains("elsewhere"), "{err}");
        assert!(err.contains("mount_root"), "it names the widening: {err}");

        // The same database under the grant is allowed.
        let inside = store(&granted, "svc-orders");
        assert!(inst.mount(mounting(&inside, Some(&granted))).is_ok());
    }

    /// Opening is what takes the directory's lock, so a database another
    /// process holds is refused by the engine rather than by a check here —
    /// and the message is the engine's, which already says what to do.
    #[cfg(feature = "native-backend")]
    #[test]
    fn a_database_another_process_holds_is_refused() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let held = Database::open(&orders).unwrap();
        let inst = host();

        let err = inst.mount(mounting(&orders, None)).unwrap_err().to_string();
        assert!(err.contains("already open"), "{err}");
        drop(held);
        assert!(inst.mount(mounting(&orders, None)).is_ok(), "freed");
    }

    #[test]
    fn unmounting_stops_serving_it_and_frees_the_database() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let inst = host();
        inst.mount(mounting(&orders, None)).unwrap();

        let gone = inst.unmount("svc-orders").unwrap().expect("was mounted");
        assert_eq!(gone.path, orders);
        assert!(inst.serving("svc-orders").is_err());
        assert!(inst.mounts().is_empty());
        drop(gone);
        // The lock went with it: another handle can open the directory now.
        assert!(Database::open(&orders).is_ok());

        // Unmounting what was never mounted is said, not an error.
        assert!(inst.unmount("svc-orders").unwrap().is_none());
    }

    #[test]
    fn mounting_the_same_name_twice_is_refused() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let billing = store(tmp.path(), "svc-billing");
        let inst = host();
        inst.mount(mounting(&orders, None)).unwrap();

        let err = inst
            .mount(Mounting {
                path: &billing,
                as_prefix: Some("svc-orders"),
                grant: None,
                remember: false,
            })
            .unwrap_err()
            .to_string();
        assert!(err.contains("already mounts 'svc-orders'"), "{err}");
    }

    /// A mount made over the wire has no command line to be named on again,
    /// so it is written down and served again on the next start.
    #[test]
    fn a_remembered_mount_is_served_again_after_a_restart() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let host = tmp.path().join("host");
        let names = |inst: &Instance| -> Vec<String> {
            inst.mounts().iter().map(|m| m.name.clone()).collect()
        };

        {
            let inst = Instance::new(Arc::new(Database::open(&host).unwrap()));
            inst.mount(Mounting {
                path: &orders,
                as_prefix: None,
                grant: None,
                remember: true,
            })
            .unwrap();
            assert_eq!(names(&inst), ["svc-orders"]);
            inst.unmount("svc-orders").unwrap();
        }
        // Unmounted, so nothing is replayed.
        {
            let inst = Instance::new(Arc::new(Database::open(&host).unwrap()));
            assert!(inst.replay(None).unwrap().is_empty());
            assert!(names(&inst).is_empty(), "forgotten with the unmount");
            inst.mount(Mounting {
                path: &orders,
                as_prefix: Some("far"),
                grant: None,
                remember: true,
            })
            .unwrap();
        }
        // Mounted again, under a prefix — and the prefix is remembered too.
        {
            let inst = Instance::new(Arc::new(Database::open(&host).unwrap()));
            assert!(inst.replay(None).unwrap().is_empty());
            assert_eq!(names(&inst), ["far"]);
            assert!(inst.serving("far/svc-orders").is_ok());
        }
    }

    /// A recorded directory that has moved must not stop a process starting,
    /// so a replay reports what it could not open instead of failing.
    #[test]
    fn a_replay_reports_what_it_could_not_mount() {
        let tmp = tempfile::tempdir().unwrap();
        let orders = store(tmp.path(), "svc-orders");
        let host = tmp.path().join("host");
        {
            let inst = Instance::new(Arc::new(Database::open(&host).unwrap()));
            inst.mount(Mounting {
                path: &orders,
                as_prefix: None,
                grant: None,
                remember: true,
            })
            .unwrap();
        }
        std::fs::rename(&orders, tmp.path().join("moved")).unwrap();

        {
            let inst = Instance::new(Arc::new(Database::open(&host).unwrap()));
            let failed = inst.replay(None).unwrap();
            assert_eq!(failed.len(), 1, "{failed:?}");
            assert_eq!(failed[0].0, "svc-orders");
            // The reason, not a silently created empty database in its place.
            assert!(
                failed[0].1.to_string().contains("no database at"),
                "{:?}",
                failed[0].1
            );
            assert!(inst.mounts().is_empty());
            // And it can be forgotten, though it was never served this run.
            assert!(inst.unmount("svc-orders").unwrap().is_none());
        }
        let inst = Instance::new(Arc::new(Database::open(&host).unwrap()));
        assert!(
            Remembered::of(&inst.host().plane("startup").unwrap())
                .unwrap()
                .is_empty(),
            "forgotten, so the next start does not retry it"
        );
    }

    /// `repo/graph.drsg` is what `drsg init` writes, so the repository names
    /// the mount — not the file inside it, which is the same word every time.    /// `repo/graph.drsg` is what `drsg init` writes, so the repository names
    /// the mount — not the file inside it, which is the same word every time.
    #[test]
    fn a_mount_is_named_after_its_repository_not_its_file() {
        assert_eq!(
            mount_name(Path::new("/r/svc-orders")).unwrap(),
            "svc-orders"
        );
        assert_eq!(
            mount_name(Path::new("/r/svc-orders/graph.drsg")).unwrap(),
            "svc-orders"
        );
        assert_eq!(mount_name(Path::new("/r/orders.drsg")).unwrap(), "orders");
    }
}
