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
kubectl --kubeconfig ~/.kube/harvester get net-attach-def -A          # networkName = <ns>/<name>
kubectl --kubeconfig ~/.kube/harvester get virtualmachineimages -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name        # imageId = <ns>/<name>
# storageClassName: use the IMAGE'S class (lh-<uuid>), NOT the generic
# `harvester-longhorn` default — the generic class makes a blank disk with no OS:
kubectl --kubeconfig ~/.kube/harvester -n <img-ns> get virtualmachineimage <img-name> \
  -o jsonpath='{.status.storageClassName}{"\n"}'
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

Apply the bundle against the management cluster:

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

Running a playbook against the VM after it boots (`spec.ansible.enabled: true`)
is shipped as a **separate** example bundle — `../deploy-remote-ansible/` — so
this VM bundle stays minimal. It reuses this bundle's EnvironmentConfig and adds
the Tekton / `in-cluster` provider-config / credentials manifests it needs.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `clusterproviderconfig harvester` not usable / auth errors | Secret key not `kubeconfig`, wrong namespace, or multi-context kubeconfig — redo step 2 with `--minify --flatten` |
| VM Object stuck: `namespaces "..." not found` | target namespace missing on the Harvester cluster — create it there (step 3) |
| `status.share.ip` stays empty / no `AgentConnected` | **most common:** no `cloudInit.networkConfig`, so the guest gets an empty network-config and no DHCP lease on the Multus/bridge NIC → no internet → `qemu-guest-agent` can't install → no IP. Set the DHCP `networkConfig` (see `5-xr.yaml`). Also keep `qemu-guest-agent` in `cloudInit.packages`. |
| VM stuck `Stopping`/`RestartRequired`, `DisksNotLiveMigratable` | `evictionStrategy: LiveMigrateIfPossible` on a non-shared RWO/Block boot disk — set `vm.evictionStrategy: None` |
| `cannot resolve package dependencies: ... node ... already exists` | a long-named Function CR duplicates a short-named one — use short names only, delete the duplicate |

---

## Clean up

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
kubectl delete -f 5-xr.yaml
# platform teardown (optional):
kubectl delete -f 4-environmentconfig.yaml -f 3-clusterproviderconfig-harvester.yaml
kubectl -n crossplane-system delete secret harvester-kubeconfig
```
