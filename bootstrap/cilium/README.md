# cilium

A Crossplane **v2** Configuration that installs Cilium and, optionally, its
**LoadBalancer** and a Gateway API **Gateway** on a target cluster, from a
namespaced `Cilium` XR (group `config.stuttgart-things.com`).

Superset of the [`cni`](../cni/) Configuration: `cni` installs the Cilium CNI;
`cilium` adds `loadBalancer` (`CiliumLoadBalancerIPPool` +
`CiliumL2AnnouncementPolicy`) and `gateway` (gateway-api CRDs + a Cilium
`GatewayClass` + a `Gateway`) on top of the install.

The Composition is **inline `function-kcl`** — no external OCI module. Provider
config refs derive from `spec.clusterName` (`{clusterName}-helm` /
`{clusterName}-kubernetes`), matching `cni`/`flux-init`, so the
[platform](../platform/) can compose it from a bare clusterName.

## What it emits

| # | Kind | When |
|---|------|------|
| 1 | `helm.m` Release — `cilium` | `install.enabled` (default true) |
| 2 | `Object` → `CiliumLoadBalancerIPPool` | `loadBalancer.enabled` and an IP is known |
| 3 | `Object` → `CiliumL2AnnouncementPolicy` | `loadBalancer.enabled` and an IP is known |
| 4 | `Object` → observe `XIPReservation` | `loadBalancer.ipMode: dynamic` |
| 5 | `helm.m` Release — `gateway-api` (CRDs) + `Object` → `GatewayClass` | `install.enabled` and `gatewayAPI.enabled` |

The **CRDs go in before Cilium**: with `gatewayAPI.enabled`, the Cilium Release is
withheld until the `gateway-api` Release reports Ready. Cilium enables its Gateway
controller only if the CRDs exist *when it starts* — installed afterwards,
`gatewayAPI.enabled` is a no-op until the operator and agents restart, and until
0.2.0 both Releases were emitted in the same pass, so which won was a race. The
gate is **sticky**: once the Cilium Release exists it keeps being emitted, because
not emitting a composed resource is how Crossplane deletes it, and dropping the CNI
of a running cluster because a CRD chart went briefly NotReady is worse than the
race it would close.
| 6 | `Object` → `Gateway` (HTTP + optional HTTPS/TLS) | `gateway.enabled` and `gateway.domain` set |
| — | `protection.crossplane.io` `Usage`s | teardown ordering (CRs/Gateway delete before the Helm Releases that own their CRDs) |

`status.ready` flips true once every enabled component reports Ready.

## LoadBalancer IP — the ip-reservation seam

- `loadBalancer.ipMode: static` → uses `loadBalancer.ipRange.{start,end}`.
- `loadBalancer.ipMode: dynamic` → Observes the named `XIPReservation`
  (`resources.stuttgart-things.com`, on the **management** cluster, from the
  [`ip-reservation`](../ip-reservation/) Configuration) and uses its reserved
  IPs as the pool block. This is what turns a clusterbook-reserved lab IP into
  an advertised Cilium LB VIP → Gateway → (DNS) → cert.

Dynamic mode needs the provider-kubernetes RBAC in
[`examples/rbac.yaml`](examples/rbac.yaml).

## Gateway domain

`gateway.domain` is optional. When it is unset, the domain is **derived from the
observed `XIPReservation`'s `fqdn`** (clusterbook `createDNS` emits a wildcard
`*.<cluster>.<zone>`; the `*.` is stripped for the bare domain). So a cluster
that reserves its own DNS name never has to restate it here. An explicit
`gateway.domain` always wins; with neither an explicit domain nor an observed
fqdn, the Gateway is withheld until one appears.

## Values this XRD does not model

`install.values` is merged **last** into the chart values, so it wins over every
modelled field — the same contract as [`cni`](../cni/)'s `spec.values`. Use it for
hubble, bpf, encryption and anything else not named here.

`install.ipamMode` defaults to **`kubernetes`** (node podCIDRs), which is what kind,
k3s and rke2 all assign, and what `cni` uses. The chart's own default is
`cluster-pool` (`10.0.0.0/8`), which does not match `ipv4NativeRoutingCIDR` — the
combination this Configuration shipped with until 0.2.0.

> **Which Configuration to use.** [`cni`](../cni/) installs Cilium and nothing else;
> this one adds the LoadBalancer pool and the Gateway. On fleet clusters registered
> in Argo CD, the LB and Gateway come from the `network-platform/cilium-lb` and
> `cilium-gateway` ApplicationSets instead — see
> [#438](https://github.com/stuttgart-things/crossplane-configurations/issues/438).
> This Configuration is for a cluster that has no Argo CD to do it.

## Already-installed Cilium

Set `install.enabled: false` to skip the Cilium Helm install (e.g. k3s that
already ships Cilium) and render only the LB/Gateway resources. The existing
Cilium must have `gatewayAPI` / `l2announcements` enabled for them to function.

## API

- **Group:** `config.stuttgart-things.com`
- **Version:** `v1alpha1`
- **Kind:** `Cilium` — Scope `Namespaced` (v2 XRD, no claim)
- **Required:** `spec.clusterName`

See [`examples/`](examples/) for `xr-min` (install only), `xr` (full LB +
Gateway with a dynamic reservation), `rbac.yaml`, and `functions.yaml`.
