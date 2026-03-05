"""
Integration tests for logging and artifact system.

Tests:
- User/project isolation in storage paths
- CRUD operations (Create, Read, Update, Delete)
- Job ID generation and metadata
- Structured logging
"""

import pytest
import json
import tempfile
import os
from datetime import datetime, timezone
from hypothesis import given, strategies as st


class TestStoragePathIsolation:
    """Test that storage paths properly isolate users and projects"""
    
    def test_user_isolation(self):
        """Test that different users have isolated storage paths"""
        from ascend.storage.paths import get_job_base_path
        
        alice_path = get_job_base_path("project1", "alice", "job123")
        bob_path = get_job_base_path("project1", "bob", "job123")
        
        # Same job ID, different users should have different paths
        assert alice_path != bob_path
        assert "alice" in alice_path
        assert "bob" in bob_path
        assert "alice" not in bob_path
        assert "bob" not in alice_path
    
    def test_project_isolation(self):
        """Test that different projects have isolated storage paths"""
        from ascend.storage.paths import get_job_base_path
        
        project1_path = get_job_base_path("project1", "alice", "job123")
        project2_path = get_job_base_path("project2", "alice", "job123")
        
        # Same user, same job ID, different projects should have different paths
        assert project1_path != project2_path
        assert "project1" in project1_path
        assert "project2" in project2_path
        assert "project1" not in project2_path
        assert "project2" not in project1_path
    
    def test_complete_isolation(self):
        """Test complete isolation: different users AND projects"""
        from ascend.storage.paths import get_job_base_path
        
        paths = [
            get_job_base_path("proj1", "alice", "job1"),
            get_job_base_path("proj1", "bob", "job1"),
            get_job_base_path("proj2", "alice", "job1"),
            get_job_base_path("proj2", "bob", "job1"),
        ]
        
        # All paths should be unique
        assert len(paths) == len(set(paths))
        
        # Each path should contain correct components
        assert "proj1" in paths[0] and "alice" in paths[0]
        assert "proj1" in paths[1] and "bob" in paths[1]
        assert "proj2" in paths[2] and "alice" in paths[2]
        assert "proj2" in paths[3] and "bob" in paths[3]
    
    def test_path_structure(self):
        """Test that path structure follows expected format"""
        from ascend.storage.paths import get_job_base_path
        
        path = get_job_base_path("my-project", "alice", "20251204-143022-alice-myproject-a3f5d2c8-7b9e4f1a")
        
        # Should follow: projects/{project}/users/{user}/jobs/{job-id}
        expected = "projects/my-project/users/alice/jobs/20251204-143022-alice-myproject-a3f5d2c8-7b9e4f1a"
        assert path == expected
    
    def test_legacy_path_compatibility(self):
        """Test that legacy paths are still supported"""
        from ascend.storage.paths import get_legacy_package_path, get_legacy_result_path
        
        package_path = get_legacy_package_path("alice", "job123")
        result_path = get_legacy_result_path("alice", "job123")
        
        # Legacy paths should not include project
        assert "projects/" not in package_path
        assert "projects/" not in result_path
        
        # Legacy paths should follow old format
        assert package_path == "users/alice/jobs/job123/package.pkl"
        assert result_path == "users/alice/jobs/job123/result.pkl"


