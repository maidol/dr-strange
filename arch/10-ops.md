# Ops graph

**Status**: design · begun 2026-10-17 · nothing implemented yet

The year's thesis is that a code commit and a production incident are two
ends of one graph. This doc fixes the half that does not exist yet: what an
operations topology looks like as nodes and edges, how it is identified, and
— the part that decides whether the thesis holds at all — how it joins to the
code and history planes that are already there.

Four decisions are settled, and the rest of this doc is their consequences:

1. Ops data lives in **its own plane**, `<name>_ops`, the way history lives
   in `<name>_git`.
2. The **container image** is the join carrier between a running thing and
   the commit that built it.
3. The plugins read **declared state** — manifests as written — and not a
   live cluster.
4. ConfigMap **values are recorded**; Secret values never are.

## 1. Scope: declared state, not runtime

In scope for the first version, because a file in a repository is all a
parser plugin needs:

| | |
|---|---|
| Kubernetes | `Deployment`, `StatefulSet`, `DaemonSet`, `Job`, `CronJob`, `Service`, `Ingress`, `ConfigMap`, `Secret`, `PersistentVolumeClaim`, `Namespace` |
| Compose | services, networks, volumes, `depends_on`, published ports |
| Terraform | `resource`, `data`, `module`, `variable`, `output`, `provider`, and the references between them |

Out of scope, and deliberately so: **Pods, nodes, metrics, alerts**. Each is
cluster state, not a declaration — it needs credentials, it is stale the
moment it is read, and none of it belongs to a plugin whose input is a
directory. The vocabulary below reserves labels for them so that Q1's
Prometheus and OpenTelemetry work extends this model instead of replacing it.

This is the same line the `deps` plugin draws: what a manifest *declares* is
worth recording even where the thing it declares cannot be observed.

## 2. The plane

A digested repository already produces two planes — `<name>` for the code as
it is now, `<name>_git` for its history. Ops is the third:

```
myrepo        code as it is now
myrepo_git    commits, branches, tags
myrepo_ops    services, workloads, config, routes
```

Its own plane because an ops resync and a code digest have unrelated
lifecycles: a chart changes on a different cadence from the code, and
re-reading one must not invalidate the other. Planes are cheap
([09-planes.md](09-planes.md) §1), so a third is not a cost.

**And because planes are hard namespaces, this choice has a consequence that
shapes everything in §5: a cross-plane edge is rejected at the storage
layer** (`Error::PlaneMismatch`). There is no edge from a Workload to the
Commit that built it. The join is a shared property and a query, never an
edge, until the `portals` concept in 09-planes §6 either ships or does not.

## 3. Nodes

Populated by the first version:

| Label | Key | What it is |
|---|---|---|
| `Namespace` | `<name>` | A k8s namespace, or a Compose project |
| `Workload` | `<ns>/<kind>/<name>` | Anything that runs a container. `kind` on the node (`Deployment`, `CronJob`, a Compose `service`) rather than one label per kind, so a query can ask for every workload at once |
| `Container` | `<workload key>/<container name>` | One container spec inside a workload |
| `Image` | the reference as written | The join carrier — see §5 |
| `Service` | `<ns>/Service/<name>` | A stable address in front of workloads |
| `Ingress` | `<ns>/Ingress/<name>` | External entry, with host and path on the edges it makes |
| `ConfigMap`, `Secret` | `<ns>/<kind>/<name>` | Configuration. §7 governs their contents |
| `Volume` | `<ns>/Volume/<name>` | A PVC, or a Compose volume |
| `Network` | `<ns>/Network/<name>` | A Compose network. k8s has no analogue worth a node |

Terraform declares the layer the other two run *on*, and adds:

| Label | Key | What it is |
|---|---|---|
| `Infra` | `<type>.<name>` | A managed resource, keyed by Terraform's own address — `aws_instance.web`. The provider's type is a property, because there are thousands of them and a label per type would be a vocabulary nobody could query |
| `DataSource` | `data.<type>.<name>` | Something read rather than managed |
| `Module` | `module.<name>` | A module block, with its `source` |
| `Variable` | `var.<name>` | An input. §7 governs its default |
| `Output` | `output.<name>` | What the module publishes |
| `Provider` | `provider.<name>` | A configured provider |

