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

use crate::api::PlaneHandle;
use crate::error::Result;
use crate::types::{PropDesc, PropValue, Properties};

/// The plane property an attachment list is recorded under.
pub const ATTACHMENTS: &str = "attachments";

/// What the property means, for anything that reads a plane's own map.
const WHAT_IT_IS: &str = "the planes this one may ask — a name, the plane it \
                          names there, and a URL when it is served elsewhere";

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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::Database;

    fn plane() -> Database {
        let db = Database::in_memory().unwrap();
        db.create_plane("code", Properties::new()).unwrap();
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
}
