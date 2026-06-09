# HOW-TO: Provision Harvester VMs from a separate Crossplane cluster

A step-by-step runbook for the **remote two-cluster** setup: Crossplane runs on
the management cluster (`crossplane-mgmt`); the VMs are created on a **separate**
Harvester cluster via a kubeconfig Secret. Copy-paste the commands in order.

| Cluster | KUBECONFIG | Role |
|---------|-----------|------|
| `crossplane-mgmt` | `~/.kube/crossplane-mgmt` | runs Crossplane + provider-kubernetes — **you apply everything here** |
| Harvester | `~/.kube/harvester` | KubeVirt host — the PVC, cloud-init Secret and VirtualMachine land here |

---

## 0. Get the repo and switch to the branch

```bash
git clone https://github.com/stuttgart-things/crossplane-configurations.git
cd crossplane-configurations
git fetch origin claude/bold-ramanujan-9ogk4r
git switch claude/bold-ramanujan-9ogk4r
cd machinery/harvester-vm/examples/deploy-remote
```

---

## 1. Point kubectl at the management cluster

Everything in steps 2–6 runs against `crossplane-mgmt`:

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
kubectl get pods -n crossplane-system        # sanity: crossplane + provider-kubernetes Running
```

---

## 2. Create the Harvester kubeconfig Secret (deliverable a)

provider-kubernetes reads the remote cluster's kubeconfig from this Secret. The
Secret key MUST be `kubeconfig` and the namespace `crossplane-system` — both
match `deploy-remote/3-clusterproviderconfig-harvester.yaml`.

```bash
kubectl create secret generic harvester-kubeconfig \
  --from-file=kubeconfig=$HOME/.kube/harvester \
  --namespace crossplane-system
```

If `~/.kube/harvester` holds multiple contexts, flatten to a single one first so
the provider always targets the right cluster:

```bash
kubectl --kubeconfig ~/.kube/harvester config view --minify --flatten \
  > /tmp/harvester-kubeconfig
kubectl create secret generic harvester-kubeconfig \
  --from-file=kubeconfig=/tmp/harvester-kubeconfig -n crossplane-system
```

Verify:

```bash
kubectl -n crossplane-system get secret harvester-kubeconfig
```

---

## 3. Edit the EnvironmentConfig for your Harvester (deliverable c)

Open `4-environmentconfig.yaml` and replace the `PLACEHOLDER` values. Discover
the real ones on the **Harvester** cluster:

```bash
kubectl --kubeconfig ~/.kube/harvester get storageclass
kubectl --kubeconfig ~/.kube/harvester get net-attach-def -A          # networkName = <ns>/<name>
kubectl --kubeconfig ~/.kube/harvester get virtualmachineimages -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name        # imageId = <ns>/<name>
```

Keep `providerConfigRef: harvester` (it must match the ClusterProviderConfig in
file 3). Make sure the target `namespace` exists **on the Harvester cluster** —
it is not auto-created:

```bash
kubectl --kubeconfig ~/.kube/harvester get ns default      # the bundle default
# or, for a dedicated namespace:
kubectl --kubeconfig ~/.kube/harvester create namespace vms
```

---

## 4. Install the platform (deliverables b + c)

Apply the bundle against the management cluster. `kubectl apply -f <dir>` is
**non-recursive**, so this installs the plain-VM platform only — the `ansible/`
subfolder is opt-in (step 7):

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
kubectl apply -f .
```

This applies:

| File | What |
|------|------|
| `1-provider-kubernetes.yaml`             | provider-kubernetes (idempotent — already running) |
| `2-configuration-harvester-vm.yaml`      | the `harvester-vm` package (pulls Functions + cloud-config/volume-claim/ansible-run via dependsOn) |
| `3-clusterproviderconfig-harvester.yaml` | **(b)** `harvester` CPC → remote Harvester via the Secret from step 2 |
| `4-environmentconfig.yaml`               | **(c)** Harvester placement defaults |
| `5-xr.yaml`                              | an example VM (`dev1`) |

Wait for the package + provider config to be ready:

```bash
kubectl get configuration harvester-vm                 # INSTALLED=True HEALTHY=True
kubectl get clusterproviderconfig harvester            # should appear / be usable
```

---

## 5. Request a VM

