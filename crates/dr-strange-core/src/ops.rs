//! The join from something running back to the commit that built it
//! (`arch/10-ops.md` §5).
//!
//! An ops plane holds `Image` nodes carrying whatever revision a pipeline
//! wrote into the tag; a history plane holds the commits. Planes are hard
//! namespaces and a cross-plane edge is rejected at the storage layer, so the
//! join is a *read* of two planes rather than an edge: take `revision` from
//! one side and resolve it against the other.
//!
//! Nothing here is new machinery. [`crate::rev`] already accepts an
//! abbreviated sha at git's own minimum length and already answers an
//! ambiguous prefix with candidates instead of a guess, which is the whole of
//! the hard part.
//!
//! Every way the join fails to hold is a [`Provenance`] of its own rather
//! than an empty answer. A floating tag, a digest-only reference and an image
//! built from another repository are three different facts, and a verb that
//! returned nothing for all three would not answer the only question worth
//! asking of it.

use crate::api::{Database, PlaneHandle};
use crate::compact::{history_plane_name, ops_plane_name, prop_str};
use crate::error::Result;
use crate::rev::{CommitRef, RevAnswer, resolve_rev};
use crate::types::{Dir, NodeRecord};

/// Hex digits of a digest shown before it is cut short.
const DIGEST_SHOWN: usize = 12;
/// Images listed before a plane-wide answer says how many it left out.
const IMAGES_SHOWN: usize = 50;
/// Things named on one image's "run by" line before the same.
const RUNNERS_SHOWN: usize = 6;
/// The label every ops reader mints a container image under.
const IMAGE: &str = "Image";
/// The edge from whatever runs an image to the image.
const USES_IMAGE: &str = "USES_IMAGE";
/// The edge from a workload to the containers it declares.
const RUNS: &str = "RUNS";
/// The edge from anything to the namespace it is in.
const IN_NAMESPACE: &str = "IN_NAMESPACE";

/// What an `Image` node turned out to say about where it came from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Provenance {
    /// The join held: this commit built the image.
    Commit(CommitRef),
    /// The revision is abbreviated and several commits fit it, newest first.
    Candidates(Vec<CommitRef>),
    /// A revision no commit of this history holds.
    Elsewhere {
        /// The revision the tag carried.
        revision: String,
    },
    /// The tag names no revision: `latest`, `stable`, a version number.
    Floating {
        /// The tag, so a reader sees what was asked of it.
        tag: String,
    },
    /// Pinned by digest, which fixes the bytes and says nothing about a commit.
    Pinned {
        /// The digest as written.
        digest: String,
    },
    /// Neither tag nor digest, so the reference means `latest` by implication.
    Untagged,
    /// There is no history plane to resolve the revision against.
    NoHistory {
        /// The revision that went unresolved.
        revision: String,
    },
}

impl Provenance {
    /// The one line a surface prints for this case.
    ///
    /// Here rather than in each surface: the MCP verb and the CLI must say the
    /// same sentence, and two copies of a sentence are one sentence with a
    /// disagreement waiting.
    pub fn stated(&self) -> String {
        match self {
            Provenance::Commit(c) => commit_line(c),
            Provenance::Candidates(cs) => format!(
                "{} commits fit that revision, newest first: {}",
                cs.len(),
                cs.iter()
                    .map(|c| c.sha.short())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            Provenance::Elsewhere { revision } => format!(
                "revision `{revision}` is in no commit of this history; the usual cause is an \
                 image built from another repository"
            ),
            Provenance::Floating { tag } => format!("no revision: the tag `{tag}` names none"),
            Provenance::Pinned { digest } => format!(
                "pinned by digest ({}); provenance needs a registry call",
                short_digest(digest)
            ),
            Provenance::Untagged => {
                "no revision: the reference carries neither tag nor digest".to_string()
            }
            Provenance::NoHistory { revision } => {
                format!("revision `{revision}`, with no history plane to resolve it against")
            }
        }
    }

    /// The commit this image was built from, when exactly one was named.
    pub fn commit(&self) -> Option<&CommitRef> {
        match self {
            Provenance::Commit(c) => Some(c),
            _ => None,
        }
    }
}

/// Where one `Image` node came from, resolved against a history plane.
///
/// `history` is `None` when there is no history plane — a digest run with
/// `--no-git` records none — so that the answer says as much instead of
/// reading as "no commit built this".
///
/// No head hint is passed to [`resolve_rev`]: a revision out of a tag is a
/// sha, never `HEAD`-relative, so there is nothing for a hint to stand in for.
pub fn provenance(image: &NodeRecord, history: Option<&PlaneHandle<'_>>) -> Result<Provenance> {
    let props = &image.properties;
    let Some(revision) = prop_str(props, "revision") else {
        // A digest is the more precise fact when a reference carries both.
        return Ok(match (prop_str(props, "digest"), prop_str(props, "tag")) {
            (Some(digest), _) => Provenance::Pinned {
                digest: digest.to_string(),
            },
            (None, Some(tag)) => Provenance::Floating {
                tag: tag.to_string(),
            },
            (None, None) => Provenance::Untagged,
        });
    };
    let Some(history) = history else {
        return Ok(Provenance::NoHistory {
            revision: revision.to_string(),
        });
    };
    Ok(match resolve_rev(history, None, revision)? {
        RevAnswer::One(commit) => Provenance::Commit(commit),
        RevAnswer::Many(commits) => Provenance::Candidates(commits),
        RevAnswer::None(_) => Provenance::Elsewhere {
            revision: revision.to_string(),
        },
    })
}

/// The one line a commit is printed on, wherever it is printed.
fn commit_line(c: &CommitRef) -> String {
    let mut out = format!(
        "{}  {}  \"{}\"  ({})",
        c.sha.short(),
        c.date,
        c.summary,
        c.author
    );
    if !c.reachable {
        out.push_str("  [a rewrite left this commit behind]");
    }
    out
}

/// A digest cut down the way a registry client prints one: the algorithm and
/// the first few hex digits.
fn short_digest(digest: &str) -> String {
    match digest.split_once(':') {
        Some((algo, hex)) => format!("{algo}:{}", &hex[..hex.len().min(DIGEST_SHOWN)]),
        None => digest.chars().take(DIGEST_SHOWN).collect(),
    }
}

/// One thing that runs an image.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Use {
    /// The workload or infrastructure resource, by key.
    pub thing: String,
    /// What kind of thing it is.
    pub kind: String,
    /// The namespace it is in, when the plane records one.
    pub namespace: Option<String>,
    /// The container the image is named in, when one sits between.
    pub through: Option<String>,
}

