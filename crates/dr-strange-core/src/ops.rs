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

use crate::api::PlaneHandle;
use crate::compact::prop_str;
use crate::error::Result;
use crate::rev::{CommitRef, RevAnswer, resolve_rev};
use crate::types::NodeRecord;

/// Hex digits of a digest shown before it is cut short.
const DIGEST_SHOWN: usize = 12;

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
            Provenance::Commit(c) => {
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
            Provenance::Candidates(cs) => format!(
                "{} commits fit that revision, newest first: {}",
                cs.len(),
                cs.iter()
                    .map(|c| c.sha.short())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            Provenance::Elsewhere { revision } => format!(
                "revision `{revision}` is in no commit of this history — the usual cause is an \
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

/// A digest cut down the way a registry client prints one: the algorithm and
/// the first few hex digits.
fn short_digest(digest: &str) -> String {
    match digest.split_once(':') {
        Some((algo, hex)) => format!("{algo}:{}", &hex[..hex.len().min(DIGEST_SHOWN)]),
        None => digest.chars().take(DIGEST_SHOWN).collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::Database;
    use crate::types::{PropDesc, PropValue, Properties};

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
    /// `Image` node per row of `arch/10-ops.md` §5.
    fn planes() -> Database {
        let db = Database::in_memory().unwrap();

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

        let ops = db.create_plane("acme_ops", Properties::new()).unwrap();
        let mut txn = ops.write().unwrap();
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
            txn.create_node_with_key(reference, &["Image"], props(&pairs))
                .unwrap();
        }
        txn.commit().unwrap();

        db
    }

    fn of(db: &Database, reference: &str, with_history: bool) -> Provenance {
        let ops = db.plane("acme_ops").unwrap();
        let image = ops.node_by_key(reference).unwrap().unwrap();
        let history = db.plane("acme_git").unwrap();
        provenance(&image, with_history.then_some(&history)).unwrap()
    }

    #[test]
    fn a_revision_tagged_image_names_the_commit_that_built_it() {
        let db = planes();
        let p = of(&db, "ghcr.io/acme/api:sha-abc1234", true);
        let commit = p.commit().expect("one commit");
        assert_eq!(commit.sha.as_str(), SOLO);
        assert!(p.stated().contains("abc1234ef567"));
        assert!(p.stated().contains("the one with its own prefix"));
    }

    #[test]
    fn an_abbreviated_revision_that_fits_two_commits_lists_both_newest_first() {
        let db = planes();
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
        let db = planes();
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
        let db = planes();
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
        let db = planes();
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
        let db = planes();
        let p = of(&db, "ghcr.io/acme/api", true);
        assert_eq!(p, Provenance::Untagged);
        assert!(p.stated().contains("neither tag nor digest"));
    }

    #[test]
    fn with_no_history_plane_the_revision_is_reported_unresolved() {
        let db = planes();
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
        let db = planes();
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
        let db = planes();
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