`5-xr.yaml` already created `dev1`. For more VMs, copy it, change `metadata.name`,
`volume.pvcName`, `cloudInit.vmName`/`hostname` and the SSH key, then apply:

```bash
kubectl apply -f 5-xr.yaml
```

---

## 6. Watch it build

```bash
# Management cluster: XR status (share.ip, vm.ready)
kubectl -n default get harvestervm dev1
kubectl -n default get harvestervm dev1 -o jsonpath='{.status}{"\n"}'
crossplane beta trace harvestervm.resources.stuttgart-things.com/dev1 -n default

# Harvester cluster: the real objects
kubectl --kubeconfig ~/.kube/harvester -n default get virtualmachine,virtualmachineinstance,pvc
```

Order: VolumeClaim + CloudInit build → KubeVirt VM starts → QEMU guest agent
reports the IP onto `status.share.ip`. Keep `qemu-guest-agent` in
`cloudInit.packages` or the IP never appears on a Multus/bridge network.

---

## 7. (Optional) Ansible base-OS provisioning

Runs a playbook against the VM after it gets an IP. The Tekton PipelineRun runs
on the **management** cluster (via the `in-cluster` provider config), even though
the VM is remote. Files live in `ansible/`.

**a. Apply the Ansible platform (management cluster):**

```bash
kubectl apply -f ansible/1-namespace-tekton-ci.yaml
kubectl apply -f ansible/2-clusterproviderconfig-in-cluster.yaml
```

**b. Create the `ansible-credentials` Secret** the PipelineRun logs in with.
Keep `ANSIBLE_USER` in sync with the cloud-init user in your XR (`sthings`):

```bash
kubectl create secret generic ansible-credentials \
  --namespace tekton-ci \
  --from-literal=ANSIBLE_USER=sthings \
  --from-literal=ANSIBLE_PASSWORD='<vm-password>'        # pragma: allowlist secret
```

> Prefer not to type the password? Use External Secrets Operator instead — mirror
> `../../../virtual-machine/examples/deploy-harvester/6-platform-externalsecret-ansible-credentials.yaml`.

**c. Ansible RBAC** — the provider-kubernetes ServiceAccount must be allowed to
manage Tekton resources in `tekton-ci`:

```bash
SA=$(kubectl -n crossplane-system get sa -o name | grep provider-kubernetes | head -1 | cut -d/ -f2)
kubectl create clusterrolebinding provider-kubernetes-tekton \
  --clusterrole=cluster-admin \
  --serviceaccount=crossplane-system:$SA
```

> `cluster-admin` is the quick path; scope it down to the Tekton API groups for
> production (see `cicd/ansible-run/README.md`, precondition #3).

**d. The Tekton stack** (Pipelines controller + the `stage-time` Ansible
pipeline) must be installed on the management cluster — this bundle does not ship
it.

**e. Request a VM with Ansible enabled** (instead of `5-xr.yaml`):

```bash
kubectl apply -f ansible/3-xr-with-ansible.yaml
```

**Watch the Ansible side:**

```bash
kubectl -n default get harvestervm dev1 -o jsonpath='{.status.ansibleReady}{"\n"}'
kubectl -n default get ansiblerun
kubectl -n tekton-ci get pipelinerun -w
```

The `AnsibleRun` only appears **after** the VM reports an IP (it's gated on a
non-empty `status.share.ip`). XR `READY=True` once the PipelineRun succeeds.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `clusterproviderconfig harvester` not usable / auth errors | Secret key not `kubeconfig`, wrong namespace, or multi-context kubeconfig — redo step 2 with `--minify --flatten` |
| VM Object stuck: `namespaces "..." not found` | target namespace missing on the Harvester cluster — create it there (step 3) |
| `status.share.ip` stays empty | `qemu-guest-agent` removed from `cloudInit.packages`, or no guest agent in the image |
| `cannot resolve package dependencies: ... node ... already exists` | a long-named Function CR duplicates a short-named one — use short names only, delete the duplicate |
| AnsibleRun never appears | expected until the VM has an IP; also check Tekton stack + RBAC (step 7c/7d) |

---

## Clean up

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
kubectl delete -f 5-xr.yaml                    # or ansible/3-xr-with-ansible.yaml
# platform teardown (optional):
kubectl delete -f 4-environmentconfig.yaml -f 3-clusterproviderconfig-harvester.yaml
kubectl -n crossplane-system delete secret harvester-kubeconfig
```