`Infra` rather than `Resource` **on purpose**: the Kubernetes reader already
uses `Resource` as its catch-all for a kind it does not model, and one label
meaning both "an unmodelled Kubernetes object" and "a managed cloud resource"
would make every query over either one wrong.

Reserved, so that later work does not rename these:

`Pod`, `Node` (cluster runtime, Q1) · `Metric`, `Alert` (Prometheus, Q1) ·
`DeployEvent` (a rollout, Q1 — *not* called `Deployment`, which is a
workload kind here and would collide) · `Incident` (Q2).

## 4. Edges

| Edge | From → to | Carries |
|---|---|---|
| `IN_NAMESPACE` | anything → `Namespace` | — |
| `RUNS` | `Workload` → `Container` | — |
| `USES_IMAGE` | `Container` → `Image` | — |
| `SELECTS` | `Service` → `Workload` | the selector as written, and `matched_by` (`selector`, `name`) |
| `ROUTES_TO` | `Ingress` → `Service` | `host`, `path`, `port` |
| `MOUNTS` | `Container` → `ConfigMap` \| `Secret` \| `Volume` | `as` (`env`, `envFrom`, `file`), `key` where one key is named, `path` for a file |
| `DEPENDS_ON` | `Workload` → `Workload` | Compose `depends_on`, with `condition` |
| `ATTACHED_TO` | `Workload` \| `Service` → `Network` | — |

Terraform's edges come from *references*, which is where its graph lives:

| Edge | From → to | Carries |
|---|---|---|
| `DEPENDS_ON` | `Infra` → `Infra` \| `DataSource` | `via` (`reference` or `depends_on`) — an interpolated address is a dependency as surely as a declared one |
| `USES_VARIABLE` | `Infra` \| `Module` → `Variable` | — |
| `USES_MODULE` | `Namespace` → `Module` | — |
| `PROVIDED_BY` | `Infra` → `Provider` | — |
| `EXPOSES` | `Output` → `Infra` | — |
| `USES_IMAGE` | `Infra` → `Image` | the same edge the other two readers draw, so a task definition reaches a revision |

`USES_IMAGE` from an `Infra` node is the join paying off twice: a container
image named in Terraform — an ECS task definition, a Cloud Run service, a
`docker_container` — reaches the same `Image` node the Kubernetes reader
would mint, under the same key, with the same `revision` parsed by the same
SDK code.

Reserved: `SCHEDULED_ON` (Pod → Node) · `SCRAPES` (Metric → Workload) ·
`ALERTS_ON` (Alert → anything) · `CALLS` (Service → Service, from traces) ·
`DEPLOYED` (DeployEvent → Workload) · `CAUSED_BY` (Incident → DeployEvent).

`SELECTS` is the one edge with real work behind it. A k8s Service names pods
by label selector, and the pods come from a workload's pod template — so the
edge is computed by matching the Service's `spec.selector` against each
workload's `spec.template.metadata.labels`. Where a selector matches nothing
in the tree, the Service is recorded and the edge is not, with the selector
kept on the node so a reader can see what it asked for.

## 5. Identity, and the image join

Within the ops plane, keys are structural and exact: a namespace, a kind, a
name. Nothing is guessed.

Across planes, the carrier is the **image reference**, because it is the one
string both a manifest and a build pipeline name. The `Image` node keeps the
reference as written, plus whatever is parseable from it:

```
ghcr.io/acme/api:sha-9f2c1b7
└─ registry ──┘ └─repo─┘ └── tag ──┘   → revision = "9f2c1b7"
```

A tag component that looks like a git revision — `sha-<hex>`, `git-<hex>`, a
bare 7-to-40 character hex string — is recorded as a `revision` property. A
digest (`@sha256:…`) is recorded as `digest`, which identifies the image
exactly but says nothing about the commit.

