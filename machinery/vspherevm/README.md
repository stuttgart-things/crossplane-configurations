# vspherevm

A Crossplane v2 Configuration that provisions a vSphere VM from a namespaced
`NativeVsphereVM` XR (group `resources.stuttgart-things.com`) via the **native**
[`provider-vspherevm`](https://github.com/stuttgart-things/provider-vspherevm)
managed `VirtualMachine` resource — no Terraform/OpenTofu.

It is the native-provider sibling of the OpenTofu-based [`vsphere-vm`](../vsphere-vm)
Configuration:

| | `vsphere-vm` (OpenTofu) | `vspherevm` (native) |
|---|---|---|
| Engine | OpenTofu `Workspace` → Terraform module | `provider-vspherevm` `VirtualMachine` MR |
| EnvironmentConfig values | inventory **paths/names** (`/LabDA/network/…`, `sthings-u24`) | opaque vCenter **MOIDs** (`dvportgroup-2048`, `templateUuid`, `datastore-12`, `resgroup-8`) |
| Credentials | tfvars Secret | `vsphere-creds` Secret via ProviderConfig |

## How it works

The Composition is a `function-kcl` pipeline:

1. **load-environment** (`function-environment-configs`) — loads the
   per-environment placement MOIDs from the `EnvironmentConfig` selected by the
   config-scoped label
   `vspherevm.resources.stuttgart-things.com/environment=<spec.environmentConfig>`.
2. **render** (`function-kcl`) — emits a native `VirtualMachine`; per-VM fields
   (name/cpu/ram/disk/…) come from the XR, placement MOIDs
   (templateUuid/datastoreId/resourcePoolId/networkId/folder) default from the
   EnvironmentConfig and are overridable per XR.
3. **patch-status** — surfaces `status.share.ip / moid / powerState` onto the XR.

So an XR only states intent; the lab topology is injected:

```yaml
apiVersion: resources.stuttgart-things.com/v1alpha1
kind: NativeVsphereVM
metadata:
  name: vspherevm-test1
  namespace: default
spec:
  environmentConfig: default     # or labda
  vm:
    name: vspherevm-test1
    cpu: "2"
    ram: "4096"
    disk: "40"
```

## Cluster preconditions

- `provider-vspherevm` installed (declared as `dependsOn`; also see
  `examples/provider.yaml`).
- A `ClusterProviderConfig` (`vspherevm.m.stuttgart-things.com/v1beta1`) named
  per the XR's `spec.providerConfigRef` (default kind `ClusterProviderConfig`,
  name `default`), pointing at a `vsphere-creds` Secret in `crossplane-system`.
  The Composition emits the namespaced (`.m`) `VirtualMachine` — a namespaced
  composite cannot compose a cluster-scoped resource — so it references a `.m`
  (Cluster)ProviderConfig. See `examples/clusterproviderconfig.yaml`.
  > ⚠️ The credentials JSON key MUST be `user`, **not** `username` — the provider
  > reads `creds["user"]`; a wrong key yields a misleading vCenter
  > `Cannot complete login due to an incorrect user name or password`.
- The `EnvironmentConfig`(s) for the target environment(s) — see
  `examples/environmentconfig.yaml`.

## Render locally

```bash
CONFIG=machinery/vspherevm XR=machinery/vspherevm/examples/xr.yaml task render
```

The render needs the EnvironmentConfig passed via `--extra-resources` (the
`task render` / verify harness defaults to `examples/environmentconfig.yaml`).