/// One image an ops plane holds: where it came from, and what runs it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImageRow {
    /// The reference as written, which is the node's key.
    pub reference: String,
    /// What its revision turned out to name.
    pub provenance: Provenance,
    /// Everything in this plane that runs it; empty when nothing does.
    pub used_by: Vec<Use>,
}

/// One image a thing runs, and where that image came from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Runs {
    /// The reference as written.
    pub reference: String,
    /// The container the image is named in, when one sits between.
    pub through: Option<String>,
    /// What its revision turned out to name.
    pub provenance: Provenance,
}

/// What one thing in an ops plane runs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Running {
    /// The thing asked about, by key.
    pub thing: String,
    /// What kind of thing it is.
    pub kind: String,
    /// One row per image it runs, in key order.
    pub images: Vec<Runs>,
}

/// What one thing runs and what built each image — the forward direction.
///
/// `key` names a `Workload`, a `Container`, an `Infra` resource or an `Image`
/// itself. `None` means the plane holds no such key, which a surface says
/// rather than reporting that the thing runs nothing.
///
/// The walk is structural, not a list of labels: an image is whatever a
/// `USES_IMAGE` edge reaches, directly or across one `RUNS` hop. A Kubernetes
/// workload reaches its images through containers and a Terraform resource
/// names one directly, and neither case is special-cased here.
pub fn running(
    ops: &PlaneHandle<'_>,
    history: Option<&PlaneHandle<'_>>,
    key: &str,
) -> Result<Option<Running>> {
    let Some(node) = ops.node_by_key(key)? else {
        return Ok(None);
    };
    let mut images = Vec::new();
    if node.labels.iter().any(|l| l == IMAGE) {
        images.push(runs_row(&node, None, history)?);
    } else {
        for image in step(ops, &node, Dir::Out, USES_IMAGE)? {
            images.push(runs_row(&image, None, history)?);
        }
        for mid in step(ops, &node, Dir::Out, RUNS)? {
            for image in step(ops, &mid, Dir::Out, USES_IMAGE)? {
                images.push(runs_row(&image, Some(key_of(&mid)), history)?);
            }
        }
    }
    images.sort_by(|a, b| (&a.reference, &a.through).cmp(&(&b.reference, &b.through)));
    images.dedup();
    Ok(Some(Running {
        thing: key_of(&node),
        kind: kind_of(&node),
        images,
    }))
}

/// Every image the plane holds, or only those built from one commit — the
/// reverse direction and the plane-wide answer, which are one scan.
///
/// `sha` is a full object name. The match is a prefix test rather than an
/// equality, because a tag carries an abbreviation and the history plane holds
/// the whole thing.
///
/// A scan, deliberately: an ops plane holds hundreds of images rather than
/// millions, so an index over them would be built for no reason.
pub fn images(
    ops: &PlaneHandle<'_>,
    history: Option<&PlaneHandle<'_>>,
    sha: Option<&str>,
) -> Result<Vec<ImageRow>> {
    let wanted = sha.map(str::to_ascii_lowercase);
    let mut rows = Vec::new();
    for image in ops.query().scan_label(IMAGE).nodes()? {
        if let Some(sha) = &wanted {
            let revision = prop_str(&image.properties, "revision").map(str::to_ascii_lowercase);
            if !revision.is_some_and(|r| sha.starts_with(&r)) {
                continue;
            }
        }
        rows.push(ImageRow {
            reference: key_of(&image),
            provenance: provenance(&image, history)?,
            used_by: users_of(ops, &image)?,
        });
    }
    rows.sort_by(|a, b| a.reference.cmp(&b.reference));
    Ok(rows)
}

