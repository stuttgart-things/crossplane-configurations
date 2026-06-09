# deploy-remote — HarvesterVM from a separate management cluster

Stand up a Harvester / KubeVirt VM where **Crossplane runs on one cluster
(`crossplane-mgmt`) and the VMs land on a separate Harvester cluster**. This is
the remote sibling of the in-tree
[`virtual-machine/.../deploy-harvester`](../../../virtual-machine/examples/deploy-harvester/)
bundle, but scoped to the standalone `harvester-vm` Configuration and wired for
the two-cluster (kubeconfig-Secret) topology instead of `InjectedIdentity`.

> **Want the step-by-step?** [`HOWTO.md`](HOWTO.md) is the copy-paste runbook
> (clone → branch → secret → platform → VM). This README is the
> reference/file-index.

The two clusters:

| Cluster | KUBECONFIG | Role |
|---------|-----------|------|
| `crossplane-mgmt` | `~/.kube/crossplane-mgmt` | runs Crossplane + provider-kubernetes; you apply everything here |
| Harvester | `~/.kube/harvester` | KubeVirt host; the PVC, cloud-init Secret and VirtualMachine land here |

Apply order (files are numbered for a directory apply — **all against the
management cluster**):

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
```

## (a) Create the Harvester kubeconfig Secret on the management cluster

provider-kubernetes reads the remote cluster's kubeconfig from a Secret. Create
it directly from your Harvester kubeconfig file (never commit it):

```bash
kubectl create secret generic harvester-kubeconfig \
  --from-file=kubeconfig=$HOME/.kube/harvester \
  --namespace crossplane-system
```

> The key is `kubeconfig` (the `--from-file=kubeconfig=...` left-hand side) —
> it must match `spec.credentials.secretRef.key` in file 3. The namespace
> (`crossplane-system`) must match `secretRef.namespace`.
>
> If your `~/.kube/harvester` has multiple contexts, export a single-context,
> token-based kubeconfig first so the provider always targets the right cluster:
>
> ```bash
> kubectl --kubeconfig ~/.kube/harvester config view --minify --flatten \
>   > /tmp/harvester-kubeconfig
> kubectl create secret generic harvester-kubeconfig \
>   --from-file=kubeconfig=/tmp/harvester-kubeconfig -n crossplane-system
> ```

## (b) + (c) Install the platform and request a VM

```bash
kubectl apply -f machinery/harvester-vm/examples/deploy-remote/
```

| File | Scope | What |
|------|-------|------|
| `1-provider-kubernetes.yaml`            | cluster    | provider-kubernetes install (already present on crossplane-mgmt; idempotent) |
| `2-configuration-harvester-vm.yaml`     | cluster    | installs the `harvester-vm` package — its `dependsOn` pulls the Functions + `cloud-config`/`volume-claim`/`ansible-run` Configurations automatically |
| `3-clusterproviderconfig-harvester.yaml`| cluster    | **(b)** provider-kubernetes `ClusterProviderConfig` `harvester` → REMOTE Harvester via the kubeconfig Secret from (a) |
| `4-environmentconfig.yaml`              | cluster    | **(c)** shared Harvester placement defaults (provider config, storage class, namespace, network, image) |
| `5-xr.yaml`                             | namespaced | the VM request — the only file you edit per VM |

> Apply order matters only in that the **Secret (a) must exist before file 3**
> can become usable, and files 3 + 4 should exist before the XR (5) reconciles.
> `kubectl apply -f <dir>` runs files in lexical order; Crossplane reconciles
> continuously, so an early XR simply waits for its provider config / env config.

### Before a real apply — edit these

- **`4-environmentconfig.yaml`** — every value except `providerConfigRef` is a
  PLACEHOLDER. Fill in your real `storageClassName`, `namespace`, `networkName`
  and `imageId` (discovery commands are in the file header).
- **Target namespace must exist on the Harvester cluster.** The PVC, cloud-init
  Secret and VirtualMachine land in `data.namespace` (file 4, default
  `default`). It is **not** auto-created. If you use a dedicated namespace:
  ```bash
  kubectl --kubeconfig ~/.kube/harvester create namespace vms
  ```
- **`5-xr.yaml`** — replace the SSH public key.

## Verify

```bash
# management cluster — provider config should be READY, XR SYNCED/READY
kubectl get clusterproviderconfig harvester
kubectl -n default get harvestervm dev1
kubectl -n default get harvestervm dev1 -o jsonpath='{.status}{"\n"}'   # share.ip, vm.ready
crossplane beta trace harvestervm.resources.stuttgart-things.com/dev1 -n default

# Harvester cluster — the actual VM
kubectl --kubeconfig ~/.kube/harvester -n default get virtualmachine,virtualmachineinstance,pvc
```

Expected order: VolumeClaim/CloudInit build first, the KubeVirt VM starts, the
in-guest QEMU guest agent reports the VM IP onto `status.share.ip`. Keep
`qemu-guest-agent` in `cloudInit.packages` — without it the VMI never reports an
IP on a bridge/Multus network and `status.share.ip` stays empty.

## Common failure modes

- **`ClusterProviderConfig harvester` not READY / auth errors** — the Secret key
  or namespace doesn't match file 3, or the kubeconfig has no usable context.
  Re-check (a); use the `--minify --flatten` form.
- **VM Object stuck, `namespaces "..." not found`** — the target namespace
  doesn't exist on the Harvester cluster. Create it there (see above).
- **`cannot resolve package dependencies: ... node ... already exists`** — a
  long-named Function CR (e.g. `crossplane-contrib-function-go-templating`)
  duplicates a short-named one. Use the short names only; delete the duplicate.

## Ansible base-OS provisioning

Optional post-provisioning (`spec.ansible.enabled: true`) is shipped as a
**separate** example bundle alongside this one — see `../deploy-remote-ansible/`
once it lands. It reuses this bundle's EnvironmentConfig and adds the Tekton /
`in-cluster` provider-config / credentials manifests it needs.
