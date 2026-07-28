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

## Naming

Two distinct names, and only one of them is `spec.vm.name`:

| | value | set from |
|---|---|---|
| vSphere inventory VM | `metadata.name` | always the XR name — must stay unique per folder |
| Guest hostname | `spec.vm.name`, falling back to `metadata.name` | cloud-init guestinfo metadata at first boot |

Setting them equal (as every example does) is the normal case. The override
exists for **composed** use: a parent Composition must give each child XR a
unique `metadata.name`, typically by prefixing its own, and without this knob
that prefix would land in the guest hostname too. `spec.vm.name` lets the parent
keep a clean guest name while the XR and the vSphere object stay prefixed —
matching how [`proxmoxvm`](../proxmoxvm/) already treats its own `spec.vm.name`.

Renaming the guest after first boot has no effect: cloud-init only re-applies
config when `instance-id` changes, and that stays pinned to the XR name.

> ⚠️ **On today's ubuntu26 templates the cloud-init path does nothing.** They
> ship `/etc/cloud/cloud-init.disabled` — subiquity leaves it behind on an
> autoinstall and `cloud-init clean` does not remove it — so `cloud-init status`
> is `disabled` and every clone boots as **`localhost`**. The metadata *is*
> delivered (`vmware-rpctool "info-get guestinfo.metadata"` returns it); nothing
> consumes it. Tracked as
> [stuttgart-things/stuttgart-things#2432](https://github.com/stuttgart-things/stuttgart-things/issues/2432).
>
> Until those templates are rebuilt, the **ansible run is the only working
> hostname path**, so this Configuration now passes `vm_hostname+-<hostname>`
> into the `AnsibleRun` vars automatically whenever `spec.ansible.enabled` is
> true. An explicit `vm_hostname` in `spec.ansible.varsFile` always wins. With
> ansible disabled *and* an affected template, the guest stays `localhost`.

## Ansible (optional)

Set `spec.ansible.enabled: true` to run base-OS provisioning. Once the VM is
Ready with an IP, the Composition emits an `AnsibleRun` (Tekton) whose inventory
is auto-populated with the VM IP:

```yaml
spec:
  environmentConfig: labda
  vm:
    name: vm1
  ansible:
    enabled: true
    playbooks: [sthings.baseos.setup]
    varsFile: [manage_filesystem+-true, update_packages+-true]
    crossplaneProviderConfig: in-cluster
```

Shared fields (`playbooks`, `varsFile`, `gitRepoUrl`, `ansibleWorkingImage`,
`credentialsSecretName`, `crossplaneProviderConfig`, `pipelineNamespace`) fall
back to the EnvironmentConfig `ansible` sub-block when unset. This reuses the
[`ansible-run`](../../cicd/ansible-run) Configuration and needs its preconditions
on the cluster: Tekton, the ansible credentials Secret (default
`ansible-credentials`), and an in-cluster `provider-kubernetes` config.
`status.share.ansibleReady` reflects the run's readiness.

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