class TestMetadataCRUD:
    """Test CRUD operations on job metadata"""
    
    def test_create_metadata(self):
        """Test creating job metadata"""
        from ascend.storage.metadata import create_job_metadata
        
        metadata = create_job_metadata(
            job_id="20251204-143022-alice-default-a3f5d2c8-7b9e4f1a",
            user="alice",
            project="default",
            function_name="train_model",
            config={"cpu": "2", "memory": "4Gi"},
            packages=["numpy", "pandas"],
        )
        
        assert metadata.job_id == "20251204-143022-alice-default-a3f5d2c8-7b9e4f1a"
        assert metadata.user == "alice"
        assert metadata.project == "default"
        assert metadata.function_name == "train_model"
        assert metadata.status == "queued"
        assert len(metadata.dependencies.packages) == 2
    
    def test_read_metadata_json(self):
        """Test reading metadata from JSON"""
        from ascend.storage.metadata import create_job_metadata, JobMetadata
        
        # Create metadata
        metadata = create_job_metadata(
            job_id="test-job",
            user="alice",
            project="test",
            function_name="test_func",
            config={},
        )
        
        # Convert to JSON
        json_str = metadata.to_json()
        
        # Read back from JSON
        metadata2 = JobMetadata.from_json(json_str)
        
        assert metadata2.job_id == metadata.job_id
        assert metadata2.user == metadata.user
        assert metadata2.project == metadata.project
        assert metadata2.function_name == metadata.function_name
    
    def test_update_metadata_status(self):
        """Test updating metadata status"""
        from ascend.storage.metadata import create_job_metadata, update_metadata_status
        
        metadata = create_job_metadata(
            job_id="test-job",
            user="alice",
            project="test",
            function_name="test_func",
            config={},
        )
        
        assert metadata.status == "queued"
        
        # Update to running
        metadata = update_metadata_status(
            metadata,
            "running",
            execution_data={"start_time": "2025-12-04T14:30:00Z"}
        )
        
        assert metadata.status == "running"
        assert metadata.execution.start_time == "2025-12-04T14:30:00Z"
        
        # Update to completed
        metadata = update_metadata_status(
            metadata,
            "completed",
            execution_data={
                "end_time": "2025-12-04T14:35:00Z",
                "exit_code": 0,
            }
        )
        
        assert metadata.status == "completed"
        assert metadata.execution.end_time == "2025-12-04T14:35:00Z"
        assert metadata.execution.exit_code == 0
    
    def test_metadata_serialization_roundtrip(self):
        """Test that metadata survives serialization roundtrip"""
        from ascend.storage.metadata import create_job_metadata, JobMetadata
        
        original = create_job_metadata(
            job_id="test-job",
            user="alice",
            project="test",
            function_name="test_func",
            config={"cpu": "4", "memory": "8Gi", "node_type": "gpu_small"},
            dep_hash="a3f5d2c8",
            packages=["torch", "transformers"],
            use_gpu=True,
        )
        
        # Serialize to JSON
        json_str = original.to_json()
        
        # Deserialize
        restored = JobMetadata.from_json(json_str)
        
        # Check all fields match
        assert restored.job_id == original.job_id
        assert restored.user == original.user
        assert restored.project == original.project
        assert restored.function_name == original.function_name
        assert restored.status == original.status
        assert restored.config == original.config
        assert restored.dependencies.hash == original.dependencies.hash
        assert restored.dependencies.packages == original.dependencies.packages
        assert restored.dependencies.use_gpu == original.dependencies.use_gpu


class TestJobIDProperties:
    """Property-based tests for job ID generation"""
    
    @given(
        user=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
            min_size=1,
            max_size=16,
        ),
        project=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
            min_size=1,
            max_size=16,
        ),
    )
    def test_job_id_uniqueness(self, user, project):
        """Test that generated job IDs are unique (even for same user/project)"""
        from ascend.utils.job_ids import generate_job_id
        
        # Generate multiple job IDs with same parameters
        job_ids = [
            generate_job_id(user=user, project=project, function_name="test")
            for _ in range(10)
        ]
        
        # All should be unique (due to random nonce)
        assert len(job_ids) == len(set(job_ids))
    
    @given(
        user=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
            min_size=1,
            max_size=16,
        ),
        project=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
            min_size=1,
            max_size=16,
        ),
    )
    def test_job_id_extractable_metadata(self, user, project):
        """Test that metadata can be extracted from job ID"""
        from ascend.utils.job_ids import generate_job_id, extract_metadata_from_job_id, _sanitize_name, USER_MAX_LENGTH, PROJECT_MAX_LENGTH
        
        job_id = generate_job_id(user=user, project=project)
        metadata = extract_metadata_from_job_id(job_id)
        
        # User and project should be extractable (after sanitization with correct max lengths)
        assert metadata["user"] == _sanitize_name(user, max_length=USER_MAX_LENGTH)
        assert metadata["project"] == _sanitize_name(project, max_length=PROJECT_MAX_LENGTH)
        
        # Date should be parseable
        assert "date" in metadata
        assert "year" in metadata
        assert "month" in metadata
        assert "day" in metadata


