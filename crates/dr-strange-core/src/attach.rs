//! Attachments — the planes a plane may ask (issues #42, #43).
//!
//! A frontend and the service it calls, or a shared library and the repository
//! that depends on it, are separate graphs. An attachment is the one fact that
//! joins them: *this plane may ask that one*. It carries no vocabulary of its
//! own — what the question is, and what key the two sides match on, belongs to
//! the verb asking it.
//!
//! The record lives in the plane's own property map (arch/09 §2), so it is
//! durable, readable by anything that reads a plane, and holds nothing secret:
//! an attached graph is part of the same environment, and the caller presents
//! the token it already holds.

use crate::api::{Database, PlaneHandle};
use crate::compute::LogicalPlan;
use crate::error::{Error, Result};
use crate::types::{NodeRecord, PropDesc, PropValue, Properties};

/// The plane property an attachment list is recorded under.
pub const ATTACHMENTS: &str = "attachments";

/// What the property means, for anything that reads a plane's own map.
const WHAT_IT_IS: &str = "the planes this one may ask — a name, the plane it \
                          names there, and a URL when it is served elsewhere";

/// A reader for planes on other drsg instances.
///
/// `Send + Sync`: one reader is shared by every thread a server answers on.
///
/// Core opens no sockets — a storage engine has no business doing so — so the
/// surface that can supplies this. A verb hands it one attachment and the plan
/// to run there, and folds the nodes it gets back; an `Err` is reported as an
/// attachment that could not be read, never swallowed.
pub trait Remote: Send + Sync {
    /// The nodes `plan` selects from `at`'s plane, or why not.
    ///
    /// The `Err` is shown to a reader, so it names the failure and never
    /// carries the remote's reply body.
    fn nodes(
        &self,
        at: &Attachment,
        plan: &LogicalPlan,
    ) -> std::result::Result<Vec<NodeRecord>, String>;
}

/// One plane this plane may ask a question of.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Attachment {
    /// The name this attachment is known by here.
    pub name: String,
    /// What the plane is called on the far side.
    pub plane: String,
    /// Where the far side is served; `None` when the plane is on this instance.
    pub url: Option<String>,
}

impl Attachment {
    /// An attachment named `name`, on `plane` there — the same name when that
    /// is unstated, since a plane is named after the directory it came from.
    pub fn new(name: impl Into<String>, plane: Option<String>, url: Option<String>) -> Self {
        let name = name.into();
        Self {
            plane: plane.unwrap_or_else(|| name.clone()),
            name,
            url,
        }
    }

    /// Whether this attachment names a plane on this instance.
    pub fn is_local(&self) -> bool {
        self.url.is_none()
    }

    fn to_prop(&self) -> PropValue {
        let mut m = Properties::new();
        m.insert(
            "name".into(),
            PropDesc::new(PropValue::Str(self.name.clone())),
        );
        m.insert(
            "plane".into(),
            PropDesc::new(PropValue::Str(self.plane.clone())),
        );
        if let Some(url) = &self.url {
            m.insert("url".into(), PropDesc::new(PropValue::Str(url.clone())));
        }
        PropValue::Map(m)
    }

