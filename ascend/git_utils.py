"""Git utilities for project identification and metadata"""

import subprocess
from pathlib import Path
from typing import Optional, Dict, Any


class GitError(Exception):
    """Raised when Git operations fail"""
    pass


def get_git_repo_name() -> str:
    """
    Get the Git repository name from the remote origin URL.
    
    Returns:
        Repository name (e.g., 'my-project')
        
    Raises:
        GitError: If not in a Git repository or remote is not configured
    """
    try:
        # Get the remote origin URL
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=True
        )
        
        remote_url = result.stdout.strip()
        
        # Extract repository name from URL
        # Handles: git@github.com:user/repo.git, https://github.com/user/repo.git
        repo_name = remote_url.rstrip('.git').split('/')[-1]
        
        if not repo_name:
            raise GitError("Could not extract repository name from remote URL")
        
        return repo_name
        
    except subprocess.CalledProcessError:
        raise GitError(
            "Not in a Git repository or remote 'origin' is not configured. "
            "Projects must be Git repositories with a remote origin."
        )


def is_git_tree_clean() -> bool:
    """
    Check if the Git working tree is clean (no uncommitted changes).
    
    Returns:
        True if tree is clean, False otherwise
        
    Raises:
        GitError: If not in a Git repository
    """
    try:
        # Check for uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # If output is empty, tree is clean
        return len(result.stdout.strip()) == 0
        
    except subprocess.CalledProcessError:
        raise GitError("Not in a Git repository")


def get_git_commit_metadata() -> Dict[str, Any]:
    """
    Get current Git commit metadata.
    
    Returns:
        Dictionary with commit information:
        - commit_hash: Full commit SHA
        - short_hash: Short commit SHA (7 chars)
        - branch: Current branch name
        - author: Commit author
        - timestamp: Commit timestamp
        - message: Commit message (first line)
        
    Raises:
        GitError: If not in a Git repository
    """
    try:
        # Get current commit hash
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        
        short_hash = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        
        # Get current branch
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        
        # Get commit author
        author = subprocess.run(
            ["git", "log", "-1", "--format=%an <%ae>"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        
        # Get commit timestamp
        timestamp = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        
        # Get commit message (first line)
        message = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        
        return {
            "commit_hash": commit_hash,
            "short_hash": short_hash,
            "branch": branch,
            "author": author,
            "timestamp": timestamp,
            "message": message,
        }
        
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to get Git commit metadata: {e}")


def validate_git_repository():
    """
    Validate that we're in a Git repository with a clean tree.
    
    Raises:
        GitError: If not in a Git repository or tree is not clean
    """
    # Check if in Git repository
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError:
        raise GitError(
            "Not in a Git repository. Projects must be Git repositories. "
            "Initialize with: git init && git remote add origin <url>"
        )
    
    # Check if tree is clean
    if not is_git_tree_clean():
        raise GitError(
            "Git working tree is not clean. Please commit or stash your changes before submitting a job. "
            "This ensures reproducibility and traceability of job executions."
        )
