"""Tests for job ID generation and parsing"""

import pytest
from datetime import datetime, timezone
from ascend.utils.job_ids import (
    generate_job_id,
    parse_job_id,
    validate_job_id,
    extract_metadata_from_job_id,
    _sanitize_name,
    USER_MAX_LENGTH,
    PROJECT_MAX_LENGTH,
)


class TestSanitizeName:
    """Tests for name sanitization"""
    
    def test_sanitize_simple_name(self):
        """Test sanitization of simple names"""
        assert _sanitize_name("Alice") == "alice"
        assert _sanitize_name("bob") == "bob"
        assert _sanitize_name("user-123") == "user-123"
    
    def test_sanitize_special_characters(self):
        """Test that special characters are replaced"""
        assert _sanitize_name("alice@example.com") == "alice-example-co"
        assert _sanitize_name("user_name") == "user-name"
        assert _sanitize_name("test.project") == "test-project"

    def test_sanitize_max_length(self):
        """Test max length truncation"""
        long_name = "a" * 30
        result = _sanitize_name(long_name, max_length=16)
        assert len(result) == 16
        assert result == "a" * 16

    def test_job_name_within_k8s_label_limit(self):
        """Test that generated job names stay within 63-char K8s label limit."""
        # Use worst-case long names
        job_id = generate_job_id(
            user="a" * 50,
            project="b" * 50,
        )
        job_name = f"ascend-{job_id}"
        assert len(job_name) <= 63, f"Job name too long ({len(job_name)} chars): {job_name}"
    
    def test_sanitize_consecutive_hyphens(self):
        """Test removal of consecutive hyphens"""
        assert _sanitize_name("user--name") == "user-name"
        assert _sanitize_name("test---project") == "test-project"
    
    def test_sanitize_leading_trailing_hyphens(self):
        """Test removal of leading/trailing hyphens"""
        assert _sanitize_name("-user-") == "user"
        assert _sanitize_name("---name---") == "name"
    
    def test_sanitize_empty_name(self):
        """Test that empty names become 'default'"""
        assert _sanitize_name("") == "default"
        assert _sanitize_name("!!!") == "default"


class TestGenerateJobId:
    """Tests for job ID generation"""
    
    def test_generate_basic_job_id(self):
        """Test basic job ID generation"""
        timestamp = datetime(2025, 12, 4, 14, 30, 22, tzinfo=timezone.utc)
        job_id = generate_job_id(
            user="alice",
            project="frauddetect",
            dep_hash="a3f5d2c8",
            function_name="train_model",
            args_hash="abc123",
            timestamp=timestamp,
        )
        
        # Should start with timestamp
        assert job_id.startswith("20251204-143022")
        
        # Should contain user and project (project truncated to PROJECT_MAX_LENGTH)
        assert "-alice-" in job_id
        assert "-frauddetec-" in job_id
        
        # Should contain dep hash
        assert "-a3f5d2c8-" in job_id
        
        # Should end with 8-char run hash
        parts = job_id.split("-")
        assert len(parts[-1]) == 8
    
    def test_generate_with_default_project(self):
        """Test job ID generation with default project"""
        job_id = generate_job_id(
            user="bob",
            function_name="test",
        )
        
        assert "-bob-" in job_id
        assert "-default-" in job_id
    
    def test_generate_with_none_dep_hash(self):
        """Test job ID generation with no dependency hash"""
        job_id = generate_job_id(
            user="alice",
            dep_hash=None,
        )
        
        assert "-00000000-" in job_id
    
    def test_generate_sanitizes_names(self):
        """Test that usernames and projects are sanitized"""
        job_id = generate_job_id(
            user="Alice@Example.com",
            project="My Project!",
        )
        
        # Names are truncated to USER_MAX_LENGTH and PROJECT_MAX_LENGTH
        assert f"-{_sanitize_name('Alice@Example.com', max_length=USER_MAX_LENGTH)}-" in job_id
        assert f"-{_sanitize_name('My Project!', max_length=PROJECT_MAX_LENGTH)}-" in job_id
    
    def test_generate_unique_run_hashes(self):
        """Test that consecutive job IDs have different run hashes"""
        timestamp = datetime(2025, 12, 4, 14, 30, 22, tzinfo=timezone.utc)
        
        job_id1 = generate_job_id(
            user="alice",
            function_name="test",
            timestamp=timestamp,
        )
        
        job_id2 = generate_job_id(
            user="alice",
            function_name="test",
            timestamp=timestamp,
        )
        
        # Should be different due to random nonce
        assert job_id1 != job_id2
        
        # But should have same prefix
        prefix1 = "-".join(job_id1.split("-")[:-1])
        prefix2 = "-".join(job_id2.split("-")[:-1])
        assert prefix1 == prefix2


