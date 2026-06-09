# deploy-remote-ansible — add Ansible base-OS provisioning

Layers **Ansible post-provisioning** on top of the
[`deploy-remote`](../deploy-remote/) VM bundle. When an XR sets
`spec.ansible.enabled: true`, `harvester-vm` waits for the VM to report an IP
(`status.share.ip`), then emits an `AnsibleRun` that runs a playbook against it
via a Tekton `PipelineRun`.

> **Two clusters, two jobs.** The VM is created on the **remote Harvester**
> (via the `harvester` kubeconfig-Secret provider config from `deploy-remote`).
> The Ansible Tekton PipelineRun runs on the **management cluster** (via the
> `in-cluster` provider config shipped here). They are deliberately different
> provider configs — the IP-gated `AnsibleRun` connects from the management
> cluster over the network to the VM's IP.

## Prerequisite: apply `deploy-remote` first

This bundle reuses `deploy-remote`'s EnvironmentConfig (which already carries the
`ansible:` block: `crossplaneProviderConfig: in-cluster` + the runner image) and
its remote `harvester` provider config + kubeconfig Secret. Stand up the VM
platform first — see [`../deploy-remote/HOWTO.md`](../deploy-remote/HOWTO.md)
steps 1–4 — then come back here.

## Files (apply against the management cluster)

| File | Scope | What |
|------|-------|------|
| `1-namespace-tekton-ci.yaml`              | namespaced (creates) | `tekton-ci` — the PipelineRun + credentials namespace |
| `2-clusterproviderconfig-in-cluster.yaml` | cluster              | provider-kubernetes `ClusterProviderConfig` `in-cluster` (InjectedIdentity) — runs the PipelineRun on the mgmt cluster |
| `3-xr-with-ansible.yaml`                  | namespaced           | the VM request with `ansible.enabled: true` (apply INSTEAD of `deploy-remote/5-xr.yaml`) |

## Steps

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
cd machinery/harvester-vm/examples/deploy-remote-ansible
```

**1. Apply the Ansible platform:**

```bash
kubectl apply -f 1-namespace-tekton-ci.yaml
kubectl apply -f 2-clusterproviderconfig-in-cluster.yaml
```

**2. Create the `ansible-credentials` Secret** the PipelineRun logs in with.
Keep `ANSIBLE_USER`/`ANSIBLE_PASSWORD` in sync with the cloud-init user/password
in the XR (`sthings` / `ChangeMe123!`):

```bash
kubectl create secret generic ansible-credentials -n tekton-ci \
  --from-literal=ANSIBLE_USER=sthings \
  --from-literal=ANSIBLE_PASSWORD='ChangeMe123!'      # pragma: allowlist secret
```

> Prefer not to type the password? Source it from Vault via ESO instead — mirror
> `../../../virtual-machine/examples/deploy-harvester/6-platform-externalsecret-ansible-credentials.yaml`.

**3. Request the VM with Ansible enabled** (instead of `deploy-remote/5-xr.yaml`;
drop your SSH key in first):

```bash
kubectl apply -f 3-xr-with-ansible.yaml
```

**4. Watch the Ansible side:**

```bash
kubectl -n default get harvestervm dev1 -o jsonpath='{.status.share.ip}{"  ansibleReady="}{.status.ansibleReady}{"\n"}'
kubectl -n default get ansiblerun
kubectl -n tekton-ci get pipelinerun -w
```

The `AnsibleRun` only appears **after** the VM reports an IP (it's gated on a
non-empty `status.share.ip`), so it won't exist until the VM is up. XR
`READY=True` once the PipelineRun succeeds.

## External prerequisites (NOT shipped here)

The `ansible: true` path needs cluster infra this bundle does not ship, because
it's shared and/or environment-specific:

1. **Tekton stack** — the Tekton Pipelines controller plus the Pipeline the
   `AnsibleRun` references (the `stuttgart-things/stage-time` Ansible pipeline).
   `tekton-ci` (file 1) is only the run namespace, not the controller. Check:
   `kubectl get crd | grep tekton`.
2. **RBAC for the provider-kubernetes ServiceAccount** — it must be allowed to
   manage Tekton resources in `tekton-ci`. The SA name is dynamic:
   ```bash
   SA=$(kubectl -n crossplane-system get sa -o name | grep provider-kubernetes | head -1 | cut -d/ -f2)
   kubectl create clusterrolebinding provider-kubernetes-tekton \
     --clusterrole=cluster-admin \
     --serviceaccount=crossplane-system:$SA
   ```
   (`cluster-admin` is the quick path; scope it to the Tekton API groups for
   production — see `cicd/ansible-run/README.md`, precondition #3.)
3. **QEMU guest agent in the image** — the VM IP that gates the AnsibleRun is
   reported by the in-guest agent. The XRD defaults `cloudInit.packages` to
   `[qemu-guest-agent]`; keep it.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `AnsibleRun` never appears | expected until the VM has an IP — confirm `status.share.ip` is non-empty first (see `deploy-remote/HOWTO.md` troubleshooting) |
| PipelineRun not created / Object errors | `in-cluster` ClusterProviderConfig missing (file 2), or the provider-kubernetes SA lacks Tekton RBAC (prereq 2) |
| PipelineRun fails to start | Tekton stack / the `stage-time` Pipeline not installed (prereq 1) |
| PipelineRun auth failure to the VM | `ANSIBLE_USER`/`ANSIBLE_PASSWORD` don't match the cloud-init user/password in the XR |

## Clean up

```bash
kubectl -n default delete harvestervm dev1
kubectl -n tekton-ci delete secret ansible-credentials
# platform teardown (optional):
kubectl delete -f 2-clusterproviderconfig-in-cluster.yaml -f 1-namespace-tekton-ci.yaml
```