    fn from_prop(v: &PropValue) -> Option<Self> {
        let PropValue::Map(m) = v else { return None };
        let text = |k: &str| match m.get(k).map(|p| &p.value) {
            Some(PropValue::Str(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        };
        let name = text("name")?;
        Some(Self::new(name, text("plane"), text("url")))
    }
}

/// The attachments recorded on one plane, in the order they were made.
#[derive(Debug, Clone, Default)]
pub struct Attachments {
    recorded: Vec<Attachment>,
    unreadable: usize,
}

impl Attachments {
    /// What `plane` records.
    pub fn of(plane: &PlaneHandle<'_>) -> Result<Self> {
        Ok(Self::read(&plane.properties()?))
    }

    fn read(props: &Properties) -> Self {
        let Some(PropDesc {
            value: PropValue::List(items),
            ..
        }) = props.get(ATTACHMENTS)
        else {
            return Self::default();
        };
        let mut out = Self::default();
        for item in items {
            match Attachment::from_prop(item) {
                Some(a) => out.recorded.push(a),
                None => out.unreadable += 1,
            }
        }
        out
    }

    /// Every attachment, in the order recorded.
    pub fn list(&self) -> &[Attachment] {
        &self.recorded
    }

    /// The attachment known by this name.
    pub fn get(&self, name: &str) -> Option<&Attachment> {
        self.recorded.iter().find(|a| a.name == name)
    }

    /// Entries under the property that are not attachment records — something
    /// other than `attach` wrote them, and a verb says so rather than dropping
    /// them in silence.
    pub fn unreadable(&self) -> usize {
        self.unreadable
    }

    fn write(&self, props: &mut Properties) {
        let items = self.recorded.iter().map(Attachment::to_prop).collect();
        props.insert(
            ATTACHMENTS.into(),
            PropDesc::described(WHAT_IT_IS, PropValue::List(items)),
        );
    }

    /// Record `a` on `plane`, replacing and returning any attachment of the
    /// same name. Read and write happen under one write lock, so two attaches
    /// cannot lose each other.
    pub fn attach(plane: &PlaneHandle<'_>, a: Attachment) -> Result<Option<Attachment>> {
        let mut replaced = None;
        plane.update_properties(|props| {
            let mut have = Self::read(props);
            match have.recorded.iter_mut().find(|x| x.name == a.name) {
                Some(slot) => replaced = Some(std::mem::replace(slot, a)),
                None => have.recorded.push(a),
            }
            have.write(props);
        })?;
        Ok(replaced)
    }

    /// Forget the attachment named `name`, returning what was there.
    pub fn detach(plane: &PlaneHandle<'_>, name: &str) -> Result<Option<Attachment>> {
        let mut removed = None;
        plane.update_properties(|props| {
            let mut have = Self::read(props);
            if let Some(at) = have.recorded.iter().position(|a| a.name == name) {
                removed = Some(have.recorded.remove(at));
                have.write(props);
            }
        })?;
        Ok(removed)
    }
}

/// Record an attachment on `plane`, replacing one of the same name.
///
/// A local attachment is verified: the plane it names must be on this
/// instance, and the error names the ones that are. A URL is recorded as
/// given — nothing is contacted until a verb asks it something.
pub fn attach(db: &Database, plane: &str, a: Attachment) -> Result<String> {
    if a.name.trim().is_empty() {
        return Err(Error::InvalidArgument("an attachment needs a name".into()));
    }
    if let Some(url) = a.url.as_deref().filter(|u| !is_http_url(u)) {
        return Err(Error::InvalidArgument(format!(
            "'{url}' is not a drsg URL — it reads `http://host:port`"
        )));
    }
    let here = planes(db)?;
    if a.is_local() {
        if a.plane == plane {
            return Err(Error::InvalidArgument(format!(
                "plane '{plane}' cannot attach itself"
            )));
        }
        if !here.iter().any(|p| p == &a.plane) {
            return Err(Error::NotFound(format!(
                "plane '{}' on this instance — it holds: {}",
                a.plane,
                here.join(", ")
            )));
        }
    }
    let handle = db.plane(plane)?;
    let replaced = Attachments::attach(&handle, a.clone())?;
    let mut out = format!("attached on plane '{plane}'\n");
    out.push_str(&row(&a, a.name.chars().count(), &here));
    if let Some(was) = replaced {
        out.push_str("  replaced\n");
        out.push_str(&row(&was, was.name.chars().count(), &here));
    }
    Ok(out)
}

/// Forget the attachment named `name` on `plane`.
///
/// An absent name is reported, not an error: it leaves the plane in the state
/// the caller asked for.
pub fn detach(db: &Database, plane: &str, name: &str) -> Result<String> {
    let handle = db.plane(plane)?;
    match Attachments::detach(&handle, name)? {
        Some(gone) => Ok(format!(
            "detached from plane '{plane}'\n{}",
            row(&gone, gone.name.chars().count(), &planes(db)?)
        )),
        None => {
            let have = Attachments::of(&handle)?;
            let names: Vec<&str> = have.list().iter().map(|a| a.name.as_str()).collect();
            Ok(match names.is_empty() {
                true => format!("plane '{plane}' has nothing attached\n"),
                false => format!(
                    "plane '{plane}' has no attachment named '{name}' — attached: {}\n",
                    names.join(", ")
                ),
            })
        }
    }
}

/// What `plane` attaches, and whether each is there to be asked.
pub fn attachments(db: &Database, plane: &str) -> Result<String> {
    let handle = db.plane(plane)?;
    let have = Attachments::of(&handle)?;
    if have.list().is_empty() && have.unreadable() == 0 {
        return Ok(format!(
            "plane '{plane}' attaches nothing — `attach <plane>` records one, \
             and every verb that crosses graphs reads it\n"
        ));
    }
    let here = planes(db)?;
    let width = have
        .list()
        .iter()
        .map(|a| a.name.chars().count())
        .max()
        .unwrap_or(0);
    let mut out = format!("plane '{plane}' attaches {}\n", have.list().len());
    for a in have.list() {
        out.push_str(&row(a, width, &here));
    }
    let missing = have
        .list()
        .iter()
        .filter(|a| a.is_local() && !here.iter().any(|p| p == &a.plane))
        .count();
    if missing > 0 {
        out.push_str(&format!(
            "{missing} attachment(s) name no plane on this instance — \
             digest it, or detach it\n"
        ));
    }
    if have.unreadable() > 0 {
        out.push_str(&format!(
            "ignored {} entry(s) under `{ATTACHMENTS}` that `attach` did not write\n",
            have.unreadable()
        ));
    }
    Ok(out)
}

/// Whether `url` names a drsg over HTTP: a scheme and a host, which is as much
/// as this layer can judge without a URL parser.
fn is_http_url(url: &str) -> bool {
    url.strip_prefix("http://")
        .or_else(|| url.strip_prefix("https://"))
        .is_some_and(|host| !host.is_empty() && !host.starts_with('/'))
}

/// The planes on this instance, by name.
fn planes(db: &Database) -> Result<Vec<String>> {
    Ok(db.planes()?.into_iter().map(|(_, name)| name).collect())
}

/// One attachment as a line: where it is, and whether it is there to be asked.
///
/// `width` is in characters, as every other padded listing here counts it.
fn row(a: &Attachment, width: usize, here: &[String]) -> String {
    let mut line = match &a.url {
        Some(url) => format!("  {:width$}  \u{2192} plane '{}' at {url}", a.name, a.plane),
        None => format!(
            "  {:width$}  \u{2192} plane '{}' on this instance",
            a.name, a.plane
        ),
    };
    match &a.url {
        Some(_) => line.push_str("   not contacted"),
        None if !here.iter().any(|p| p == &a.plane) => line.push_str("   no such plane here"),
        None => {}
    }
    line.push('\n');
    line
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::Database;

    fn plane() -> Database {
        let db = Database::in_memory().unwrap();
        db.create_plane("code", Properties::new()).unwrap();
        db
    }

    fn pair() -> Database {
        let db = plane();
        db.create_plane("web", Properties::new()).unwrap();
        db
    }

    #[test]
    fn an_unstated_plane_is_the_name_it_was_attached_under() {
        let a = Attachment::new("web", None, None);
        assert_eq!(a.plane, "web");
        assert!(a.is_local());
        let b = Attachment::new("web", Some("ui".into()), Some("http://h:1/".into()));
        assert_eq!(b.plane, "ui");
        assert!(!b.is_local());
    }

    #[test]
    fn an_attachment_survives_the_round_trip() {
        let db = plane();
        let p = db.plane("code").unwrap();
        let web = Attachment::new(
            "web",
            Some("ui".into()),
            Some("http://127.0.0.1:36500".into()),
        );
        assert_eq!(Attachments::attach(&p, web.clone()).unwrap(), None);
        assert_eq!(
            Attachments::attach(&p, Attachment::new("lib", None, None)).unwrap(),
            None
        );
        let have = Attachments::of(&p).unwrap();
        assert_eq!(have.list().len(), 2);
        assert_eq!(have.get("web"), Some(&web));
        assert_eq!(have.get("lib").unwrap().plane, "lib");
        assert_eq!(have.get("nothing"), None);
        assert_eq!(have.unreadable(), 0);
    }

    #[test]
    fn attaching_the_same_name_replaces_it_in_place() {
        let db = plane();
        let p = db.plane("code").unwrap();
        Attachments::attach(&p, Attachment::new("web", None, None)).unwrap();
        Attachments::attach(&p, Attachment::new("lib", None, None)).unwrap();
        let was = Attachments::attach(
            &p,
            Attachment::new("web", Some("ui".into()), Some("http://h:1".into())),
        )
        .unwrap();
        assert_eq!(was, Some(Attachment::new("web", None, None)));
        let have = Attachments::of(&p).unwrap();
        assert_eq!(
            have.list()
                .iter()
                .map(|a| a.name.as_str())
                .collect::<Vec<_>>(),
            ["web", "lib"],
            "a replacement keeps its place in the order"
        );
        assert_eq!(have.get("web").unwrap().plane, "ui");
    }

    #[test]
    fn detaching_reports_what_was_there() {
        let db = plane();
        let p = db.plane("code").unwrap();
        Attachments::attach(&p, Attachment::new("web", None, None)).unwrap();
        assert_eq!(Attachments::detach(&p, "nothing").unwrap(), None);
        assert_eq!(
            Attachments::detach(&p, "web").unwrap(),
            Some(Attachment::new("web", None, None))
        );
        assert!(Attachments::of(&p).unwrap().list().is_empty());
    }

    #[test]
    fn an_entry_attach_did_not_write_is_counted_not_dropped() {
        let db = plane();
        let p = db.plane("code").unwrap();
        Attachments::attach(&p, Attachment::new("web", None, None)).unwrap();
        p.update_properties(|props| {
            let PropDesc {
                value: PropValue::List(items),
                ..
            } = props.get_mut(ATTACHMENTS).unwrap()
            else {
                unreachable!()
            };
            items.push(PropValue::Str("hand-written".into()));
            items.push(PropValue::Map(Properties::new()));
        })
        .unwrap();
        let have = Attachments::of(&p).unwrap();
        assert_eq!(have.list().len(), 1);
        assert_eq!(
            have.unreadable(),
            2,
            "a bare string, and a map with no name"
        );
    }

    #[test]
    fn a_property_of_the_wrong_shape_reads_as_nothing_attached() {
        let db = plane();
        let p = db.plane("code").unwrap();
        p.update_properties(|props| {
            props.insert(
                ATTACHMENTS.into(),
                PropDesc::new(PropValue::Str("web".into())),
            );
        })
        .unwrap();
        let have = Attachments::of(&p).unwrap();
        assert!(have.list().is_empty());
        assert_eq!(have.unreadable(), 0);
    }
    #[test]
    fn a_local_attachment_names_a_plane_that_is_here() {
        let db = pair();
        let out = attach(&db, "code", Attachment::new("web", None, None)).unwrap();
        assert_eq!(
            out,
            "attached on plane 'code'\n  web  \u{2192} plane 'web' on this instance\n"
        );
    }

    #[test]
    fn a_local_attachment_to_no_plane_names_the_ones_there_are() {
        let db = pair();
        let err = attach(&db, "code", Attachment::new("ui", None, None)).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("plane 'ui' on this instance"), "{msg}");
        assert!(msg.contains("it holds: startup, code, web"), "{msg}");
        assert!(
            Attachments::of(&db.plane("code").unwrap())
                .unwrap()
                .list()
                .is_empty(),
            "a refused attach records nothing"
        );
    }

    #[test]
    fn a_plane_cannot_attach_itself() {
        let db = pair();
        let err = attach(
            &db,
            "code",
            Attachment::new("self", Some("code".into()), None),
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("cannot attach itself"), "{err}");
    }

    #[test]
    fn a_remote_attachment_is_recorded_as_given_and_not_contacted() {
        let db = plane();
        let out = attach(
            &db,
            "code",
            Attachment::new(
                "web",
                Some("ui".into()),
                Some("http://127.0.0.1:36500".into()),
            ),
        )
        .unwrap();
        assert_eq!(
            out,
            "attached on plane 'code'\n  \
             web  \u{2192} plane 'ui' at http://127.0.0.1:36500   not contacted\n"
        );
    }

    #[test]
    fn a_url_that_is_not_one_is_refused() {
        let db = plane();
        for bad in ["127.0.0.1:36500", "ftp://h", "http://", "http:///x"] {
            let err = attach(&db, "code", Attachment::new("web", None, Some(bad.into())))
                .unwrap_err()
                .to_string();
            assert!(err.contains("is not a drsg URL"), "{bad}: {err}");
        }
    }

    #[test]
    fn attaching_over_an_attachment_says_what_it_replaced() {
        let db = pair();
        attach(&db, "code", Attachment::new("web", None, None)).unwrap();
        let out = attach(
            &db,
            "code",
            Attachment::new("web", None, Some("http://h:1".into())),
        )
        .unwrap();
        assert_eq!(
            out,
            "attached on plane 'code'\n  \
             web  \u{2192} plane 'web' at http://h:1   not contacted\n  replaced\n  \
             web  \u{2192} plane 'web' on this instance\n"
        );
    }

    #[test]
    fn a_listing_says_which_attachment_is_not_there_to_be_asked() {
        let db = pair();
        attach(&db, "code", Attachment::new("web", None, None)).unwrap();
        attach(
            &db,
            "code",
            Attachment::new("远端", Some("ui".into()), Some("http://h:1".into())),
        )
        .unwrap();
        db.drop_plane(db.plane("web").unwrap().id()).unwrap();
        let out = attachments(&db, "code").unwrap();
        // The column is padded in characters, as every other listing here pads
        // it, so a wide glyph still sits a cell short.
        assert_eq!(
            out,
            "plane 'code' attaches 2\n  \
             web  \u{2192} plane 'web' on this instance   no such plane here\n  \
             远端   \u{2192} plane 'ui' at http://h:1   not contacted\n\
             1 attachment(s) name no plane on this instance — digest it, or detach it\n"
        );
    }

    #[test]
    fn a_plane_with_nothing_attached_says_what_to_do() {
        let db = plane();
        let out = attachments(&db, "code").unwrap();
        assert!(out.starts_with("plane 'code' attaches nothing"), "{out}");
    }

    #[test]
    fn a_listing_counts_what_attach_did_not_write() {
        let db = plane();
        let p = db.plane("code").unwrap();
        p.update_properties(|props| {
            props.insert(
                ATTACHMENTS.into(),
                PropDesc::new(PropValue::List(vec![PropValue::Str("web".into())])),
            );
        })
        .unwrap();
        let out = attachments(&db, "code").unwrap();
        assert!(out.contains("plane 'code' attaches 0"), "{out}");
        assert!(out.contains("ignored 1 entry(s)"), "{out}");
    }

    #[test]
    fn detaching_names_what_went_and_what_was_never_there() {
        let db = pair();
        attach(&db, "code", Attachment::new("web", None, None)).unwrap();
        assert_eq!(
            detach(&db, "code", "web").unwrap(),
            "detached from plane 'code'\n  web  \u{2192} plane 'web' on this instance\n"
        );
        assert_eq!(
            detach(&db, "code", "web").unwrap(),
            "plane 'code' has nothing attached\n"
        );
        attach(&db, "code", Attachment::new("web", None, None)).unwrap();
        assert_eq!(
            detach(&db, "code", "ui").unwrap(),
            "plane 'code' has no attachment named 'ui' — attached: web\n"
        );
    }
}