class TestParseJobId:
    """Tests for job ID parsing"""
    
    def test_parse_valid_job_id(self):
        """Test parsing a valid job ID"""
        job_id = "20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a"
        components = parse_job_id(job_id)
        
        assert components.timestamp == "20251204-143022"
        assert components.user == "alice"
        assert components.project == "frauddetect"
        assert components.dep_hash == "a3f5d2c8"
        assert components.run_hash == "7b9e4f1a"
    
    def test_parse_with_default_project(self):
        """Test parsing job ID with default project"""
        job_id = "20251204-143022-bob-default-00000000-12345678"
        components = parse_job_id(job_id)
        
        assert components.user == "bob"
        assert components.project == "default"
        assert components.dep_hash == "00000000"
    
    def test_parse_invalid_format(self):
        """Test that invalid format raises ValueError"""
        with pytest.raises(ValueError, match="Invalid job ID format"):
            parse_job_id("invalid-job-id")
        
        with pytest.raises(ValueError):
            parse_job_id("20251204-alice-project-hash-hash")
        
        with pytest.raises(ValueError):
            parse_job_id("not-a-timestamp-alice-project-a3f5d2c8-7b9e4f1a")
    
    def test_parse_and_reconstruct(self):
        """Test that parsing and reconstructing gives same ID"""
        original_id = "20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a"
        components = parse_job_id(original_id)
        reconstructed = components.full_id
        
        assert reconstructed == original_id


class TestValidateJobId:
    """Tests for job ID validation"""
    
    def test_validate_valid_job_ids(self):
        """Test validation of valid job IDs"""
        assert validate_job_id("20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a")
        assert validate_job_id("20251204-120000-bob-default-00000000-12345678")
        assert validate_job_id("20251231-235959-u-p-ffffffff-abcdef01")
    
    def test_validate_invalid_job_ids(self):
        """Test validation of invalid job IDs"""
        assert not validate_job_id("invalid")
        assert not validate_job_id("20251204-alice-project-hash-hash")
        assert not validate_job_id("not-enough-parts")


class TestExtractMetadata:
    """Tests for metadata extraction from job IDs"""
    
    def test_extract_metadata(self):
        """Test extracting metadata from job ID"""
        job_id = "20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a"
        metadata = extract_metadata_from_job_id(job_id)
        
        assert metadata["job_id"] == job_id
        assert metadata["timestamp"] == "20251204-143022"
        assert metadata["date"] == "2025-12-04"
        assert metadata["time"] == "14:30:22"
        assert metadata["year"] == "2025"
        assert metadata["month"] == "12"
        assert metadata["day"] == "04"
        assert metadata["user"] == "alice"
        assert metadata["project"] == "frauddetect"
        assert metadata["dep_hash"] == "a3f5d2c8"
        assert metadata["run_hash"] == "7b9e4f1a"
    
    def test_extract_metadata_different_dates(self):
        """Test date extraction with various dates"""
        job_id = "20250101-000000-bob-test-12345678-abcdef01"
        metadata = extract_metadata_from_job_id(job_id)
        
        assert metadata["date"] == "2025-01-01"
        assert metadata["time"] == "00:00:00"
        assert metadata["year"] == "2025"
        assert metadata["month"] == "01"
        assert metadata["day"] == "01"


# Property-based tests using hypothesis
try:
    from hypothesis import given, strategies as st
    import string
    
    @given(
        user=st.text(
            alphabet=string.ascii_lowercase + string.digits,
            min_size=1,
            max_size=16,
        ).filter(lambda s: s and not s.startswith('-') and not s.endswith('-')),
        project=st.text(
            alphabet=string.ascii_lowercase + string.digits,
            min_size=1,
            max_size=16,
        ).filter(lambda s: s and not s.startswith('-') and not s.endswith('-')),
    )
    def test_job_id_roundtrip_property(user, project):
        """Property test: generated job IDs should be parseable"""
        # Generate job ID
        job_id = generate_job_id(user=user, project=project)
        
        # Should be valid
        assert validate_job_id(job_id)
        
        # Should be parseable
        components = parse_job_id(job_id)
        
        # User and project should match (after sanitization)
        # Note: sanitization may remove leading/trailing hyphens
        assert components.user == _sanitize_name(user, max_length=USER_MAX_LENGTH)
        assert components.project == _sanitize_name(project, max_length=PROJECT_MAX_LENGTH)
        
        # Should reconstruct to same ID
        assert components.full_id == job_id

except ImportError:
    # Hypothesis not available, skip property tests
    pass
