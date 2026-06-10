# Mixed-cluster runbook — split control plane, generic + Harvester

End-to-end recipe for the stuttgart-things lab, where Crossplane and Rancher run
on **different** clusters (split control plane):

| Cluster | Kubeconfig | Role |
|---|---|---|
| `crossplane-mgmt` | `~/.kube/crossplane-mgmt` | Crossplane control plane (provider-kubernetes, functions, this Configuration) |
| `platform.sthings.lab` | `~/.kube/platform.sthings.lab` | Rancher management + Harvester credential |

Vision: **`kind: Cluster` → extract kubeconfig → wire into Crossplane → register
in central Argo CD.** The provision step differs per infrastructure (`generic`
custom-node vs `harvester` VM machine pool); everything downstream
(bridge → wired `ClusterProviderConfig` → bootstrap → Argo CD) is identical.

All commands below run against `crossplane-mgmt` unless noted.

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
```

## 0. Preconditions (one-time)

- `provider-kubernetes` (>=v1.2.0) + `function-go-templating` + `function-auto-ready`
  installed on `crossplane-mgmt` (short names).
- Harvester cloud credential exists on the Rancher cluster (Rancher UI →
  Cluster Management → Cloud Credentials). Find its secret name:
  ```bash
  KUBECONFIG=~/.kube/platform.sthings.lab \
    kubectl -n cattle-global-data get secrets | grep '^cc-'
  # -> cattle-global-data:<name>  goes into spec.harvester.cloudCredentialSecretName
  ```

## 1. Bridge the Rancher kubeconfig to the control plane

The split path needs a `ClusterProviderConfig` on `crossplane-mgmt` that targets
the Rancher cluster. Back it with the Rancher admin kubeconfig:

```bash
kubectl -n crossplane-system create secret generic rancher-mgmt-kubeconfig \
  --from-file=kubeconfig=$HOME/.kube/platform.sthings.lab \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f cluster-provider-config-rancher.yaml   # creates CPC 'rancher-mgmt'
```

> The kubeconfig's `server:` must be reachable from `crossplane-mgmt`
> (here `https://192.168.10.134:6443`, same subnet — OK). A `127.0.0.1` server
> would not work cross-cluster.

## 2. Install the Configuration (XRD + Composition)

From a local checkout (no OCI push needed for dev):

```bash
kubectl apply -f ../apis/definition.yaml
kubectl apply -f ../apis/composition.yaml
# functions are already installed on crossplane-mgmt; otherwise:
# kubectl apply -f functions.yaml
```

## 3. Provision a Harvester cluster

```bash
kubectl apply -f xr-harvester.yaml      # RancherCluster 'k3s-xp', infrastructure: harvester
```

Watch the XR and the emitted Objects:

```bash
kubectl get ranchercluster k3s-xp -n default -w
kubectl get object.kubernetes.m.crossplane.io -l app.kubernetes.io/instance=k3s-xp-cluster
```

On the Rancher cluster, the provisioning Cluster + HarvesterConfig + the VM:

```bash
export KUBECONFIG=~/.kube/platform.sthings.lab
kubectl -n fleet-default get cluster.provisioning.cattle.io k3s-xp
kubectl -n fleet-default get harvesterconfig.rke-machine-config.cattle.io k3s-xp-pool1
```

## 4. Extract kubeconfig → wired ClusterProviderConfig (the "export" step)

Once Rancher publishes `k3s-xp-kubeconfig` in `fleet-default`, the bridge Object
surfaces it as connection Secret `k3s-xp-kubeconfig-bridged` on `crossplane-mgmt`,
and the wired CPC `k3s-xp` consumes it:

```bash
export KUBECONFIG=~/.kube/crossplane-mgmt
kubectl -n default get secret k3s-xp-kubeconfig-bridged          # bridged kubeconfig
kubectl get clusterproviderconfig.kubernetes.m.crossplane.io k3s-xp
# proof: the bootstrap namespace lands on the DOWNSTREAM cluster
kubectl get object.kubernetes.m.crossplane.io -l app.kubernetes.io/instance=k3s-xp-bootstrap-namespace
```

## 5. (Optional) Register in central Argo CD

Argo CD + clusterbook-operator run on the **Rancher cluster** here; that target
(`argocdProviderConfigRef: rancher-mgmt`) comes from the EnvironmentConfig, so the
XR just flips `register: true`. The Composition mints an `argocd-manager` SA on the
downstream cluster, **auto-discovers** its API endpoint (from the downstream
`kubernetes` Endpoints), assembles a direct-endpoint kubeconfig (endpoint +
downstream CA + SA token), and emits the kubeconfig Secret + `ClusterbookCluster`
on the Rancher cluster; clusterbook-operator then creates the Argo CD
`cluster-k3s-xp` Secret.

```yaml
spec:
  argocd:
    register: true     # target from EnvironmentConfig; server auto-discovered
```

```bash
# the produced Argo CD cluster Secret (on the Rancher/Argo cluster)
KUBECONFIG=~/.kube/platform.sthings.lab kubectl -n argocd get secret cluster-k3s-xp
```

## Generic vs Harvester

| | `infrastructure: generic` (default) | `infrastructure: harvester` |
|---|---|---|
| Provision objects | one bare `provisioning.cattle.io/Cluster` (no pools) | `HarvesterConfig` + `Cluster` with a machine pool |
| Node registration | manual (Rancher registration command) | automatic (Harvester VMs) |
| Extra spec | — | `spec.harvester` (cloud cred, image, network, sizing) |
| Downstream flow | identical | identical |

Generic example: `xr-split.yaml`. Harvester example: `xr-harvester.yaml`.

## Teardown

```bash
kubectl delete -f xr-harvester.yaml      # Foreground delete; Rancher removes the VM
```
