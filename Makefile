.PHONY: help env install install-dev test test-unit test-integration clean lint format setup \
        az-login get-credentials bootstrap add-user admin-setup user-init write-config

VENV := .venv
UV   := uv

# ---------------------------------------------------------------------------
# Load .env if present – all variables are exported to sub-processes
# ---------------------------------------------------------------------------
ifneq (,$(wildcard .env))
  include .env
  export
endif

# Default target
help:
	@echo "Ascend Development Makefile"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@echo "  Development"
	@echo "  ----------"
	@echo "  make env            - Create virtual environment with uv"
	@echo "  make install        - Install package in development mode"
	@echo "  make install-dev    - Install package with dev dependencies"
	@echo "  make setup          - Full setup (env + dev deps)"
	@echo ""
	@echo "  Testing"
	@echo "  -------"
	@echo "  make test           - Run all tests"
	@echo "  make test-unit      - Run unit tests only (fast)"
	@echo "  make test-integration - Run integration tests on AKS (1200s timeout)"
	@echo ""
	@echo "  Admin Setup (requires .env – see .env.example)"
	@echo "  ------------------------------------------"
	@echo "  make az-login       - Login to Azure with service principal from .env"
	@echo "  make get-credentials - Fetch AKS kubeconfig"
	@echo "  make bootstrap      - Bootstrap Azure infrastructure (idempotent)"
	@echo "  make add-user [USERNAME=<name>] - Provision a user namespace on AKS (auto-derived if omitted)"
	@echo "  make admin-setup [USERNAME=<name>] - Full admin flow (login → credentials → bootstrap → add user)"
	@echo "  make user-init  - Run 'ascend user init' (discovers resources via Azure API)"
	@echo "  make write-config USERNAME=<name> - Write .ascend.yaml directly from .env (no API calls)"
	@echo ""
	@echo "  Housekeeping"
	@echo "  ------------"
	@echo "  make clean          - Remove build artifacts and cache"
	@echo "  make lint           - Run code linters"
	@echo "  make format         - Format code with black"

# Create virtual environment with uv
env:
	$(UV) venv $(VENV)
	@echo ""
	@echo "Virtual environment created at $(VENV)"
	@echo "All make targets automatically use this venv via uv run."

# Install package in development mode (includes Azure backend)
install: env
	$(UV) pip install -e ".[azure]"

# Install package with dev dependencies
install-dev: env
	$(UV) sync --all-extras

# Run all tests
test:
	$(UV) run pytest -v

# Run unit tests only (exclude integration tests)
test-unit:
	$(UV) run pytest -v --ignore=tests/integration -m "not integration"

# Run integration tests (requires real Azure/AKS infrastructure)
# Pass REBUILD_IMAGES=true to force-rebuild runtime images (bust all caches)
REBUILD_IMAGES ?= false
test-integration:
ifeq ($(REBUILD_IMAGES),true)
	$(UV) run pytest -v -m integration --timeout=1200 --rebuild-images
else
	$(UV) run pytest -v -m integration --timeout=1200
endif

# Clean build artifacts and cache
clean:
	rm -rf $(VENV)
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Run linters
lint:
	$(UV) run ruff check .
	$(UV) run mypy ascend/

# Format code
format:
	$(UV) run black .

# Complete setup (create env, install dev deps)
setup: install-dev
	@echo ""
	@echo "Setup complete."
	@echo "  Run CLI commands via:  uv run ascend <command>"
	@echo "  Run tests via:        make test-unit"
	@echo ""
	@echo "Quick admin setup:  make admin-setup USERNAME=alice"

# ---------------------------------------------------------------------------
# Admin Setup – sources credentials from .env (see .env.example)
# ---------------------------------------------------------------------------

# Shell helper – abort with a message if a variable is empty
_require = @test -n "$($(1))" || { echo "Error: $(1) is not set. Please create a .env file – see .env.example" >&2; exit 1; }

