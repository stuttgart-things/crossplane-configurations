# management-plane

Turns a target cluster into a **management** cluster from a namespaced
`ManagementPlane` XR: installs Crossplane on it, then the providers, functions,
configurations and in-cluster provider configs that let it build other clusters.

```yaml
apiVersion: config.stuttgart-things.com/v1alpha1
kind: ManagementPlane
metadata:
  name: mgmt-1
  namespace: default
spec:
  clusterName: mgmt-1
```

That is the whole surface for a normal management cluster.

## Why this is not part of `platform`

`platform` answers *what does this cluster offer*. Being a management cluster is
a **role** — and the two have different lifetimes: a workload cluster's apps
change weekly, its control plane does not. So `ManagementPlane` is a sibling of
`Platform`, composed alongside it, not a component inside it.

## What it emits

Against the target cluster, through the same provider config refs a `Platform`
uses:

| | |
|---|---|
| `helm.m.crossplane.io` Release | the crossplane chart |
| `kubernetes.m.crossplane.io` Object | one per Provider / Function / Configuration |
| `kubernetes.m.crossplane.io` Object | one per in-cluster ProviderConfig |

## The package set is not in the XR

It comes from
[`xplane-crossplane-catalog`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-crossplane-catalog)
via `spec.profile` (default `machinery`): 4 providers, 5 functions, 11
configurations, 3 provider configs. It is a **fleet fact** — the same on every
management cluster here — so it lives where it can be version-controlled and
unit-tested rather than copied into each XR.

The catalog is also where the naming rule is *asserted*: providers and
configurations carry the CR name Crossplane itself derives from the package
path, which is what makes the
[#247](https://github.com/stuttgart-things/crossplane-configurations/issues/247)
Lock collision impossible rather than merely unlikely.

`status.transitive` reports the eleven packages that arrive through another
package's `dependsOn` without ever being named — because "what is installed
here" is not answerable from the spec.

### Breaking out

```yaml
spec:
  packageOverrides:
    stuttgart-things-crossplane-configurations-platform:
      ghcr.io/stuttgart-things/crossplane-configurations/platform:v0.3.9
```

Keyed by the **CR name**, not the short name — `platform` is rejected as
unknown, deliberately: silently ignoring it would leave you believing a pin took
effect. The value replaces the **whole reference**, not just the tag, because a
tag-only override would keep the catalog's registry and the mirror is
load-bearing (`function-kcl` sits on `xpkg.upbound.io` precisely because our
`dependsOn` entries use `xpkg.crossplane.io`).

## Install order

Forced by CRDs, not preference: the `pkg.crossplane.io` CRDs arrive *with*
Crossplane, and each provider's `ProviderConfig` CRD arrives with that provider.

```
Release ready ──▶ packages ──▶ provider configs
```

Emitting everything at once would still converge, since the Objects retry. What
it costs is the ability to diagnose a failed bootstrap: the real errors are
buried under minutes of expected ones.

**Every gate is one-way.** Not emitting a composed resource is how Crossplane
deletes it, so a gate that closed again would tear the cluster down — a
Crossplane Deployment briefly NotReady would uninstall the whole fleet's
packages.

## Cluster preconditions

- a Helm `ClusterProviderConfig` named `{clusterName}-helm`
- a Kubernetes `ClusterProviderConfig` named `{clusterName}-kubernetes`

Both are what `provider-kubeconfig`'s `RemoteCluster` creates for a registered
cluster — the same precondition `platform` has.

Note what is **not** a precondition: this package does not `dependsOn` the
Configurations it installs. They are applied as Objects onto a *different*
cluster; pulling them onto this one would install the whole fleet's
Configurations on the seed.

## RBAC

One `ClusterRoleBinding`: the group `system:serviceaccounts:crossplane-system`
to `cluster-admin`.

**The group, not a ServiceAccount.** Crossplane's provider SAs carry a
per-revision generated name, so binding one by name applies to nothing — and
RBAC does not validate subjects, so it looks like it worked. That is the trap
[#251](https://github.com/stuttgart-things/crossplane-configurations/issues/251)
fixed.

**`cluster-admin`, deliberately.** On a management cluster the providers create
arbitrary cluster content; that is the job. The two narrower ClusterRoles the
machinery play fetches from raw URLs (`provider-kubernetes-remote-cluster`,
`provider-kubernetes-ip-reservation`) bind the *same group*, so this subsumes
both — they stop being a precondition.

Emitted alongside the Release rather than behind a gate:
`rbac.authorization.k8s.io` is built in, so unlike everything else here it needs
no CRD first, and the providers hold their permissions before the first reconcile
rather than after a failed one.

`spec.clusterAdminRbac: false` turns it off, for a cluster where something else
grants the equivalent. Not a hardening knob — what it removes is the ability to
reconcile anything at all. Without it the cluster has every XRD and can act on
none of them, which presents as a hang rather than an error.

## Not yet covered

**Capability charts** are out and stay out: their values are per-environment,
which is the line the catalog holds.

## Versions

| What | Version | Where it comes from |
|---|---|---|
| `management-plane` Configuration | `v0.2.0` | [`crossplane.yaml`](crossplane.yaml) |
| `xplane-management-plane` KCL module | `0.2.0` | [`apis/composition.yaml`](apis/composition.yaml) (OCI, pulled at render time) |
| `xplane-crossplane-catalog` KCL module | `0.1.0` | dependency of the above — the package set |
| Crossplane on the TARGET cluster | `2.3.3` | the `machinery` profile |