/// Everything that runs one image, through a container or directly.
fn users_of(ops: &PlaneHandle<'_>, image: &NodeRecord) -> Result<Vec<Use>> {
    let mut out = Vec::new();
    for user in step(ops, image, Dir::In, USES_IMAGE)? {
        let owners = step(ops, &user, Dir::In, RUNS)?;
        if owners.is_empty() {
            out.push(use_row(ops, &user, None)?);
        } else {
            for owner in owners {
                out.push(use_row(ops, &owner, Some(key_of(&user)))?);
            }
        }
    }
    out.sort_by(|a, b| (&a.thing, &a.through).cmp(&(&b.thing, &b.through)));
    out.dedup();
    Ok(out)
}

/// One `Use`, with the namespace the thing sits in when the plane records one.
fn use_row(ops: &PlaneHandle<'_>, thing: &NodeRecord, through: Option<String>) -> Result<Use> {
    Ok(Use {
        thing: key_of(thing),
        kind: kind_of(thing),
        namespace: step(ops, thing, Dir::Out, IN_NAMESPACE)?
            .first()
            .map(key_of),
        through,
    })
}

/// One image a thing runs, with its provenance resolved.
fn runs_row(
    image: &NodeRecord,
    through: Option<String>,
    history: Option<&PlaneHandle<'_>>,
) -> Result<Runs> {
    Ok(Runs {
        reference: key_of(image),
        through,
        provenance: provenance(image, history)?,
    })
}

/// One hop along `ty`, as node records.
fn step(ops: &PlaneHandle<'_>, from: &NodeRecord, dir: Dir, ty: &str) -> Result<Vec<NodeRecord>> {
    let mut out = Vec::new();
    for nb in ops.neighbors(from.id, dir, Some(ty))? {
        if let Some(node) = ops.node(nb.node)? {
            out.push(node);
        }
    }
    Ok(out)
}

/// A node's key, which every ops reader mints it with.
fn key_of(node: &NodeRecord) -> String {
    node.external_key
        .clone()
        .unwrap_or_else(|| "(no key)".to_string())
}

/// What a node is: the `kind` an ops reader recorded — `Deployment`,
/// `CronJob` — else its label, which is all a reader that wrote no `kind`
/// said.
fn kind_of(node: &NodeRecord) -> String {
    prop_str(&node.properties, "kind")
        .map(str::to_string)
        .or_else(|| node.labels.first().cloned())
        .unwrap_or_default()
}

/// Which planes a `deployed` answer should read, as a caller named them.
#[derive(Debug, Clone, Copy)]
pub struct Asked<'a> {
    /// The code plane, or the ops plane beside it.
    pub plane: &'a str,
    /// The ops plane, when the convention does not name it.
    pub ops: Option<&'a str>,
    /// The history plane, when the convention does not name it.
    pub history: Option<&'a str>,
}

/// The ops plane and the history plane [`Asked`] names.
///
/// Here rather than in each surface, because the MCP verb and the CLI arm
/// resolving a pair of planes differently would be the one failure neither
/// could see.
///
/// `myrepo` and `myrepo_ops` both name the same repository: the suffix is
/// stripped and both siblings derived from what is left. A plane with no
/// `_ops` sibling is read as the ops plane itself — a digest of a manifest
/// tree names its plane after the directory, so that is where such a tree's
/// images are. Missing history is `None` rather than an error, because an
/// image's own answer is worth having without it.
pub fn planes<'db>(
    db: &'db Database,
    asked: &Asked<'_>,
) -> Result<(PlaneHandle<'db>, Option<PlaneHandle<'db>>)> {
    let code = asked
        .plane
        .strip_suffix(crate::compact::OPS_SUFFIX)
        .unwrap_or(asked.plane);
    let ops = match asked.ops {
        Some(named) => db.plane(named)?,
        None => match db.plane(&ops_plane_name(code)) {
            Ok(plane) => plane,
            Err(_) => db.plane(asked.plane)?,
        },
    };
    let history = match asked.history {
        Some(named) => Some(db.plane(named)?),
        None => db.plane(&history_plane_name(code)).ok(),
    };
    Ok((ops, history))
}

/// What built a running thing, or what runs a commit — `deployed`.
///
/// `target` is a key in the ops plane, a revision, or `None` for the plane as
/// a whole. One verb in whichever direction the argument points, the shape
/// [`crate::compact::trace`] uses when it tries the reverse direction and
/// says so: a key is tried first, then a revision, and an argument that is
/// neither is told it is neither.
///
/// Every answer ends by naming the planes it read, because a verb that joined
/// the wrong pair of planes would otherwise look like one that found nothing.
pub fn deployed(
    ops: &PlaneHandle<'_>,
    history: Option<&PlaneHandle<'_>>,
    target: Option<&str>,
) -> Result<String> {
    let mut out = match target {
        None => plane_wide(ops, history)?,
        Some(target) => match running(ops, history, target)? {
            Some(run) => forward(&run),
            None => reverse(ops, history, target)?,
        },
    };
    out.push_str(&planes_read(ops, history)?);
    Ok(out)
}

