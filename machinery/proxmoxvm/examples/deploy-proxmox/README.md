# deploy-proxmox — ordered, split platform + xr bundle (LabUL)

A stand-alone, apply-in-order bundle for the `proxmoxvm` Configuration. Platform
setup is `0-`…`5-`; the only file a VM consumer edits is `6-user-xr.yaml`.

| File | Layer | Purpose |
|------|-------|---------|
| `0-platform-namespace.yaml` | platform | Namespace `proxmox-vms` for the XR. |
| `1-platform-configuration.yaml` | platform | Installs the `proxmoxvm` Configuration (deps pull provider + functions). |
| `2-platform-clusterproviderconfig.yaml` | platform | `.m` `ClusterProviderConfig/default` → `proxmox-creds`. |
| `3-platform-environmentconfig.yaml` | platform | LabUL placement (label `…/environment: labul`). |
| `4-platform-clustersecretstore.yaml` | platform | **ESO** Vault `ClusterSecretStore` (optional; delete if you already have one). |
| `5-platform-externalsecret-proxmox-creds.yaml` | platform | **ESO** `ExternalSecret` → renders `proxmox-creds` from Vault. |
| `6-user-xr.yaml` | **xr** | The `NativeProxmoxVM` request. |

## Credentials: ESO vs manual

`4-`/`5-` materialize `proxmox-creds` from Vault via External Secrets Operator —
the alternative to the inline Secret in `../clusterproviderconfig.yaml`. Pick
**one**:

- **ESO (these files):** adjust the Vault address / k8s-auth mount / role in `4-`,
  then apply `4-` + `5-`. The `ExternalSecret` renders the bpg provider's
  `credentials` JSON blob from the `cicd-proxmox-labul` KV (path `default`):

  | Vault key | → JSON key |
  |-----------|-----------|
  | `pve_api_url` | `endpoint` |
  | `pve_api_user` | `username` |
  | `pve_api_password` | `password` |
  | `vm_ssh_user` | `ssh_username` |
  | `vm_ssh_password` | `ssh_password` |

- **Manual:** skip `4-`/`5-` and create `proxmox-creds` from the inline Secret in
  `../clusterproviderconfig.yaml` (token auth shown there; swap for
  username/password if preferred).

> The bpg provider uses SSH to the node for some operations (e.g. uploading
> cloud-init snippets), so the `ssh_username`/`ssh_password` keys are included —
> drop them if your flow doesn't need node SSH.

## Template VMID (clone)

The native bpg provider clones a template by its **numeric VMID**, not its name.
The `labul` EnvironmentConfig sets `templateVmId: "110"` (the `sthings-u26`
template on `ul-pve01`). Resolve your own template's VMID and adjust if different.
The root disk is on `virtio0` (not `scsi0`) — `diskInterface` is set accordingly.

## Apply

```bash
# platform (run once per cluster); drop 4-/5- if not using ESO
kubectl apply -f 0-platform-namespace.yaml
kubectl apply -f 1-platform-configuration.yaml
# wait for the Configuration to be Healthy + the XRD established
kubectl apply -f 2-platform-clusterproviderconfig.yaml -f 3-platform-environmentconfig.yaml
kubectl apply -f 4-platform-clustersecretstore.yaml -f 5-platform-externalsecret-proxmox-creds.yaml

# the VM request
kubectl apply -f 6-user-xr.yaml
```

> The `crossplane render` / CI verify harness uses the **top-level** `examples/`
> files (`functions.yaml`, `environmentconfig.yaml`, `xr*.yaml`); this bundle is
> the apply-ready deployment view and is not part of the render path.