class TestStructuredLogging:
    """Test structured logging functionality"""
    
    def test_logger_creates_valid_jsonl(self):
        """Test that logger creates valid JSON Lines output"""
        from ascend.utils.structured_logging import AscendLogger, parse_jsonl_log_file
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            logger = AscendLogger(
                job_id="test-job",
                user="alice",
                project="test",
                output_file=log_file,
                console_output=False,
            )
            
            logger.info("Test message 1")
            logger.warning("Test message 2")
            logger.error("Test message 3")
            
            # Parse log file
            entries = parse_jsonl_log_file(log_file)
            
            assert len(entries) == 3
            assert all(e["job_id"] == "test-job" for e in entries)
            assert all(e["user"] == "alice" for e in entries)
            assert all(e["project"] == "test" for e in entries)
            
            # Check log levels
            assert entries[0]["level"] == "INFO"
            assert entries[1]["level"] == "WARNING"
            assert entries[2]["level"] == "ERROR"
        
        finally:
            os.unlink(log_file)
    
    def test_logger_preserves_metadata(self):
        """Test that logger preserves context and metrics"""
        from ascend.utils.structured_logging import AscendLogger, parse_jsonl_log_file
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            logger = AscendLogger(
                job_id="test-job",
                user="alice",
                output_file=log_file,
                console_output=False,
            )
            
            logger.info(
                "Function completed",
                context={"function_name": "test_func"},
                duration_ms=5000,
                metrics={"peak_memory_mb": 2048, "gpu_utilization_pct": 75},
            )
            
            entries = parse_jsonl_log_file(log_file)
            
            assert len(entries) == 1
            entry = entries[0]
            
            assert entry["context"]["function_name"] == "test_func"
            assert entry["duration_ms"] == 5000
            assert entry["metrics"]["peak_memory_mb"] == 2048
            assert entry["metrics"]["gpu_utilization_pct"] == 75
        
        finally:
            os.unlink(log_file)


class TestIntegrationScenarios:
    """End-to-end integration test scenarios"""
    
    def test_complete_job_lifecycle(self):
        """Test complete job lifecycle: create -> run -> complete"""
        from ascend.utils.job_ids import generate_job_id, parse_job_id
        from ascend.storage.metadata import create_job_metadata, update_metadata_status
        from ascend.storage.paths import get_metadata_path, get_log_path
        
        # 1. Generate job ID
        job_id = generate_job_id(
            user="alice",
            project="testproject",
            function_name="test_func",
        )
        
        # 2. Verify job ID is parseable
        components = parse_job_id(job_id)
        assert components.user == "alice"
        assert components.project == "testprojec"  # truncated to PROJECT_MAX_LENGTH (10)
        
        # 3. Create metadata
        metadata = create_job_metadata(
            job_id=job_id,
            user="alice",
            project="testproject",
            function_name="test_func",
            config={"cpu": "2", "memory": "4Gi"},
        )
        
        assert metadata.status == "queued"
        
        # 4. Update to running
        metadata = update_metadata_status(
            metadata,
            "running",
            execution_data={"start_time": datetime.now(timezone.utc).isoformat()}
        )
        
        assert metadata.status == "running"
        
        # 5. Update to completed
        metadata = update_metadata_status(
            metadata,
            "completed",
            execution_data={
                "end_time": datetime.now(timezone.utc).isoformat(),
                "exit_code": 0,
            }
        )
        
        assert metadata.status == "completed"
        assert metadata.execution.exit_code == 0
        
        # 6. Verify paths are correct
        metadata_path = get_metadata_path("testproject", "alice", job_id)
        log_path = get_log_path("testproject", "alice", job_id)
        
        assert "testproject" in metadata_path
        assert "alice" in metadata_path
        assert job_id in metadata_path
        assert metadata_path.endswith("metadata.json")
        assert log_path.endswith("job.log.jsonl")
    
    def test_multiple_users_same_project(self):
        """Test that multiple users can work in same project without conflicts"""
        from ascend.utils.job_ids import generate_job_id
        from ascend.storage.paths import get_job_base_path
        
        # Same project, different users
        alice_job = generate_job_id(user="alice", project="shared")
        bob_job = generate_job_id(user="bob", project="shared")
        
        alice_path = get_job_base_path("shared", "alice", alice_job)
        bob_path = get_job_base_path("shared", "bob", bob_job)
        
        # Paths should be different
        assert alice_path != bob_path
        
        # Both should be in same project
        assert "shared" in alice_path
        assert "shared" in bob_path
        
        # But different user directories
        assert "/alice/" in alice_path
        assert "/bob/" in bob_path
    
    def test_same_user_multiple_projects(self):
        """Test that same user can work in multiple projects"""
        from ascend.utils.job_ids import generate_job_id
        from ascend.storage.paths import get_job_base_path
        
        # Same user, different projects
        job1 = generate_job_id(user="alice", project="project1")
        job2 = generate_job_id(user="alice", project="project2")
        
        path1 = get_job_base_path("project1", "alice", job1)
        path2 = get_job_base_path("project2", "alice", job2)
        
        # Paths should be different
        assert path1 != path2
        
        # Both should have alice
        assert "/alice/" in path1
        assert "/alice/" in path2
        
        # But different projects
        assert "project1" in path1
        assert "project2" in path2
