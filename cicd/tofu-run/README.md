# tofu-run

A Crossplane v2 **namespaced** `TofuRun` Configuration that runs OpenTofu on a target cluster by emitting a Tekton `PipelineRun` from the XR — the tofu counterpart of [`ansible-run`](../ansible-run/).

The Composition renders the PipelineRun via [`function-kcl`](https://github.com/crossplane-contrib/function-kcl) from the KCL module [`kcl-tofu-pr`](https://github.com/stuttgart-things/kcl/tree/main/crossplane/kcl-tofu-pr) (pulled from OCI at render time), wrapped in a `kubernetes.m.crossplane.io/v1alpha1` Object so `provider-kubernetes` applies it on the cluster named by `spec.crossplaneProviderConfig`.

- **XR group/kind:** `resources.stuttgart-things.com/v1alpha1` / `TofuRun`
- **Scope:** Namespaced
- **Pipeline:** stage-time [`execute-tofu`](https://github.com/stuttgart-things/stage-time/blob/main/pipelines/execute-tofu.yaml)

## Vault auth

The `execute-tofu` pipeline authenticates to Vault via **AppRole** — `role_id`/`secret_id` are long-lived login material, exchanged per run for a short-lived policy token, so nothing static and privileged is stored. Provide them as `TF_VAR_vault_role_id` / `TF_VAR_vault_secret_id` in `credentialsSecretName`; `VAULT_ADDR` in `vaultSecretName`.

## Composition pipeline

- `function-environment-configs` — per-environment defaults from the `EnvironmentConfig` selected by `spec.environmentConfig` (config-scoped label `tofu-run.resources.stuttgart-things.com/environment`).
- `function-kcl` (`render-pipelinerun`) — renders the PipelineRun/Object from `kcl-tofu-pr`.
- `function-kcl` (`derive-status`) — surfaces the PipelineRun's `Succeeded` condition, reason/message, times and child TaskRuns onto the TofuRun status. Pipeline-agnostic; identical to `ansible-run`.
- `function-auto-ready` — with `spec.deriveReadiness: true` (default) the XR is Ready only once the pipeline succeeds.

## Prerequisites on the target cluster

- A `ClusterProviderConfig` for `provider-kubernetes` matching `spec.crossplaneProviderConfig`.
- Tekton, with the stage-time tasks reachable via the git resolver.
- Secrets `vault` and `terraform-credentials` in `spec.namespace` (see [Vault auth](#vault-auth)).

## Render locally

```bash
crossplane render \
  examples/xr.yaml apis/composition.yaml examples/functions.yaml \
  --extra-resources examples/environmentconfig.yaml
```

`examples/xr-min.yaml` relies on the XRD default `wrapInCrossplane: true`, which the apiserver applies but `crossplane render` does not — set it explicitly (as `examples/xr.yaml` does) to see the wrapped Object offline.
