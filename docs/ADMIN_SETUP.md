# Administrator Setup

This guide covers infrastructure bootstrap and user provisioning for Ascend on Azure AKS.

## Prerequisites

- **Azure CLI** (`az`) installed and authenticated
- **kubectl** configured with cluster-admin access
- **Python 3.11–3.13**
- Azure role: **Contributor** on the resource group
- Kubernetes: **cluster-admin** role

## Quick Start (Makefile)

The Makefile automates the full admin flow using credentials from a `.env` file.

**1. Create your `.env`** (see `.env.example` for the template):

```bash
cp .env.example .env
# Fill in Azure subscription, resource group, AKS cluster,
# and service principal credentials.
```

**2. Run the one-liner** — this logs in with the service principal, fetches AKS
credentials, bootstraps infrastructure, and provisions the user:

```bash
make admin-setup USERNAME=alice
```

`USERNAME` is optional — when omitted the username is derived from the current
Azure identity (same logic used by `ascend user init`):

```bash
make admin-setup   # derives username from Azure credential
```

Or run individual steps:

```bash
make az-login          # Azure SP login
make get-credentials   # fetch kubeconfig
make bootstrap         # create storage, ACR, role assignments (idempotent)
make add-user USERNAME=alice
```

### What Bootstrap Creates

- Storage account and blob container for job artifacts
- Container registry for runtime images
- Role assignments for the AKS kubelet managed identity

### What User Provisioning Creates

- Namespace `ascend-users-{username}`
- ServiceAccount with RBAC permissions
- Verifies blob storage and ACR access

## Manual CLI Setup

```bash
# 1. Authenticate with Azure
az login

# 2. Get Kubernetes credentials
az aks get-credentials --resource-group <resource-group> --name <cluster>

# 3. Bootstrap infrastructure (storage account, container registry, role assignments)
uv run ascend admin bootstrap --resource-group <resource-group> --cluster-name <cluster>

# 4. Provision a user (--username is optional; derived from Azure identity when omitted)
uv run ascend admin setup --username alice --cluster <cluster> --resource-group <resource-group>
```

## Azure RBAC Requirements

### Roles Required by the Bootstrap Identity

Running `ascend admin bootstrap` creates storage accounts, a container registry,
role assignments, and a runtime image. The identity executing the bootstrap needs:

| Azure Role | Scope | Why |
|---|---|---|
| **Contributor** | Resource group | Create/update Storage Account, Blob Container, and Container Registry |
| **User Access Administrator** (or **Owner**) | Resource group | Assign `Storage Blob Data Contributor` to the bootstrap principal and to the AKS kubelet identity; assign `AcrPull` + `AcrPush` to the kubelet identity |
| **Azure Kubernetes Service Cluster User Role** | AKS cluster | Read cluster credentials and kubelet identity metadata |

### Roles Assigned to AKS Kubelet Identity

The bootstrap process assigns these roles to the AKS kubelet managed identity
so that runner pods can operate without additional user intervention:

| Assigned Role | Target Principal | Scope | Purpose |
|---|---|---|---|
| **Storage Blob Data Contributor** | AKS kubelet identity | Storage account | Runner pods read/write packages and results |
| **AcrPull** | AKS kubelet identity | Container Registry | Pods pull runtime images |
| **AcrPush** | AKS kubelet identity | Container Registry | In-cluster Kaniko builds push custom images |

> **Tip:** If you cannot get `User Access Administrator`, ask an Owner to
> pre-assign the roles listed in the second table and run the bootstrap with
> `--skip-role-assignments` (or simply re-run — every step is idempotent).