# Login to Azure using service principal credentials from .env
az-login:
	$(call _require,AZURE_TENANT_ID)
	$(call _require,AZURE_CLIENT_ID)
	$(call _require,AZURE_CLIENT_SECRET)
	@echo "Logging in to Azure (service principal)…"
	@az login --service-principal \
		-u "$(AZURE_CLIENT_ID)" \
		-p "$(AZURE_CLIENT_SECRET)" \
		--tenant "$(AZURE_TENANT_ID)" \
		--output none
	@echo "  ✓ Azure login succeeded"

# Fetch AKS kubeconfig
get-credentials: az-login
	$(call _require,AZURE_RESOURCE_GROUP)
	$(call _require,AZURE_AKS_CLUSTER_NAME)
	@echo "Fetching AKS credentials…"
	@az aks get-credentials \
		--resource-group "$(AZURE_RESOURCE_GROUP)" \
		--name "$(AZURE_AKS_CLUSTER_NAME)" \
		--overwrite-existing \
		--output none
	@echo "  ✓ kubeconfig updated"

# Bootstrap Azure infrastructure (storage, ACR, role assignments)
bootstrap: get-credentials
	$(call _require,AZURE_RESOURCE_GROUP)
	$(call _require,AZURE_AKS_CLUSTER_NAME)
	@echo "Bootstrapping infrastructure…"
	$(UV) run ascend admin bootstrap \
		--resource-group "$(AZURE_RESOURCE_GROUP)" \
		--cluster-name "$(AZURE_AKS_CLUSTER_NAME)" \
		$(if $(AZURE_STORAGE_ACCOUNT),--storage-account "$(AZURE_STORAGE_ACCOUNT)") \
		$(if $(AZURE_CONTAINER_REGISTRY),--container-registry "$(AZURE_CONTAINER_REGISTRY)")

# Provision a single user (USERNAME= is optional; derived from Azure identity
# when omitted, matching the behaviour of 'ascend user init')
add-user: get-credentials
	$(call _require,AZURE_AKS_CLUSTER_NAME)
	$(call _require,AZURE_RESOURCE_GROUP)
	@echo "Provisioning user…"
	$(UV) run ascend admin setup \
		$(if $(USERNAME),--username "$(USERNAME)") \
		--cluster "$(AZURE_AKS_CLUSTER_NAME)" \
		--resource-group "$(AZURE_RESOURCE_GROUP)"

# Full admin flow: login → credentials → bootstrap → provision user
admin-setup: bootstrap add-user
	@echo ""
	@echo "Admin setup complete."

# Run 'ascend user init' (interactive resource discovery via Azure API)
user-init: az-login get-credentials
	$(call _require,AZURE_AKS_CLUSTER_NAME)
	$(call _require,AZURE_RESOURCE_GROUP)
	$(UV) run ascend user init \
		--cluster "$(AZURE_AKS_CLUSTER_NAME)" \
		--resource-group "$(AZURE_RESOURCE_GROUP)"

# Write .ascend.yaml directly from .env variables (no Azure API calls)
write-config:
	@test -n "$(USERNAME)" || { echo "Error: USERNAME is required. Usage: make write-config USERNAME=alice" >&2; exit 1; }
	$(call _require,AZURE_AKS_CLUSTER_NAME)
	$(call _require,AZURE_RESOURCE_GROUP)
	$(call _require,AZURE_STORAGE_ACCOUNT)
	$(call _require,AZURE_CONTAINER_REGISTRY)
	@echo "Writing .ascend.yaml…"
	@printf '%s\n' \
		'cloud_provider: azure' \
		'username: $(USERNAME)' \
		'cluster_name: $(AZURE_AKS_CLUSTER_NAME)' \
		'resource_group: $(AZURE_RESOURCE_GROUP)' \
		'namespace: ascend-users-$(USERNAME)' \
		'storage_account: $(AZURE_STORAGE_ACCOUNT)' \
		'container_registry: $(AZURE_CONTAINER_REGISTRY)' \
		> .ascend.yaml
	@echo "  ✓ .ascend.yaml written"
