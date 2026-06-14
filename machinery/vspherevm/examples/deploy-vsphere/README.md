# deploy-vsphere — ordered, split platform + xr bundle (LabDA)

A stand-alone, apply-in-order bundle for the `vspherevm` Configuration. Platform
setup is `0-`…`5-`; the only file a VM consumer edits is `6-user-xr.yaml`.

| File | Layer | Purpose |
|------|-------|---------|
| `0-platform-namespace.yaml` | platform | Namespace `vsphere-vms` for the XR. |
| `1-platform-configuration.yaml` | platform | Installs the `vspherevm` Configuration (deps pull provider + functions). |
| `2-platform-clusterproviderconfig.yaml` | platform | `.m` `ClusterProviderConfig/default` → `vsphere-creds`. |
| `3-platform-environmentconfig.yaml` | platform | LabDA placement MOIDs (label `…/environment: labda`). |
| `4-platform-clustersecretstore.yaml` | platform | **ESO** Vault `ClusterSecretStore` (optional; delete if you already have one). |
| `5-platform-externalsecret-vsphere-creds.yaml` | platform | **ESO** `ExternalSecret` → renders `vsphere-creds` from Vault. |
| `6-user-xr.yaml` | **xr** | The `NativeVsphereVM` request. |

## Credentials: ESO vs manual

`4-`/`5-` materialize `vsphere-creds` from Vault via External Secrets Operator —
the alternative to the manual `../secret.yaml.tmpl`. Pick **one**:

- **ESO (these files):** adjust the Vault address / KV mount (`cicd-vsphere-labda`)
  / k8s-auth mount / role in `4-`, then apply `4-` + `5-`. The `ExternalSecret`
  renders the provider's `credentials` JSON blob.
- **Manual:** skip `4-`/`5-` and create `vsphere-creds` from `../secret.yaml.tmpl`.

> ⚠️ The credentials JSON key MUST be `user`, not `username` — the provider reads
> `creds["user"]`; a wrong key yields a misleading vCenter
> `Cannot complete login due to an incorrect user name or password`.

## Apply

```bash
# platform (run once per cluster); drop 4-/5- if not using ESO
kubectl apply -f 0-platform-namespace.yaml
kubectl apply -f 1-platform-configuration.yaml
# wait for the Configuration to be Healthy + the XRD established
kubectl apply -f 2-platform-clusterproviderconfig.yaml -f 3-platform-environmentconfig.yaml
kubectl apply -f 4-platform-clustersecretstore.yaml -f 5-platform-externalsecret-vsphere-creds.yaml

# the VM request
kubectl apply -f 6-user-xr.yaml
```

> The `crossplane render` / CI verify harness uses the **top-level** `examples/`
> files (`functions.yaml`, `environmentconfig.yaml`, `xr*.yaml`); this bundle is
> the apply-ready deployment view and is not part of the render path.