/// What one thing runs, each image with what built it.
fn forward(run: &Running) -> String {
    let mut out = format!("{} {}\n", run.kind, run.thing);
    if run.images.is_empty() {
        out.push_str("  nothing this plane records says it runs an image\n");
    }
    for image in &run.images {
        let named = match &image.through {
            Some(through) => format!("{} ({})", image.reference, under(through, &run.thing)),
            None => image.reference.clone(),
        };
        out.push_str(&format!("  {}\n", joined(&named, &image.provenance)));
    }
    out
}

/// What runs the commit a revision names.
fn reverse(
    ops: &PlaneHandle<'_>,
    history: Option<&PlaneHandle<'_>>,
    target: &str,
) -> Result<String> {
    let Some(history) = history else {
        return Ok(format!(
            "`{target}` is no key in this ops plane, and with no history plane a revision \
             cannot be resolved\n"
        ));
    };
    let commit = match resolve_rev(history, None, target)? {
        RevAnswer::One(commit) => commit,
        RevAnswer::Many(commits) => return Ok(crate::rev::ambiguous(target, &commits)),
        RevAnswer::None(why) => {
            return Ok(format!(
                "`{target}` is neither a key in this ops plane nor a revision in its history: \
                 {why}\n"
            ));
        }
    };
    let mut out = format!("{}\n", commit_line(&commit));
    let rows = images(ops, Some(history), Some(commit.sha.as_str()))?;
    if rows.is_empty() {
        out.push_str("  nothing in this plane runs an image built from it\n");
    }
    for row in &rows {
        for user in &row.used_by {
            let through = user
                .through
                .as_ref()
                .map(|t| format!(" ({})", under(t, &user.thing)))
                .unwrap_or_default();
            out.push_str(&format!("  {}  {}{through}\n", user.thing, row.reference));
        }
        if row.used_by.is_empty() {
            out.push_str(&format!(
                "  {}  (no workload in this plane runs it)\n",
                row.reference
            ));
        }
    }
    Ok(out)
}

/// Every image the plane holds, with what built it and what runs it.
fn plane_wide(ops: &PlaneHandle<'_>, history: Option<&PlaneHandle<'_>>) -> Result<String> {
    let rows = images(ops, history, None)?;
    if rows.is_empty() {
        return Ok(
            "this plane holds no images — digesting a manifest tree is what records \
                   them\n"
                .to_string(),
        );
    }
    let commits = rows
        .iter()
        .filter(|r| r.provenance.commit().is_some())
        .count();
    let mut out = format!(
        "{} image(s), {commits} of them joined to a commit of this history\n",
        rows.len()
    );
    for row in rows.iter().take(IMAGES_SHOWN) {
        out.push_str(&format!("  {}\n", joined(&row.reference, &row.provenance)));
        out.push_str(&format!("    {}\n", run_by(&row.used_by)));
    }
    if rows.len() > IMAGES_SHOWN {
        out.push_str(&format!("  … and {} more\n", rows.len() - IMAGES_SHOWN));
    }
    Ok(out)
}

/// One image's line: an arrow when the join held, a dash when it did not, so
/// a reader can see at a glance which is which.
fn joined(named: &str, provenance: &Provenance) -> String {
    let mark = if provenance.commit().is_some() {
        '→'
    } else {
        '—'
    };
    format!("{named}  {mark} {}", provenance.stated())
}

