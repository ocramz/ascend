"""Tests for username derivation, namespace discovery, and user-init validation.

Covers the three layers of the namespace mismatch UX fix:
  1. Fail-fast validation — user init aborts when namespace doesn't exist.
  2. Namespace discovery — available namespaces are listed for the user.
  3. Deterministic naming — shared derive_username_from_credential utility.
"""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException


# ---------------------------------------------------------------------------
# Layer 3 — derive_username_from_credential (shared utility)
# ---------------------------------------------------------------------------


class TestDeriveUsernameFromCredential:
    """Tests for ascend.utils.naming.derive_username_from_credential."""

    @staticmethod
    def _make_credential(claims: dict) -> MagicMock:
        """Build a mock credential whose token encodes *claims* as a JWT."""
        import base64, json

        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
        # JWT = header.payload.signature — header and sig can be empty.
        token_str = f"hdr.{payload.decode()}.sig"
        cred = MagicMock()
        token_obj = MagicMock()
        token_obj.token = token_str
        cred.get_token.return_value = token_obj
        return cred

    def test_extracts_upn(self):
        from ascend.utils.naming import derive_username_from_credential

        cred = self._make_credential({"upn": "Alice.Smith@contoso.com"})
        assert derive_username_from_credential(cred) == "alice-smith"

    def test_extracts_unique_name(self):
        from ascend.utils.naming import derive_username_from_credential

        cred = self._make_credential({"unique_name": "Bob.Jones@contoso.com"})
        assert derive_username_from_credential(cred) == "bob-jones"

    def test_extracts_preferred_username(self):
        from ascend.utils.naming import derive_username_from_credential

        cred = self._make_credential(
            {"preferred_username": "Carol.White@contoso.com"}
        )
        assert derive_username_from_credential(cred) == "carol-white"

    def test_upn_has_priority_over_unique_name(self):
        from ascend.utils.naming import derive_username_from_credential

        cred = self._make_credential(
            {"upn": "priority@contoso.com", "unique_name": "other@contoso.com"}
        )
        assert derive_username_from_credential(cred) == "priority"

    def test_fallback_to_os_user(self):
        from ascend.utils.naming import derive_username_from_credential

        cred = MagicMock()
        cred.get_token.side_effect = Exception("no token")
        with patch("getpass.getuser", return_value="localuser"):
            assert derive_username_from_credential(cred) == "localuser"

    def test_empty_claims_fallback(self):
        from ascend.utils.naming import derive_username_from_credential

        cred = self._make_credential({})  # no upn/unique_name/preferred_username
        with patch("getpass.getuser", return_value="fallback"):
            assert derive_username_from_credential(cred) == "fallback"

    def test_dots_replaced_by_hyphens(self):
        from ascend.utils.naming import derive_username_from_credential

        cred = self._make_credential({"upn": "first.middle.last@example.com"})
        assert derive_username_from_credential(cred) == "first-middle-last"


# ---------------------------------------------------------------------------
# Layer 2 — list_user_namespaces
# ---------------------------------------------------------------------------


class TestListUserNamespaces:
    """Tests for ascend.cloud.kubernetes.namespace.list_user_namespaces."""

    @staticmethod
    def _ns(name: str) -> MagicMock:
        ns = MagicMock()
        ns.metadata.name = name
        return ns

    def test_returns_only_ascend_user_namespaces(self):
        from ascend.cloud.kubernetes.namespace import list_user_namespaces

        core_v1 = MagicMock()
        ns_list = MagicMock()
        ns_list.items = [
            self._ns("default"),
            self._ns("kube-system"),
            self._ns("ascend-users-alice"),
            self._ns("ascend-users-bob"),
            self._ns("ascend-projects-myrepo"),
        ]
        core_v1.list_namespace.return_value = ns_list

        result = list_user_namespaces(core_v1=core_v1)

        assert result == ["ascend-users-alice", "ascend-users-bob"]

    def test_returns_sorted(self):
        from ascend.cloud.kubernetes.namespace import list_user_namespaces

        core_v1 = MagicMock()
        ns_list = MagicMock()
        ns_list.items = [
            self._ns("ascend-users-zara"),
            self._ns("ascend-users-alice"),
        ]
        core_v1.list_namespace.return_value = ns_list

        result = list_user_namespaces(core_v1=core_v1)
        assert result == ["ascend-users-alice", "ascend-users-zara"]

    def test_returns_empty_on_api_error(self):
        from ascend.cloud.kubernetes.namespace import list_user_namespaces

        core_v1 = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        resp.data = b""
        core_v1.list_namespace.side_effect = ApiException(http_resp=resp)

        assert list_user_namespaces(core_v1=core_v1) == []

    def test_returns_empty_when_no_matches(self):
        from ascend.cloud.kubernetes.namespace import list_user_namespaces

        core_v1 = MagicMock()
        ns_list = MagicMock()
        ns_list.items = [self._ns("default"), self._ns("kube-system")]
        core_v1.list_namespace.return_value = ns_list

        assert list_user_namespaces(core_v1=core_v1) == []


# ---------------------------------------------------------------------------
# Layer 1 — _validate_namespace pre-flight in RemoteExecutor
# ---------------------------------------------------------------------------


class TestValidateNamespacePreFlight:
    """Tests for RemoteExecutor._validate_namespace."""

    def test_raises_when_namespace_missing(self):
        from ascend.utils.errors import ExecutionError

        # Construct a minimal RemoteExecutor-like object to test the method.
        # We import the class and monkey-patch just enough to call the method.
        with patch(
            "ascend.cloud.kubernetes.namespace.namespace_exists", return_value=False
        ):
            from ascend.runtime.executor import RemoteExecutor

            # Build a bare instance without __init__
            executor = object.__new__(RemoteExecutor)
            with pytest.raises(ExecutionError, match="does not exist"):
                executor._validate_namespace("ascend-users-ghost")

    def test_passes_when_namespace_exists(self):
        with patch(
            "ascend.cloud.kubernetes.namespace.namespace_exists", return_value=True
        ):
            from ascend.runtime.executor import RemoteExecutor

            executor = object.__new__(RemoteExecutor)
            executor._validate_namespace("ascend-users-alice")  # should not raise

    def test_skips_on_connection_error(self):
        """When K8s API is unreachable, skip validation gracefully."""
        with patch(
            "ascend.cloud.kubernetes.namespace.namespace_exists",
            side_effect=ConnectionError("unreachable"),
        ):
            from ascend.runtime.executor import RemoteExecutor

            executor = object.__new__(RemoteExecutor)
            executor._validate_namespace("ascend-users-alice")  # should not raise


# ---------------------------------------------------------------------------
# user.py — _derive_username delegates to shared utility
# ---------------------------------------------------------------------------


class TestUserDeriveUsernameDelegate:
    """Verify that _derive_username in user.py delegates to the shared utility."""

    def test_delegates_to_shared(self):
        with patch(
            "ascend.utils.naming.derive_username_from_credential",
            return_value="delegated-user",
        ):
            from ascend.cli.user import _derive_username

            assert _derive_username(MagicMock()) == "delegated-user"
