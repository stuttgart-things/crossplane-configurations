# ip-reservation

A Crossplane **v2** Configuration that reserves IP addresses (and optional wildcard DNS) from [clusterbook](https://github.com/stuttgart-things/clusterbook) for a target cluster, from a namespaced `XIPReservation` XR (group `resources.stuttgart-things.com`).

The network is derived automatically from the target cluster's `RemoteCluster.status.internalNetworkKey` (`provider-kubeconfig`) — no need to specify a subnet. The Composition is a thin [`function-kcl`](https://github.com/crossplane-contrib/function-kcl) wrapper around the [`xplane-ip-reservation`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/xplane-ip-reservation) KCL module (pulled from OCI at render time).

## What it does

| # | Kind | Purpose | Condition |
|---|------|---------|-----------|
| 1 | `kubernetes.m` Object (Observe) | reads `RemoteCluster.status.internalNetworkKey` | always |
| 2 | `kubernetes.m` Object (wrapper) | `IPReservation` (`provider-clusterbook`) | once networkKey is known |

Render and status run in a single KCL pass. The IPReservation is withheld until the Observe populates the networkKey, then `status` (reserved IPs, DNS FQDN/zone) is patched back onto the XR as it becomes Ready.

## API

- **Group:** `resources.stuttgart-things.com`
- **Version:** `v1alpha1`
- **Kind:** `XIPReservation` (the `X` avoids a plural collision with the clusterbook `ipreservations` MRD)
- **Scope:** `Namespaced` (v2 XRD — no claim)

### Spec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `clusterName` | string | yes | | RemoteCluster to read `internalNetworkKey` from |
| `count` | integer | no | `1` | Number of IPs to reserve |
| `ip` | string | no | | Explicit IP — skips auto-reservation |
| `createDNS` | boolean | no | `false` | Create a PDNS wildcard record |
| `clusterbookProviderConfigRef` | string | no | `default` | clusterbook `ClusterProviderConfig` name |
| `kubernetesProviderConfigRef` | string | no | `in-cluster` | Kubernetes `ClusterProviderConfig` (InjectedIdentity) for the Objects |

### Status

| Field | Description |
|-------|-------------|
| `ready` | IPReservation is Ready |
| `networkKey` | `internalNetworkKey` from the RemoteCluster |
| `ipAddresses` | reserved IP addresses |
| `reservationStatus` | e.g. `ASSIGNED`, `ASSIGNED:DNS` |
| `fqdn` | wildcard DNS name (when `createDNS`) |
| `zone` | DNS zone (when `createDNS`) |

## Cluster preconditions

- `provider-kubeconfig` installed, with a `RemoteCluster` for the target cluster exposing `internalNetworkKey`.
- `provider-clusterbook` installed, with a `ClusterProviderConfig` matching `spec.clusterbookProviderConfigRef` (`examples/cluster-provider-config.yaml`).
- An in-cluster `kubernetes.m` `ClusterProviderConfig` (InjectedIdentity) named per `spec.kubernetesProviderConfigRef`.
- RBAC for the provider-kubernetes SA: `get,list,watch,patch` on `remoteclusters` and full CRUD on `ipreservations` (`examples/rbac.yaml`).

## DEV

Local render (no cluster). With an empty environment the Observe Object renders but the IPReservation is withheld (no networkKey yet):

```bash
crossplane render examples/xr.yaml \
  apis/composition.yaml \
  examples/functions.yaml \
  --include-function-results
```