**The join is then a query, not an edge**, and it runs across two planes:
read `revision` from `myrepo_ops`, look up that commit in `myrepo_git`. Both
halves are exact; the only inference is that the tag means what it looks
like.

## 6. Where the join holds, and where it does not

- **A revision-tagged image joins cleanly.** `api:sha-9f2c1b7` and commit
  `9f2c1b7` are the same thing by construction, because a pipeline put the
  sha in the tag.
- **A floating tag joins to nothing.** `api:latest`, `api:v2`, `api:stable`
  name no commit. The image is recorded; `revision` is absent, and a query
  that wants a commit gets nothing rather than a guess.
- **A digest-only reference is exact but silent.** `api@sha256:…` pins the
  bytes and omits the provenance. Resolving it needs a registry call —
  outside what a parser does, and a candidate for Q1's deploy-event work.
- **A third-party image never joins, correctly.** `postgres:17` is not built
  from this repository, and nothing should connect it to a commit here.
- **The repository may not be the one that built the image.** A monorepo of
  charts deploys images from elsewhere. The `revision` is recorded either
  way; whether `myrepo_git` holds that commit is a question the query
  answers, not an assumption the model makes.

The rule, same as `deps`: record what the manifest says, join only where the
join is exact, and state the gap where it is not.

## 7. Config and secrets

**ConfigMap values are recorded.** A query can then see what a service is
configured with, which is most of what a config question asks.

The consequence has to be stated rather than discovered: those values land
in the plane, and therefore in every backup and export of it. A ConfigMap in
a real repository sometimes carries things that should have been a Secret —
a connection string with a password in it is the usual one. **A plane built
from a repository's manifests should be treated as carrying whatever those
manifests carry.**

**Terraform states it outright, and that is the rule.** A `variable` or
`output` marked `sensitive = true` is the author saying so, exactly as
choosing `ConfigMap` over `Secret` is — so a default or a value is recorded
unless the block declares itself sensitive.

Two Terraform files are **never read at all**: `*.tfvars` is values rather
than declarations and is where real credentials are kept, and
`terraform.tfstate` is runtime state that contains every attribute of every
resource in plain text, including the secret ones. Neither is a declaration,
so neither is in scope under §1 even before the secret question arises.

**Secret values are never recorded**, in any encoding. The node carries the
name, the keys, and the type; `data` and `stringData` values are dropped at
parse time, not redacted later. `base64` is not encryption, and a plugin that
read it would put real credentials in a graph that gets copied around.

## 8. What the later quarters add

The model is shaped so these extend rather than rewrite it:

- **Q1 — Prometheus**: `Metric` nodes, `SCRAPES` to the workload a target
  resolves to, `ALERTS_ON` from an alerting rule.
- **Q1 — OpenTelemetry**: `CALLS` between services, from traces. This is the
  first edge in the ops plane that is observed rather than declared, and it
  should carry that distinction on the edge.
- **Q1 — deploy events**: `DeployEvent` nodes joined to the image digest and
  so, through §5, to a commit. This is where the image-to-commit join stops
  being a query and starts being recorded.
- **Q2 — incidents**: `Incident`, `CAUSED_BY`, and the time-travel queries
  that read the topology as it was at the moment of a failure.

## 9. Open questions

1. **Cross-plane reads.** The §5 join needs one query to read two planes.
   Whether that is a client-side join, a new verb, or the `portals` concept
   from 09-planes §6 is undecided — and it is the single thing that most
   affects whether this track delivers its headline.
2. **Helm and Kustomize.** The plugins parse rendered YAML; something has to
   render it. A `helm template` invocation is not a parser's job, so this is
   likely a CLI step that writes a rendered tree the plugin then reads.
3. **One plane per environment.** `prod` and `staging` are different
   topologies from the same chart. A plane each, a namespace node each, or a
   property on every node — unresolved, and it interacts with question 1.
4. **Whether `Container` earns its node.** It could be properties on the
   workload. It exists here because `MOUNTS` and `USES_IMAGE` are per
   container, and a sidecar is a different thing from its main container.
