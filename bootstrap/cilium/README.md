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