/// The things that run an image, as one line.
fn run_by(used_by: &[Use]) -> String {
    if used_by.is_empty() {
        return "run by nothing this plane records".to_string();
    }
    let mut out = format!(
        "run by {}",
        used_by
            .iter()
            .take(RUNNERS_SHOWN)
            .map(|u| u.thing.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    );
    if used_by.len() > RUNNERS_SHOWN {
        out.push_str(&format!(" … and {} more", used_by.len() - RUNNERS_SHOWN));
    }
    out
}

/// A key shown relative to the thing it belongs to: the container `main`
/// rather than `prod/Deployment/api/main`.
fn under(key: &str, owner: &str) -> String {
    key.strip_prefix(&format!("{owner}/"))
        .unwrap_or(key)
        .to_string()
}

/// The planes an answer read, named so a wrong pairing is visible.
fn planes_read(ops: &PlaneHandle<'_>, history: Option<&PlaneHandle<'_>>) -> Result<String> {
    let ops = ops.name()?;
    Ok(match history {
        Some(history) => format!(
            "note: read the ops plane `{ops}` against the history plane `{}`\n",
            history.name()?
        ),
        None => format!(
            "note: read the ops plane `{ops}` alone — with no history plane, no revision \
             resolves to a commit\n"
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::{Database, WriteTxn};
    use crate::types::{NodeId, PropDesc, PropValue, Properties};

    /// Two commits sharing the seven-character prefix `9f2c1b7`, and one that
    /// shares nothing — ambiguity is the case worth a fixture.
    const NEWER: &str = "9f2c1b7d8e5a1122334455667788990011223344";
    const OLDER: &str = "9f2c1b7aaaaa5566778899001122334455667788";
    const SOLO: &str = "abc1234ef5670099887766554433221100998877";
    const DIGEST: &str = "sha256:5f3e2d1c0b9a8877665544332211009988776655443322110099887766554433";

    fn props(pairs: &[(&str, &str)]) -> Properties {
        let mut p = Properties::new();
        for (k, v) in pairs {
            p.insert(
                (*k).to_string(),
                PropDesc::new(PropValue::Str((*v).to_string())),
            );
        }
        p
    }

    /// A history plane holding the three commits, and an ops plane holding one
    /// `Image` node per row of `arch/10-ops.md` §5 — plus the topology that
    /// reaches them: two Kubernetes workloads in two namespaces whose
    /// containers run the same image, one that runs a third-party image, and a
    /// Terraform resource that names an image with no container between.
    fn fixture() -> Database {
        let db = Database::in_memory().unwrap();
        commits(&db);
        topology(&db);
        db
    }

    /// Three commits, two of them sharing a seven-character prefix.
    fn commits(db: &Database) {
        let history = db.create_plane("acme_git", Properties::new()).unwrap();
        let mut txn = history.write().unwrap();
        for (sha, ts, summary) in [
            (NEWER, 3000, "widen the retry window"),
            (OLDER, 2000, "an older commit that shares a prefix"),
            (SOLO, 1000, "the one with its own prefix"),
        ] {
            let mut node = props(&[
                ("sha", sha),
                ("summary", summary),
                ("author_name", "Ada"),
                ("committed_at", "2026-10-12T10:00:00+00:00"),
            ]);
            node.insert(
                "committed_ts".to_string(),
                PropDesc::new(PropValue::Int(ts)),
            );
            txn.create_node_with_key(&format!("commit:{sha}"), &["Commit"], node)
                .unwrap();
        }
        txn.commit().unwrap();
    }

    /// The ops plane: one `Image` per row of §5, and what reaches them.
    fn topology(db: &Database) {
        let ops = db.create_plane("acme_ops", Properties::new()).unwrap();
        let mut txn = ops.write().unwrap();
        let mut node = std::collections::HashMap::new();
        for (reference, pairs) in [
            (
                "ghcr.io/acme/api:sha-abc1234",
                vec![("tag", "sha-abc1234"), ("revision", "abc1234")],
            ),
            (
                "ghcr.io/acme/api:sha-9f2c1b7",
                vec![("tag", "sha-9f2c1b7"), ("revision", "9f2c1b7")],
            ),
            (
                "ghcr.io/acme/api:sha-deadbee",
                vec![("tag", "sha-deadbee"), ("revision", "deadbee")],
            ),
            ("ghcr.io/acme/api:v2.1.0", vec![("tag", "v2.1.0")]),
            (
                "ghcr.io/acme/api:latest",
                vec![("tag", "latest"), ("digest", DIGEST)],
            ),
            ("ghcr.io/acme/api", vec![]),
            ("postgres:17", vec![("tag", "17")]),
        ] {
            let mut pairs = pairs;
            pairs.push(("reference", reference));
            let id = txn
                .create_node_with_key(reference, &["Image"], props(&pairs))
                .unwrap();
            node.insert(reference.to_string(), id);
        }
        workloads(&mut txn, &node);
        txn.commit().unwrap();
    }

    /// Two Kubernetes workloads running the same image in two namespaces, one
    /// running a third-party image, and a Terraform resource that names an
    /// image with no container between.
    fn workloads(txn: &mut WriteTxn<'_>, node: &std::collections::HashMap<String, NodeId>) {
        let mut node = node.clone();
        for ns in ["prod", "staging"] {
            let id = txn
                .create_node_with_key(ns, &["Namespace"], props(&[("name", ns)]))
                .unwrap();
            node.insert(ns.to_string(), id);
        }
        for (workload, kind, ns, image) in [
            (
                "prod/Deployment/api",
                "Deployment",
                "prod",
                "ghcr.io/acme/api:sha-abc1234",
            ),
            (
                "staging/Deployment/api",
                "Deployment",
                "staging",
                "ghcr.io/acme/api:sha-abc1234",
            ),
            (
                "prod/StatefulSet/postgres",
                "StatefulSet",
                "prod",
                "postgres:17",
            ),
        ] {
            let id = txn
                .create_node_with_key(workload, &["Workload"], props(&[("kind", kind)]))
                .unwrap();
            let container = format!("{workload}/main");
            let cid = txn
                .create_node_with_key(&container, &["Container"], props(&[("role", "container")]))
                .unwrap();
            txn.create_edge(id, cid, "RUNS", Properties::new()).unwrap();
            txn.create_edge(cid, node[image], "USES_IMAGE", Properties::new())
                .unwrap();
            txn.create_edge(id, node[ns], "IN_NAMESPACE", Properties::new())
                .unwrap();
        }

        // Terraform's shape: a resource names an image with no container.
        let task = txn
            .create_node_with_key(
                "aws_ecs_task_definition.api",
                &["Infra"],
                props(&[("type", "aws_ecs_task_definition")]),
            )
            .unwrap();
        txn.create_edge(
            task,
            node["ghcr.io/acme/api:sha-9f2c1b7"],
            "USES_IMAGE",
            Properties::new(),
        )
        .unwrap();
    }

    fn what_runs(db: &Database, key: &str) -> Option<Running> {
        let ops = db.plane("acme_ops").unwrap();
        let history = db.plane("acme_git").unwrap();
        running(&ops, Some(&history), key).unwrap()
    }

    fn rows(db: &Database, sha: Option<&str>) -> Vec<ImageRow> {
        let ops = db.plane("acme_ops").unwrap();
        let history = db.plane("acme_git").unwrap();
        images(&ops, Some(&history), sha).unwrap()
    }

    #[test]
    fn a_workload_reaches_its_image_through_its_container() {
        let db = fixture();
        let run = what_runs(&db, "prod/Deployment/api").expect("a workload");
        assert_eq!(run.kind, "Deployment");
        assert_eq!(run.images.len(), 1);
        let image = &run.images[0];
        assert_eq!(image.reference, "ghcr.io/acme/api:sha-abc1234");
        assert_eq!(image.through.as_deref(), Some("prod/Deployment/api/main"));
        assert_eq!(image.provenance.commit().unwrap().sha.as_str(), SOLO);
    }

    /// Terraform names an image on the resource itself, so the same walk has
    /// to reach it without a container in the way.
    #[test]
    fn an_infrastructure_resource_names_its_image_directly() {
        let db = fixture();
        let run = what_runs(&db, "aws_ecs_task_definition.api").expect("a resource");
        assert_eq!(run.kind, "Infra");
        assert_eq!(run.images.len(), 1);
        assert_eq!(run.images[0].through, None);
    }

    #[test]
    fn an_image_asked_about_by_itself_answers_for_itself() {
        let db = fixture();
        let run = what_runs(&db, "ghcr.io/acme/api:v2.1.0").expect("an image");
        assert_eq!(run.images.len(), 1);
        assert_eq!(
            run.images[0].provenance,
            Provenance::Floating {
                tag: "v2.1.0".to_string()
            }
        );
    }

    #[test]
    fn a_key_the_plane_does_not_hold_is_no_answer_rather_than_an_empty_one() {
        let db = fixture();
        assert!(what_runs(&db, "prod/Deployment/nope").is_none());
    }

    #[test]
    fn a_commit_lists_every_namespace_running_it() {
        let db = fixture();
        let found = rows(&db, Some(SOLO));
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].reference, "ghcr.io/acme/api:sha-abc1234");
        assert_eq!(
            found[0]
                .used_by
                .iter()
                .map(|u| (u.thing.as_str(), u.namespace.as_deref(), u.kind.as_str()))
                .collect::<Vec<_>>(),
            [
                ("prod/Deployment/api", Some("prod"), "Deployment"),
                ("staging/Deployment/api", Some("staging"), "Deployment"),
            ]
        );
        assert_eq!(
            found[0].used_by[0].through.as_deref(),
            Some("prod/Deployment/api/main")
        );
    }

    /// The tag carries seven characters and the history plane holds forty, so
    /// the reverse match is a prefix test. An equality would find nothing.
    #[test]
    fn a_full_sha_matches_the_abbreviation_a_tag_carried() {
        let db = fixture();
        let found = rows(&db, Some(NEWER));
        assert_eq!(
            found
                .iter()
                .map(|r| r.reference.as_str())
                .collect::<Vec<_>>(),
            ["ghcr.io/acme/api:sha-9f2c1b7"]
        );
        assert_eq!(found[0].used_by[0].thing, "aws_ecs_task_definition.api");
    }

    #[test]
    fn a_commit_no_image_was_built_from_matches_nothing() {
        let db = fixture();
        let absent = "ffffffffffffffffffffffffffffffffffffffff";
        assert!(rows(&db, Some(absent)).is_empty());
    }

    /// A prefix match cuts both ways: seven characters that fit two commits
    /// match the image from *either* of them. The row says so — its
    /// provenance is the candidate list — rather than reporting that this
    /// particular commit is what runs.
    #[test]
    fn an_ambiguous_abbreviation_matches_either_commit_and_says_which() {
        let db = fixture();
        for sha in [NEWER, OLDER] {
            let found = rows(&db, Some(sha));
            assert_eq!(
                found
                    .iter()
                    .map(|r| r.reference.as_str())
                    .collect::<Vec<_>>(),
                ["ghcr.io/acme/api:sha-9f2c1b7"],
                "{sha}"
            );
            let Provenance::Candidates(cs) = &found[0].provenance else {
                panic!("{:?}", found[0].provenance);
            };
            assert_eq!(cs.len(), 2);
        }
    }

    #[test]
    fn the_plane_wide_answer_holds_every_image_used_or_not() {
        let db = fixture();
        let found = rows(&db, None);
        assert_eq!(found.len(), 7);
        let orphans = found
            .iter()
            .filter(|r| r.used_by.is_empty())
            .map(|r| r.reference.as_str())
            .collect::<Vec<_>>();
        assert_eq!(
            orphans,
            [
                "ghcr.io/acme/api",
                "ghcr.io/acme/api:latest",
                "ghcr.io/acme/api:sha-deadbee",
                "ghcr.io/acme/api:v2.1.0",
            ]
        );
    }

    fn asked(db: &Database, plane: &str) -> String {
        let (ops, history) = planes(
            db,
            &Asked {
                plane,
                ops: None,
                history: None,
            },
        )
        .unwrap();
        deployed(&ops, history.as_ref(), None).unwrap()
    }

    #[test]
    fn the_code_plane_and_the_ops_plane_name_the_same_pair() {
        let db = fixture();
        for named in ["acme", "acme_ops"] {
            assert!(
                asked(&db, named)
                    .contains("read the ops plane `acme_ops` against the history plane `acme_git`"),
                "{named}"
            );
        }
    }

    /// A digest of a manifest tree names its plane after the directory, so
    /// until one routes ops facts to a sibling, the plane asked for holds them.
    #[test]
    fn a_plane_with_no_ops_sibling_is_read_as_the_ops_plane_itself() {
        let db = Database::in_memory().unwrap();
        let tree = db.create_plane("k8srepo", Properties::new()).unwrap();
        let mut txn = tree.write().unwrap();
        txn.create_node_with_key("postgres:17", &["Image"], props(&[("tag", "17")]))
            .unwrap();
        txn.commit().unwrap();

        let out = asked(&db, "k8srepo");
        assert!(out.starts_with("1 image(s)"), "{out}");
        assert!(out.contains("read the ops plane `k8srepo` alone"), "{out}");
    }

    #[test]
    fn an_explicit_pairing_overrides_the_convention() {
        let db = fixture();
        let (ops, history) = planes(
            &db,
            &Asked {
                plane: "acme",
                ops: Some("acme_ops"),
                history: None,
            },
        )
        .unwrap();
        assert_eq!(ops.name().unwrap(), "acme_ops");
        assert_eq!(history.unwrap().name().unwrap(), "acme_git");
    }

    fn answer(db: &Database, target: Option<&str>, with_history: bool) -> String {
        let ops = db.plane("acme_ops").unwrap();
        let history = db.plane("acme_git").unwrap();
        deployed(&ops, with_history.then_some(&history), target).unwrap()
    }

    #[test]
    fn a_workload_answers_with_the_commit_its_image_was_built_from() {
        let db = fixture();
        let out = answer(&db, Some("prod/Deployment/api"), true);
        assert!(out.starts_with("Deployment prod/Deployment/api\n"), "{out}");
        assert!(
            out.contains("ghcr.io/acme/api:sha-abc1234 (main)  → abc1234ef567"),
            "{out}"
        );
        assert!(out.contains("the one with its own prefix"), "{out}");
    }

    #[test]
    fn a_revision_answers_with_everything_running_it() {
        let db = fixture();
        let out = answer(&db, Some("abc1234"), true);
        assert!(out.starts_with("abc1234ef567  2026-10-12"), "{out}");
        assert!(
            out.contains("prod/Deployment/api  ghcr.io/acme/api:sha-abc1234 (main)"),
            "{out}"
        );
        assert!(
            out.contains("staging/Deployment/api  ghcr.io/acme/api:sha-abc1234"),
            "{out}"
        );
    }

    /// The forward direction is tried first, so a key that could be read as
    /// hex is still a key.
    #[test]
    fn a_key_wins_over_a_revision_of_the_same_text() {
        let db = fixture();
        let out = answer(&db, Some("postgres:17"), true);
        assert!(out.starts_with("Image postgres:17\n"), "{out}");
    }

    #[test]
    fn an_ambiguous_revision_lists_candidates_rather_than_picking_one() {
        let db = fixture();
        let out = answer(&db, Some("9f2c1b7"), true);
        assert!(out.contains("is ambiguous"), "{out}");
        assert!(out.contains("call again with more of the sha"), "{out}");
    }

    #[test]
    fn an_argument_that_is_neither_a_key_nor_a_revision_is_told_so() {
        let db = fixture();
        let out = answer(&db, Some("prod/Deployment/nope"), true);
        assert!(
            out.contains("neither a key in this ops plane nor a revision"),
            "{out}"
        );
    }

    #[test]
    fn the_plane_wide_answer_counts_what_joined() {
        let db = fixture();
        let out = answer(&db, None, true);
        assert!(
            out.starts_with("7 image(s), 1 of them joined to a commit of this history\n"),
            "{out}"
        );
        assert!(
            out.contains("run by prod/Deployment/api, staging/Deployment/api"),
            "{out}"
        );
        assert!(out.contains("run by nothing this plane records"), "{out}");
    }

    #[test]
    fn every_answer_names_the_planes_it_read() {
        let db = fixture();
        assert!(
            answer(&db, None, true).contains(
                "note: read the ops plane `acme_ops` against the history plane `acme_git`"
            ),
        );
        let alone = answer(&db, Some("abc1234"), false);
        assert!(
            alone.contains("read the ops plane `acme_ops` alone"),
            "{alone}"
        );
        assert!(
            alone.contains("with no history plane a revision cannot be resolved"),
            "{alone}"
        );
    }

    fn of(db: &Database, reference: &str, with_history: bool) -> Provenance {
        let ops = db.plane("acme_ops").unwrap();
        let image = ops.node_by_key(reference).unwrap().unwrap();
        let history = db.plane("acme_git").unwrap();
        provenance(&image, with_history.then_some(&history)).unwrap()
    }

    #[test]
    fn a_revision_tagged_image_names_the_commit_that_built_it() {
        let db = fixture();
        let p = of(&db, "ghcr.io/acme/api:sha-abc1234", true);
        let commit = p.commit().expect("one commit");
        assert_eq!(commit.sha.as_str(), SOLO);
        assert!(p.stated().contains("abc1234ef567"));
        assert!(p.stated().contains("the one with its own prefix"));
    }

    #[test]
    fn an_abbreviated_revision_that_fits_two_commits_lists_both_newest_first() {
        let db = fixture();
        let p = of(&db, "ghcr.io/acme/api:sha-9f2c1b7", true);
        let Provenance::Candidates(cs) = &p else {
            panic!("{p:?}");
        };
        assert_eq!(
            cs.iter().map(|c| c.sha.as_str()).collect::<Vec<_>>(),
            [NEWER, OLDER]
        );
        assert!(p.stated().contains("2 commits fit"));
    }

    #[test]
    fn a_revision_this_history_does_not_hold_reads_as_built_elsewhere() {
        let db = fixture();
        let p = of(&db, "ghcr.io/acme/api:sha-deadbee", true);
        assert_eq!(
            p,
            Provenance::Elsewhere {
                revision: "deadbee".to_string()
            }
        );
        assert!(p.stated().contains("another repository"));
    }

    #[test]
    fn a_floating_tag_says_the_tag_names_no_revision() {
        let db = fixture();
        let p = of(&db, "ghcr.io/acme/api:v2.1.0", true);
        assert_eq!(
            p,
            Provenance::Floating {
                tag: "v2.1.0".to_string()
            }
        );
        assert!(p.stated().contains("names none"));
    }

    #[test]
    fn a_digest_outranks_the_tag_beside_it_and_asks_for_a_registry() {
        let db = fixture();
        let p = of(&db, "ghcr.io/acme/api:latest", true);
        assert_eq!(
            p,
            Provenance::Pinned {
                digest: DIGEST.to_string()
            }
        );
        let stated = p.stated();
        assert!(stated.contains("sha256:5f3e2d1c0b9a"), "{stated}");
        assert!(
            !stated.contains("887766"),
            "the digest is cut short: {stated}"
        );
        assert!(stated.contains("registry call"));
    }

    #[test]
    fn a_reference_with_neither_tag_nor_digest_names_no_revision() {
        let db = fixture();
        let p = of(&db, "ghcr.io/acme/api", true);
        assert_eq!(p, Provenance::Untagged);
        assert!(p.stated().contains("neither tag nor digest"));
    }

    #[test]
    fn with_no_history_plane_the_revision_is_reported_unresolved() {
        let db = fixture();
        let p = of(&db, "ghcr.io/acme/api:sha-abc1234", false);
        assert_eq!(
            p,
            Provenance::NoHistory {
                revision: "abc1234".to_string()
            }
        );
        assert!(p.stated().contains("no history plane"));
    }

    /// A third-party image is not detected as one, deliberately: nothing in a
    /// manifest distinguishes `postgres:17` from a locally built `myapp:17`,
    /// and the honest answer — its tag names no revision — is the same for
    /// both. The verb never claims an image was built elsewhere unless a
    /// revision said so.
    #[test]
    fn a_third_party_image_is_a_tag_that_names_no_revision_not_a_guess() {
        let db = fixture();
        let p = of(&db, "postgres:17", true);
        assert_eq!(
            p,
            Provenance::Floating {
                tag: "17".to_string()
            }
        );
    }

    #[test]
    fn a_commit_a_rewrite_left_behind_is_flagged_in_the_line() {
        let db = fixture();
        let history = db.plane("acme_git").unwrap();
        let mut txn = history.write().unwrap();
        let commit = history
            .node_by_key(&format!("commit:{SOLO}"))
            .unwrap()
            .unwrap();
        txn.set_prop(
            commit.id,
            "reachable",
            PropDesc::new(PropValue::Bool(false)),
        )
        .unwrap();
        txn.commit().unwrap();

        let p = of(&db, "ghcr.io/acme/api:sha-abc1234", true);
        assert!(
            p.stated().contains("left this commit behind"),
            "{}",
            p.stated()
        );
    }
}
