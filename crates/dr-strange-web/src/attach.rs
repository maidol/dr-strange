//! Reading a plane another drsg serves (issues #42, #43).
//!
//! The question travels as a serialized [`LogicalPlan`] over `plane.query`,
//! the read method every drsg has served for releases, so federating a verb
//! adds no RPC method and leaves the SDKs alone. This is the half core cannot
//! hold: a storage engine opens no sockets, so it declares [`Remote`] and the
//! surfaces that can reach the network supply it.

use std::io::Read;
use std::net::IpAddr;
use std::time::Duration;

use dr_strange_core::attach::{Attachment, Remote};
use dr_strange_core::{LogicalPlan, NodeRecord, PlaneId, json};
use serde_json::{Value, json as jval};
use url::Url;

use crate::fetch::{Network, Prefix, Route, guard, protect};

/// Longest an attached plane has to answer in.
const ANSWER_TIMEOUT: Duration = Duration::from_secs(60);

/// Most one answer may be — the limit `/rpc` puts on a request, applied to a
/// reply. A plane is asked for its routes rather than for itself, so this
/// bounds a URL that is not a drsg, not an honest answer.
const MAX_ANSWER: u64 = 64 * 1024 * 1024;

/// Reads an attached plane over `plane.query` on another drsg.
///
/// The address guard stays on, re-permitted for exactly the host the
/// attachment names — an operator attaching a peer on loopback is the
/// ordinary case, and switching the guard off for the whole request would
/// permit every other private address with it, redirects included.
///
/// The reply is what is withheld: an `Err` reaches a reader, so it reports a
/// body by shape and quotes one only where it is a drsg's own JSON-RPC error.
pub struct Reader {
    net: Network,
    token: Option<String>,
}

impl Reader {
    /// A reader that goes where `net` says, presenting `token`.
    pub fn new(net: Network, token: Option<String>) -> Self {
        Self { net, token }
    }

    /// [`Reader::new`] with the `DRSG_TOKEN` this process holds.
    ///
    /// Attached graphs belong to one environment, so the caller presents the
    /// token it already has; a drsg wanting a different one is a different
    /// environment, not an attachment.
    pub fn from_env(net: Network) -> Self {
        let token = std::env::var("DRSG_TOKEN").ok().filter(|t| !t.is_empty());
        Self::new(net, token)
    }
}

impl Remote for Reader {
    fn nodes(&self, at: &Attachment, plan: &LogicalPlan) -> Result<Vec<NodeRecord>, String> {
        let url = at.url.as_deref().ok_or("it names no url")?;
        let url = format!("{}/rpc", url.trim_end_matches('/'));
        let parsed = Url::parse(&url).map_err(|e| format!("{url} is not a url: {e}"))?;
        let allow = reachable(&parsed)?;
        let route = Route {
            net: &self.net,
            allow: &allow,
        };
        guard::precheck(&parsed, route.allow, route.net).map_err(|e| format!("{e:#}"))?;

        let body = serde_json::to_vec(&jval!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "plane.query",
            "params": { "plane": at.plane, "plan": plan },
        }))
        .map_err(|e| format!("serializing the plan: {e}"))?;
        let agent = protect(
            ureq::Agent::config_builder().timeout_global(Some(ANSWER_TIMEOUT)),
            &parsed,
            route,
        )
        .map_err(|e| format!("{e:#}"))?;
        let mut post = agent
            .post(parsed.as_str())
            .header("content-type", "application/json");
        if let Some(t) = &self.token {
            post = post.header("Authorization", &format!("Bearer {t}"));
        }
        let reply = post.send(&body).map_err(|e| match e {
            ureq::Error::StatusCode(code) => format!("{url} answered HTTP {code}"),
            other => format!("{url}: {other}"),
        })?;

        let mut text = String::new();
        Read::take(reply.into_body().into_reader(), MAX_ANSWER)
            .read_to_string(&mut text)
            .map_err(|e| format!("reading what {url} answered: {e}"))?;
        let reply: Value = serde_json::from_str(&text)
            .map_err(|_| format!("{url} answered {} bytes that are not json", text.len()))?;
        rows(&reply, &url)?
            .iter()
            .map(|n| {
                // The reply carries no plane id, and nothing here could read
                // one: what tells the graphs apart is the attachment's name.
                json::json_to_node(n, PlaneId::STARTUP)
                    .map_err(|e| format!("{url} answered a node that will not read: {e}"))
            })
            .collect()
    }
}

/// The addresses an attachment may be read from: the one its URL names, and
/// nothing else.
///
/// [`guard::PublicOnly`] refuses loopback and private ranges, which is
/// where a peer drsg lives, so the host the operator wrote down is
/// re-permitted rather than the guard being switched off. A named host is
/// resolved here and its addresses permitted, so neither a later answer to
/// that name nor a redirect can move the request to an address the operator
/// did not name.
fn reachable(url: &Url) -> Result<Vec<Prefix>, String> {
    let addrs: Vec<IpAddr> = match url.host() {
        Some(url::Host::Ipv4(ip)) => vec![ip.into()],
        Some(url::Host::Ipv6(ip)) => vec![ip.into()],
        Some(url::Host::Domain(host)) => {
            let port = url.port_or_known_default().unwrap_or(80);
            std::net::ToSocketAddrs::to_socket_addrs(&(host, port))
                .map_err(|e| format!("resolving {host}: {e}"))?
                .map(|a| a.ip())
                .collect()
        }
        None => return Err(format!("{url} names no host")),
    };
    if addrs.is_empty() {
        return Err(format!("{url} resolves to no address"));
    }
    addrs
        .iter()
        .map(|ip| Prefix::parse(&ip.to_string()).map_err(|e| format!("{e:#}")))
        .collect()
}

/// The `result` array of a JSON-RPC reply, or why there is none.
///
/// A drsg's own `error` is quoted: that is the asked drsg's structured answer
/// to the question — `plane 'ui' not found` is what an operator needs to see.
/// Any other body is reported by shape, never by content.
fn rows<'a>(reply: &'a Value, url: &str) -> Result<&'a Vec<Value>, String> {
    if let Some(e) = reply.get("error") {
        let code = e.get("code").and_then(Value::as_i64).unwrap_or_default();
        let message = e
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("no message");
        return Err(format!("{url} refused the query ({code}): {message}"));
    }
    match reply.get("result") {
        Some(Value::Array(rows)) => Ok(rows),
        Some(_) => Err(format!("{url} answered a result that is not a node list")),
        None => Err(format!("{url} answered no result")),
    }
}
