from __future__ import annotations

import os
import re
import selectors
import shutil
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import unquote, urlparse

from mcp_security import redact_sensitive_text
from project_context import ProjectContextError, safe_project_path


MAX_PATHS = 100
MAX_PATH_CHARS = 4096
MAX_MESSAGE_CHARS = 160
MAX_REMOTE_URL_CHARS = 2048
MAX_PUBLIC_NAME_CHARS = 128
MAX_PUBLIC_EMAIL_CHARS = 254
MAX_STDOUT_BYTES = 64_000
MAX_STDERR_BYTES = 16_000
MAX_RAW_COMMIT_BYTES = 1_048_576
MAX_RAW_MESSAGE_BYTES = 1_048_576
MAX_REWRITE_STATUS_BYTES = 4_194_304
MAX_REWRITE_TOTAL_BYTES = 16_777_216
MAX_REWRITE_COMMITS = 100
LOCAL_TIMEOUT_SECONDS = 20.0
REMOTE_TIMEOUT_SECONDS = 30.0
MAX_METADATA_ENTRIES = 200_000
MAX_CREDENTIAL_HELPERS = 16
MAX_CREDENTIAL_HELPER_CHARS = 4096

_INHERITED_ENV_KEYS = (
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)
_CONTROLLED_GIT_ENV_KEYS = frozenset(
    {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
    }
)

_REMOTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_OBJECT_ID_RE = re.compile(r"[0-9a-fA-F]{40,64}\Z")
_LOCAL_PROCESS_CONFIG_RE = (
    r"^(credential(\..*)?\.helper|"
    r"core\.(askpass|sshcommand|gitproxy|alternaterefscommand|hookspath|pager|editor|fsmonitor)|"
    r"sequence\.editor|pager\..*|"
    r"extensions\.partialclone|remote\..*\.(uploadpack|receivepack|vcs|promisor)|"
    r"diff\.external|diff\..*\.(command|textconv)|merge\..*\.driver|"
    r"interactive\.difffilter|gpg(\..*)?\.program|submodule\..*\.update)$"
)
_FILTER_PROCESS_CONFIG_RE = r"^filter\..*\.(clean|smudge|process)$"
_URL_REWRITE_CONFIG_RE = r"^url\..*\.(insteadof|pushinsteadof)$"
_LOCAL_HTTP_TRANSPORT_CONFIG_RE = (
    r"^([hH][tT][tT][pP]\..*|"
    r"[rR][eE][mM][oO][tT][eE]\..*\."
    r"([pP][rR][oO][xX][yY]|[pP][rR][oO][xX][yY][aA][uU][tT][hH][mM][eE][tT][hH][oO][dD]))$"
)
_SAFE_GIT_BASE_CONFIG_ARGS = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.askPass=",
    "-c",
    "core.sshCommand=/bin/false",
    "-c",
    "core.editor=/bin/false",
    "-c",
    "sequence.editor=/bin/false",
    "-c",
    "core.pager=",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "commit.status=false",
    "-c",
    "commit.verbose=false",
    "-c",
    "tag.gpgSign=false",
    "-c",
    "push.gpgSign=false",
    "-c",
    "push.followTags=false",
    "-c",
    "push.recurseSubmodules=no",
    "-c",
    "fetch.recurseSubmodules=false",
    "-c",
    "submodule.recurse=false",
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
    "-c",
    "protocol.file.allow=always",
    "-c",
    "color.ui=false",
)


class SafeGitConfigError(ValueError):
    """Controlled error for missing or unsafe trusted Git configuration."""


class SafeGitOperationError(ValueError):
    """Controlled error for a denied or failed Git operation."""


@dataclass(frozen=True)
class SafeGitPolicy:
    remote_name: str = field(repr=False)
    expected_remote_url: str = field(repr=False)
    public_name: str = field(repr=False)
    public_email: str = field(repr=False)
    push_enabled: bool
    history_rewrite_enabled: bool

    def __post_init__(self) -> None:
        _validate_policy(self)

    def __repr__(self) -> str:
        return (
            "SafeGitPolicy("
            f"push_enabled={self.push_enabled!r}, "
            f"history_rewrite_enabled={self.history_rewrite_enabled!r})"
        )


@dataclass(frozen=True)
class SafeGitPreflight:
    branch: str
    head: str
    remote_name: str
    staged_paths: tuple[str, ...]
    changed_count: int
    push_enabled: bool
    remote_state: str
    remote_head: str | None
    ahead_count: int | None


@dataclass(frozen=True)
class SafeGitStageResult:
    staged_paths: tuple[str, ...]
    staged_count: int


@dataclass(frozen=True)
class SafeGitCommitResult:
    sha: str
    subject: str
    committed_paths: tuple[str, ...]


@dataclass(frozen=True)
class SafeGitPushResult:
    remote_name: str
    branch: str
    old_remote_head: str
    new_head: str
    pushed: bool


@dataclass(frozen=True)
class SafeGitRewriteResult:
    rewritten_count: int
    old_head: str
    new_head: str
    branch: str
    remote_name: str


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_valid_utf8: bool


@dataclass(frozen=True)
class _GitBytesResult:
    returncode: int
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(frozen=True, repr=False)
class _RawIdentity:
    name: bytes
    email: bytes
    timestamp: str
    timezone: str


@dataclass(frozen=True, repr=False)
class _RawCommit:
    tree: str
    parent: str
    author: _RawIdentity
    committer: _RawIdentity
    message: bytes


def preflight(root: Path, policy: SafeGitPolicy) -> SafeGitPreflight:
    """Inspect the current repository without changing its worktree, index, or refs."""

    git, repo = _prepare(root, policy, write=False)
    branch = _current_branch(git, repo, policy)
    head = _head(git, repo, policy)
    staged = _staged_paths(git, repo, policy)
    changed = _changed_paths(git, repo, policy)
    _verify_remote_url(git, repo, policy)

    remote_state = "unavailable"
    remote_head: str | None = None
    ahead_count: int | None = None
    remote = _remote_head(git, repo, policy, branch, required=False)
    if remote is None:
        remote_state = "missing"
    elif remote is not False:
        remote_state = "available"
        remote_head = remote
        if _object_exists(git, repo, policy, remote_head):
            count = _run_git(
                git,
                ["rev-list", "--count", f"{remote_head}..{head}"],
                repo,
                policy,
            )
            if (
                count.returncode == 0
                and not count.stdout_truncated
                and count.stdout_valid_utf8
                and count.stdout.strip().isdigit()
            ):
                ahead_count = int(count.stdout.strip())

    return SafeGitPreflight(
        branch=branch,
        head=head,
        remote_name=policy.remote_name,
        staged_paths=_public_paths(staged, repo, policy),
        changed_count=len(changed),
        push_enabled=policy.push_enabled,
        remote_state=remote_state,
        remote_head=remote_head,
        ahead_count=ahead_count,
    )


def stage_paths(
    root: Path,
    paths: list[str],
    policy: SafeGitPolicy,
    *,
    writable_roots: Sequence[Path] | None = None,
) -> SafeGitStageResult:
    """Stage only exact, currently changed, safe relative file paths."""

    git, repo = _prepare(root, policy, write=True, writable_roots=writable_roots)
    _current_branch(git, repo, policy)
    requested = _validate_requested_paths(repo, paths)
    changed = _changed_paths(git, repo, policy)
    unknown = [path for path in requested if path not in changed]
    if unknown:
        raise SafeGitOperationError("A requested path is not an exact currently changed file.")

    result = _run_git(
        git,
        ["--literal-pathspecs", "add", "--", *requested],
        repo,
        policy,
    )
    _require_success(result, "Git could not stage the requested paths.", repo, policy)
    staged = _staged_paths(git, repo, policy)
    return SafeGitStageResult(
        staged_paths=_public_paths(staged, repo, policy),
        staged_count=len(staged),
    )


def commit_staged(
    root: Path,
    message: str,
    policy: SafeGitPolicy,
    *,
    writable_roots: Sequence[Path] | None = None,
) -> SafeGitCommitResult:
    """Commit the current safe staged diff with the policy's fixed public identity."""

    git, repo = _prepare(root, policy, write=True, writable_roots=writable_roots)
    _current_branch(git, repo, policy)
    subject = _validate_message(message)
    staged = _staged_paths(git, repo, policy)
    if not staged:
        raise SafeGitOperationError("There are no staged changes to commit.")
    for path in staged:
        _validate_safe_relative_file(repo, path, allow_deleted=True)

    diff = _run_git(
        git,
        [
            "diff",
            "--no-ext-diff",
            "--ignore-submodules=all",
            "--cached",
            "--quiet",
            "--exit-code",
        ],
        repo,
        policy,
    )
    if diff.returncode == 0:
        raise SafeGitOperationError("There are no staged changes to commit.")
    if diff.returncode != 1:
        _require_success(diff, "Git could not inspect the staged diff.", repo, policy)

    identity_env = {
        "GIT_AUTHOR_NAME": policy.public_name,
        "GIT_AUTHOR_EMAIL": policy.public_email,
        "GIT_COMMITTER_NAME": policy.public_name,
        "GIT_COMMITTER_EMAIL": policy.public_email,
    }
    result = _run_git(
        git,
        [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "commit.cleanup=verbatim",
            "-c",
            f"user.name={policy.public_name}",
            "-c",
            f"user.email={policy.public_email}",
            "commit",
            "--no-verify",
            "--no-gpg-sign",
            "-m",
            subject,
        ],
        repo,
        policy,
        controlled_env=identity_env,
    )
    _require_success(result, "Git could not commit the staged changes.", repo, policy)
    sha = _head(git, repo, policy)
    return SafeGitCommitResult(
        sha=sha,
        subject=_sanitize_text(subject, repo, policy, MAX_MESSAGE_CHARS),
        committed_paths=_public_paths(staged, repo, policy),
    )


def push_current(
    root: Path,
    policy: SafeGitPolicy,
    *,
    writable_roots: Sequence[Path] | None = None,
) -> SafeGitPushResult:
    """Fast-forward the trusted remote's same-named branch to current HEAD."""

    git, repo = _prepare(root, policy, write=True, writable_roots=writable_roots)
    if not policy.push_enabled:
        raise SafeGitOperationError("Git push is disabled by trusted policy.")
    branch = _current_branch(git, repo, policy)
    head = _head(git, repo, policy)
    _verify_remote_url(git, repo, policy)
    remote_head = _remote_head(git, repo, policy, branch, required=True)
    assert isinstance(remote_head, str)
    if not _object_exists(git, repo, policy, remote_head):
        raise SafeGitOperationError("The remote branch head is not available in the local repository.")

    ancestor = _run_git(
        git,
        ["merge-base", "--is-ancestor", remote_head, head],
        repo,
        policy,
    )
    if ancestor.returncode == 1:
        raise SafeGitOperationError("Push rejected because the remote branch has diverged.")
    _require_success(ancestor, "Git could not verify fast-forward ancestry.", repo, policy)

    if remote_head == head:
        return SafeGitPushResult(
            remote_name=policy.remote_name,
            branch=branch,
            old_remote_head=remote_head,
            new_head=head,
            pushed=False,
        )

    # Re-pin immediately before the state-changing remote command.
    _verify_remote_url(git, repo, policy)
    if urlparse(policy.expected_remote_url).scheme != "https":
        raise SafeGitOperationError("Git push supports only a pinned HTTPS remote.")
    result = _run_git(
        git,
        [
            "-c",
            "core.hooksPath=/dev/null",
            "push",
            "--no-verify",
            "--porcelain",
            policy.expected_remote_url,
            f"HEAD:refs/heads/{branch}",
        ],
        repo,
        policy,
        timeout=REMOTE_TIMEOUT_SECONDS,
        allow_host_github_auth=True,
    )
    _require_success(result, "Git could not push the current branch.", repo, policy)
    return SafeGitPushResult(
        remote_name=policy.remote_name,
        branch=branch,
        old_remote_head=remote_head,
        new_head=head,
        pushed=True,
    )


def rewrite_unpushed_identity(
    root: Path,
    policy: SafeGitPolicy,
    *,
    writable_roots: Sequence[Path] | None = None,
) -> SafeGitRewriteResult:
    """Rewrite a bounded linear chain based on the live trusted remote head."""

    git, repo = _prepare(root, policy, write=True, writable_roots=writable_roots)
    if not policy.history_rewrite_enabled:
        raise SafeGitOperationError("Git history rewrite is disabled by trusted policy.")
    branch = _current_branch(git, repo, policy)
    old_head = _head(git, repo, policy)

    _verify_remote_url(git, repo, policy)
    remote_head = _remote_head(git, repo, policy, branch, required=True)
    assert isinstance(remote_head, str)
    if not _object_exists(git, repo, policy, remote_head):
        raise SafeGitOperationError("The remote branch head is not available in the local repository.")

    ancestor = _run_git(
        git,
        ["merge-base", "--is-ancestor", remote_head, old_head],
        repo,
        policy,
    )
    if ancestor.returncode == 1:
        raise SafeGitOperationError("History rewrite rejected because the remote branch has diverged.")
    _require_success(ancestor, "Git could not verify rewrite ancestry.", repo, policy)
    if remote_head == old_head:
        raise SafeGitOperationError("There are no unpushed commits to rewrite.")

    commits = _rewrite_candidates(git, repo, policy, remote_head, old_head)
    parsed: list[tuple[str, _RawCommit]] = []
    total_bytes = 0
    expected_parent = remote_head
    for commit_id in commits:
        raw, raw_size = _read_raw_commit(git, repo, policy, commit_id)
        total_bytes += raw_size
        if total_bytes > MAX_REWRITE_TOTAL_BYTES:
            raise SafeGitOperationError("Unpushed commit data exceeds the rewrite safety limit.")
        commit = _parse_raw_commit(raw)
        if commit.parent != expected_parent:
            raise SafeGitOperationError("Unpushed history is not a strict linear commit chain.")
        parsed.append((commit_id, commit))
        expected_parent = commit_id

    # Snapshot all mutable local state immediately before creating any objects.
    if _current_branch(git, repo, policy) != branch or _head(git, repo, policy) != old_head:
        raise SafeGitOperationError("Current branch changed during rewrite preparation.")
    branch_target = _branch_target(git, repo, policy, branch)
    if branch_target != old_head:
        raise SafeGitOperationError("Current branch changed during rewrite preparation.")
    status_before = _status_porcelain(git, repo, policy)
    old_tree = _commit_tree_id(git, repo, policy, old_head)

    new_parent = remote_head
    for _, original in parsed:
        new_commit = _write_commit_tree(git, repo, policy, original, new_parent)
        rewritten, _ = _read_raw_commit(git, repo, policy, new_commit)
        verified = _parse_raw_commit(rewritten)
        _verify_rewritten_commit(verified, original, new_parent, policy)
        new_parent = new_commit
    new_head = new_parent

    if _commit_tree_id(git, repo, policy, new_head) != old_tree:
        raise SafeGitOperationError("Rewritten history did not preserve the final tree.")

    # Recheck the live base and all local snapshots before the sole ref update.
    _verify_remote_url(git, repo, policy)
    current_remote_head = _remote_head(git, repo, policy, branch, required=True)
    if current_remote_head != remote_head:
        raise SafeGitOperationError("Remote branch changed during history rewrite.")
    if _current_branch(git, repo, policy) != branch:
        raise SafeGitOperationError("Current branch changed during history rewrite.")
    if _head(git, repo, policy) != old_head:
        raise SafeGitOperationError("Current branch changed during history rewrite.")
    if _branch_target(git, repo, policy, branch) != branch_target:
        raise SafeGitOperationError("Current branch changed during history rewrite.")
    if _status_porcelain(git, repo, policy) != status_before:
        raise SafeGitOperationError("Worktree or index changed during history rewrite.")
    _verify_remote_url(git, repo, policy)

    updated = _run_git(
        git,
        ["update-ref", f"refs/heads/{branch}", new_head, old_head],
        repo,
        policy,
    )
    _require_success(updated, "Git could not atomically update the current branch.", repo, policy)

    if _head(git, repo, policy) != new_head:
        raise SafeGitOperationError("Rewritten branch head verification failed.")
    if _commit_tree_id(git, repo, policy, new_head) != old_tree:
        raise SafeGitOperationError("Rewritten branch tree verification failed.")
    if _status_porcelain(git, repo, policy) != status_before:
        raise SafeGitOperationError("Rewritten worktree or index verification failed.")
    _verify_remote_url(git, repo, policy)
    final_remote_head = _remote_head(git, repo, policy, branch, required=True)
    if final_remote_head != remote_head:
        raise SafeGitOperationError("Remote branch changed during final rewrite verification.")
    final_ancestor = _run_git(
        git,
        ["merge-base", "--is-ancestor", remote_head, new_head],
        repo,
        policy,
    )
    if final_ancestor.returncode == 1:
        raise SafeGitOperationError("Rewritten history is not based on the remote branch.")
    _require_success(final_ancestor, "Git could not verify rewritten ancestry.", repo, policy)

    return SafeGitRewriteResult(
        rewritten_count=len(parsed),
        old_head=old_head,
        new_head=new_head,
        branch=branch,
        remote_name=policy.remote_name,
    )


def _validate_policy(policy: SafeGitPolicy) -> None:
    if not isinstance(policy, SafeGitPolicy):
        raise SafeGitConfigError("A trusted SafeGitPolicy is required.")
    if not isinstance(policy.remote_name, str) or _REMOTE_RE.fullmatch(policy.remote_name) is None:
        raise SafeGitConfigError("Trusted Git remote name is invalid.")
    _validate_bounded_text(
        policy.expected_remote_url,
        MAX_REMOTE_URL_CHARS,
        "Trusted Git remote URL is invalid.",
    )
    _validate_remote_url(policy.expected_remote_url)
    _validate_bounded_text(
        policy.public_name,
        MAX_PUBLIC_NAME_CHARS,
        "Trusted public Git name is invalid.",
    )
    _validate_bounded_text(
        policy.public_email,
        MAX_PUBLIC_EMAIL_CHARS,
        "Trusted public Git email is invalid.",
    )
    if _EMAIL_RE.fullmatch(policy.public_email) is None:
        raise SafeGitConfigError("Trusted public Git email is invalid.")
    if type(policy.push_enabled) is not bool:
        raise SafeGitConfigError("Trusted Git push policy must be a boolean.")
    if type(policy.history_rewrite_enabled) is not bool:
        raise SafeGitConfigError("Trusted Git history rewrite policy must be a boolean.")


def _validate_bounded_text(value: object, limit: int, error: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or _has_control(value)
    ):
        raise SafeGitConfigError(error)


def _prepare(
    root: Path,
    policy: SafeGitPolicy,
    *,
    write: bool = False,
    writable_roots: Sequence[Path] | None = None,
) -> tuple[str, Path]:
    _validate_policy(policy)
    found_git = shutil.which("git")
    if not found_git:
        raise SafeGitConfigError("Git executable is unavailable.")
    try:
        git_path = Path(found_git).resolve(strict=True)
        git_mode = git_path.stat().st_mode
    except (OSError, RuntimeError, ValueError) as exc:
        raise SafeGitConfigError("Git executable could not be verified.") from exc
    if not stat.S_ISREG(git_mode) or not os.access(git_path, os.X_OK):
        raise SafeGitConfigError("Git executable could not be verified.")
    git = str(git_path)
    if not isinstance(root, Path) or not root.is_absolute():
        raise SafeGitOperationError("Project root must be an already-resolved absolute Path.")
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafeGitOperationError("Project root is unavailable.") from exc
    if resolved != root or not root.is_dir():
        raise SafeGitOperationError("Project root must be an already-resolved directory.")

    top = _run_git(git, ["rev-parse", "--show-toplevel"], root, policy)
    _require_success(top, "Project root is not a Git repository.", root, policy)
    _require_parseable_stdout(top, "Git repository top-level response is invalid.")
    try:
        actual_top = Path(top.stdout.rstrip("\r\n")).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafeGitOperationError("Git repository top-level could not be verified.") from exc
    if actual_top != root:
        raise SafeGitOperationError("Project root must exactly equal the Git repository top-level.")

    git_dir = _resolved_git_metadata_path(
        git,
        root,
        policy,
        ["rev-parse", "--absolute-git-dir"],
        "Git directory could not be verified.",
    )
    common_dir = _resolved_git_metadata_path(
        git,
        root,
        policy,
        ["rev-parse", "--git-common-dir"],
        "Git common directory could not be verified.",
    )
    if write:
        authorized = _validated_writable_roots(root, writable_roots)
        _require_direct_repository_metadata(root, git_dir, common_dir)
        _require_mutable_metadata_within((git_dir, common_dir), authorized)
    _reject_process_trampolines(git, root, policy)
    return git, root


def _validate_remote_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme.casefold() == "https":
        try:
            port = parsed.port
        except ValueError as exc:
            raise SafeGitConfigError("Trusted Git remote URL must be a plain HTTPS URL.") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
            or port is not None and not 1 <= port <= 65535
        ):
            raise SafeGitConfigError("Trusted Git remote URL must be a plain HTTPS URL.")
        return
    if parsed.scheme.casefold() == "file":
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise SafeGitConfigError("Trusted local Git remote URL is invalid.")
        local_path = Path(unquote(parsed.path))
        if not local_path.is_absolute():
            raise SafeGitConfigError("Trusted local Git remote URL is invalid.")
        return
    if not parsed.scheme and Path(value).is_absolute():
        return
    raise SafeGitConfigError("Trusted Git remote must use HTTPS or an absolute local path.")


def supports_safe_push(policy: SafeGitPolicy) -> bool:
    """Return whether the validated policy names a supported state-changing remote."""

    try:
        _validate_policy(policy)
    except SafeGitConfigError:
        return False
    return urlparse(policy.expected_remote_url).scheme == "https"


def _resolved_git_metadata_path(
    git: str,
    root: Path,
    policy: SafeGitPolicy,
    args: Sequence[str],
    error: str,
) -> Path:
    result = _run_git(git, args, root, policy)
    _require_success(result, error, root, policy)
    _require_parseable_stdout(result, error)
    raw = result.stdout.rstrip("\r\n")
    if not raw or "\n" in raw or "\r" in raw:
        raise SafeGitOperationError(error)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafeGitOperationError(error) from exc
    if not resolved.is_dir():
        raise SafeGitOperationError(error)
    return resolved


def _validated_writable_roots(
    root: Path,
    writable_roots: Sequence[Path] | None,
) -> tuple[Path, ...]:
    candidates = (root,) if writable_roots is None else tuple(writable_roots)
    if not candidates:
        raise SafeGitOperationError("Git write authorization has no usable writable roots.")
    validated: list[Path] = []
    for candidate in candidates:
        if not isinstance(candidate, Path) or not candidate.is_absolute():
            raise SafeGitOperationError("Git writable-root authorization is invalid.")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SafeGitOperationError("Git writable-root authorization is invalid.") from exc
        if resolved != candidate or not resolved.is_dir():
            raise SafeGitOperationError("Git writable-root authorization is invalid.")
        if resolved not in validated:
            validated.append(resolved)
    return tuple(validated)


def _require_direct_repository_metadata(root: Path, git_dir: Path, common_dir: Path) -> None:
    """Reject linked worktrees, separate git dirs, and a symlinked top-level .git."""

    dot_git = root / ".git"
    try:
        dot_git_mode = dot_git.lstat().st_mode
        resolved_dot_git = dot_git.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SafeGitOperationError(
            "Git write rejected because repository metadata is linked or external."
        ) from exc
    if (
        not stat.S_ISDIR(dot_git_mode)
        or resolved_dot_git != dot_git
        or git_dir != dot_git
        or common_dir != git_dir
    ):
        raise SafeGitOperationError(
            "Git write rejected because repository metadata is linked or external."
        )


def _require_mutable_metadata_within(
    metadata_roots: Sequence[Path],
    writable_roots: Sequence[Path],
) -> None:
    unique_metadata = tuple(dict.fromkeys(metadata_roots))
    for metadata_root in unique_metadata:
        if not _path_within_any(metadata_root, writable_roots):
            raise SafeGitOperationError(
                "Git write rejected because mutable repository metadata is outside writable roots."
            )
    scanned = 0
    for metadata_root in unique_metadata:
        for directory, dirnames, filenames in os.walk(metadata_root, followlinks=False):
            for name in (*dirnames, *filenames):
                scanned += 1
                if scanned > MAX_METADATA_ENTRIES:
                    raise SafeGitOperationError(
                        "Git metadata is too large to verify within the safety limit."
                    )
                candidate = Path(directory) / name
                if not candidate.is_symlink():
                    continue
                try:
                    target = candidate.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise SafeGitOperationError("Git metadata contains an unsafe symbolic link.") from exc
                if not _path_within_any(target, writable_roots):
                    raise SafeGitOperationError(
                        "Git write rejected because mutable repository metadata escapes writable roots."
                    )


def _path_within_any(candidate: Path, roots: Sequence[Path]) -> bool:
    return any(candidate == root or root in candidate.parents for root in roots)


def _reject_process_trampolines(git: str, root: Path, policy: SafeGitPolicy) -> None:
    scopes = ["--local"]
    if _worktree_config_enabled(git, root, policy):
        scopes.append("--worktree")
    for scope in scopes:
        configured = _run_git(
            git,
            [
                "config",
                scope,
                "--includes",
                "--name-only",
                "--get-regexp",
                _LOCAL_PROCESS_CONFIG_RE,
            ],
            root,
            policy,
        )
        _require_no_config_matches(
            configured,
            "Repository Git configuration contains a prohibited process-launch setting.",
            root,
            policy,
        )
        configured_transport = _run_git(
            git,
            [
                "config",
                scope,
                "--includes",
                "--name-only",
                "--get-regexp",
                _LOCAL_HTTP_TRANSPORT_CONFIG_RE,
            ],
            root,
            policy,
        )
        _require_no_config_matches(
            configured_transport,
            "Repository Git configuration contains prohibited HTTP transport settings.",
            root,
            policy,
        )
    for pattern, message in (
        (_FILTER_PROCESS_CONFIG_RE, "Git content filters are unsupported by Safe Git."),
        (_URL_REWRITE_CONFIG_RE, "Git URL rewrite rules are unsupported by Safe Git."),
    ):
        configured = _run_git(
            git,
            ["config", "--includes", "--name-only", "--get-regexp", pattern],
            root,
            policy,
        )
        _require_no_config_matches(configured, message, root, policy)


def _worktree_config_enabled(git: str, root: Path, policy: SafeGitPolicy) -> bool:
    result = _run_git(
        git,
        ["config", "--local", "--includes", "--bool", "--get", "extensions.worktreeConfig"],
        root,
        policy,
    )
    if result.returncode == 1 and not result.timed_out:
        return False
    _require_success(result, "Git worktree configuration could not be inspected safely.", root, policy)
    _require_parseable_stdout(result, "Git worktree configuration is invalid.")
    value = result.stdout.rstrip("\r\n")
    if value not in {"true", "false"}:
        raise SafeGitOperationError("Git worktree configuration is invalid.")
    return value == "true"


def _require_no_config_matches(
    result: _GitResult,
    message: str,
    root: Path,
    policy: SafeGitPolicy,
) -> None:
    if result.returncode == 1 and not result.timed_out:
        return
    if result.returncode == 0:
        raise SafeGitOperationError(message)
    _require_success(result, "Git configuration could not be inspected safely.", root, policy)


def _current_branch(git: str, root: Path, policy: SafeGitPolicy) -> str:
    result = _run_git(git, ["symbolic-ref", "--short", "HEAD"], root, policy)
    if result.returncode != 0:
        raise SafeGitOperationError("Detached HEAD is not allowed.")
    _require_parseable_stdout(result, "Current Git branch response is invalid.")
    branch = result.stdout.rstrip("\r\n")
    if (
        _BRANCH_RE.fullmatch(branch) is None
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or branch.endswith(("/", ".", ".lock"))
        or any(part in {"", ".", ".."} for part in branch.split("/"))
    ):
        raise SafeGitOperationError("Current Git branch name is unsafe.")
    checked = _run_git(git, ["check-ref-format", "--branch", branch], root, policy)
    _require_success(checked, "Current Git branch name is unsafe.", root, policy)
    return branch


def _head(git: str, root: Path, policy: SafeGitPolicy) -> str:
    result = _run_git(git, ["rev-parse", "--verify", "HEAD^{commit}"], root, policy)
    _require_success(result, "Current Git HEAD is not a commit.", root, policy)
    _require_parseable_stdout(result, "Current Git HEAD is invalid.")
    return _parse_object_id(result.stdout, "Current Git HEAD is invalid.")


def _verify_remote_url(git: str, root: Path, policy: SafeGitPolicy) -> None:
    push_urls = _local_config_values(
        git,
        root,
        policy,
        f"remote.{policy.remote_name}.pushurl",
    )
    configured_urls = push_urls or _local_config_values(
        git,
        root,
        policy,
        f"remote.{policy.remote_name}.url",
    )
    if configured_urls != (policy.expected_remote_url,):
        raise SafeGitOperationError("Trusted Git remote URL does not match policy.")


def _local_config_values(
    git: str,
    root: Path,
    policy: SafeGitPolicy,
    key: str,
) -> tuple[str, ...]:
    result = _run_git(
        git,
        ["config", "--local", "--includes", "--get-all", key],
        root,
        policy,
    )
    if result.returncode == 1 and not result.timed_out:
        return ()
    _require_success(result, "Trusted Git remote is unavailable.", root, policy)
    _require_parseable_stdout(result, "Trusted Git remote response is invalid.")
    if not result.stdout:
        return ()
    values = tuple(result.stdout.splitlines())
    if not values or any(not value or _has_control(value) for value in values):
        raise SafeGitOperationError("Trusted Git remote response is invalid.")
    return values


def _remote_head(
    git: str,
    root: Path,
    policy: SafeGitPolicy,
    branch: str,
    *,
    required: bool,
) -> str | None | bool:
    result = _run_git(
        git,
        ["ls-remote", policy.expected_remote_url, f"refs/heads/{branch}"],
        root,
        policy,
        timeout=REMOTE_TIMEOUT_SECONDS,
        allow_host_github_auth=True,
    )
    if (
        result.returncode != 0
        or result.timed_out
        or result.stdout_truncated
        or not result.stdout_valid_utf8
    ):
        if required:
            _require_success(result, "Remote branch head is unavailable.", root, policy)
        return False
    line = result.stdout.rstrip("\r\n")
    if not line:
        if required:
            raise SafeGitOperationError("The same-named remote branch does not exist.")
        return None
    parts = line.split("\t")
    expected_ref = f"refs/heads/{branch}"
    if len(parts) != 2 or parts[1] != expected_ref or "\n" in line or "\r" in line:
        if required:
            raise SafeGitOperationError("Remote branch head response is invalid.")
        return False
    try:
        return _parse_object_id(parts[0], "Remote branch head response is invalid.")
    except SafeGitOperationError:
        if required:
            raise
        return False


def _object_exists(git: str, root: Path, policy: SafeGitPolicy, object_id: str) -> bool:
    result = _run_git(git, ["cat-file", "-e", f"{object_id}^{{commit}}"], root, policy)
    return result.returncode == 0


def _rewrite_candidates(
    git: str,
    root: Path,
    policy: SafeGitPolicy,
    remote_head: str,
    old_head: str,
) -> tuple[str, ...]:
    result = _run_git(
        git,
        [
            "rev-list",
            "--reverse",
            "--topo-order",
            f"--max-count={MAX_REWRITE_COMMITS + 1}",
            f"{remote_head}..{old_head}",
        ],
        root,
        policy,
    )
    _require_success(result, "Git could not enumerate unpushed commits.", root, policy)
    _require_parseable_stdout(result, "Git returned an invalid unpushed commit list.")
    lines = result.stdout.splitlines()
    if len(lines) > MAX_REWRITE_COMMITS:
        raise SafeGitOperationError("More than 100 unpushed commits cannot be rewritten.")
    if not lines:
        raise SafeGitOperationError("There are no unpushed commits to rewrite.")
    commits = tuple(
        _parse_object_id(line, "Git returned an invalid unpushed commit list.") for line in lines
    )
    if commits[-1] != old_head:
        raise SafeGitOperationError("Unpushed history is not a strict linear commit chain.")
    return commits


def _read_raw_commit(
    git: str,
    root: Path,
    policy: SafeGitPolicy,
    commit_id: str,
) -> tuple[bytes, int]:
    size_result = _run_git(git, ["cat-file", "-s", commit_id], root, policy)
    _require_success(size_result, "Git could not inspect a commit object.", root, policy)
    _require_parseable_stdout(size_result, "Git returned an invalid commit object size.")
    size_text = size_result.stdout.rstrip("\r\n")
    if not size_text.isdigit():
        raise SafeGitOperationError("Git returned an invalid commit object size.")
    size = int(size_text)
    if not 1 <= size <= MAX_RAW_COMMIT_BYTES:
        raise SafeGitOperationError("A commit object exceeds the rewrite safety limit.")

    result = _run_git_bytes(
        git,
        ["cat-file", "commit", commit_id],
        root,
        policy,
        stdout_limit=size,
    )
    _require_bytes_success(result, "Git could not read a commit object.")
    if result.stdout_truncated or len(result.stdout) != size:
        raise SafeGitOperationError("Git returned incomplete commit object data.")
    return result.stdout, size


def _parse_raw_commit(raw: bytes) -> _RawCommit:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_RAW_COMMIT_BYTES:
        raise SafeGitOperationError("Commit object data is invalid or oversized.")
    headers_blob, separator, message = raw.partition(b"\n\n")
    if not separator:
        raise SafeGitOperationError("Commit object headers are malformed.")
    if len(message) > MAX_RAW_MESSAGE_BYTES:
        raise SafeGitOperationError("Commit message exceeds the rewrite safety limit.")
    headers = headers_blob.split(b"\n")
    if len(headers) != 4 or any(line.startswith(b" ") for line in headers):
        raise SafeGitOperationError("Commit contains unsupported or nonstandard headers.")
    if not headers[0].startswith(b"tree ") or not headers[1].startswith(b"parent "):
        raise SafeGitOperationError("Commit object headers are malformed.")
    tree = _parse_raw_object_id(headers[0][5:])
    parent = _parse_raw_object_id(headers[1][7:])
    author = _parse_raw_identity(headers[2], b"author ")
    committer = _parse_raw_identity(headers[3], b"committer ")
    return _RawCommit(
        tree=tree,
        parent=parent,
        author=author,
        committer=committer,
        message=message,
    )


def _parse_raw_object_id(value: bytes) -> str:
    try:
        decoded = value.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise SafeGitOperationError("Commit object headers are malformed.") from exc
    if _OBJECT_ID_RE.fullmatch(decoded) is None:
        raise SafeGitOperationError("Commit object headers are malformed.")
    return decoded.lower()


def _parse_raw_identity(line: bytes, prefix: bytes) -> _RawIdentity:
    if not line.startswith(prefix):
        raise SafeGitOperationError("Commit identity headers are malformed.")
    parts = line[len(prefix) :].rsplit(b" ", 2)
    if len(parts) != 3:
        raise SafeGitOperationError("Commit identity headers are malformed.")
    identity, timestamp_bytes, timezone_bytes = parts
    marker = identity.rfind(b" <")
    if marker <= 0 or not identity.endswith(b">"):
        raise SafeGitOperationError("Commit identity headers are malformed.")
    name = identity[:marker]
    email = identity[marker + 2 : -1]
    if (
        not name
        or not email
        or b"<" in name
        or b">" in name
        or b"<" in email
        or b">" in email
        or any(byte < 32 or byte == 127 for byte in name + email)
        or any(byte in b" \t" for byte in email)
    ):
        raise SafeGitOperationError("Commit identity headers are malformed.")
    if re.fullmatch(rb"[0-9]+", timestamp_bytes) is None:
        raise SafeGitOperationError("Commit identity headers are malformed.")
    if re.fullmatch(rb"[+-][0-9]{4}", timezone_bytes) is None:
        raise SafeGitOperationError("Commit identity headers are malformed.")
    return _RawIdentity(
        name=name,
        email=email,
        timestamp=timestamp_bytes.decode("ascii"),
        timezone=timezone_bytes.decode("ascii"),
    )


def _write_commit_tree(
    git: str,
    root: Path,
    policy: SafeGitPolicy,
    original: _RawCommit,
    new_parent: str,
) -> str:
    if len(original.message) > MAX_RAW_MESSAGE_BYTES:
        raise SafeGitOperationError("Commit message exceeds the rewrite safety limit.")
    controlled_env = {
        "GIT_AUTHOR_NAME": policy.public_name,
        "GIT_AUTHOR_EMAIL": policy.public_email,
        "GIT_AUTHOR_DATE": f"{original.author.timestamp} {original.author.timezone}",
        "GIT_COMMITTER_NAME": policy.public_name,
        "GIT_COMMITTER_EMAIL": policy.public_email,
        "GIT_COMMITTER_DATE": f"{original.committer.timestamp} {original.committer.timezone}",
    }
    result = _run_commit_tree(
        git,
        root,
        policy,
        tree=original.tree,
        parent=new_parent,
        message=original.message,
        controlled_env=controlled_env,
    )
    _require_bytes_success(result, "Git could not create a rewritten commit object.")
    if result.stdout_truncated:
        raise SafeGitOperationError("Git returned an invalid rewritten commit identifier.")
    try:
        output = result.stdout.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise SafeGitOperationError("Git returned an invalid rewritten commit identifier.") from exc
    return _parse_object_id(output, "Git returned an invalid rewritten commit identifier.")


def _verify_rewritten_commit(
    rewritten: _RawCommit,
    original: _RawCommit,
    new_parent: str,
    policy: SafeGitPolicy,
) -> None:
    try:
        expected_name = policy.public_name.encode("utf-8", errors="strict")
        expected_email = policy.public_email.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SafeGitOperationError("Trusted public Git identity is not encodable.") from exc
    preserved = (
        rewritten.tree == original.tree
        and rewritten.parent == new_parent
        and rewritten.message == original.message
        and rewritten.author.timestamp == original.author.timestamp
        and rewritten.author.timezone == original.author.timezone
        and rewritten.committer.timestamp == original.committer.timestamp
        and rewritten.committer.timezone == original.committer.timezone
    )
    replaced = (
        rewritten.author.name == expected_name
        and rewritten.author.email == expected_email
        and rewritten.committer.name == expected_name
        and rewritten.committer.email == expected_email
    )
    if not preserved or not replaced:
        raise SafeGitOperationError("A rewritten commit did not preserve required metadata.")


def _branch_target(git: str, root: Path, policy: SafeGitPolicy, branch: str) -> str:
    result = _run_git(
        git,
        ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
        root,
        policy,
    )
    _require_success(result, "Current branch target is unavailable.", root, policy)
    _require_parseable_stdout(result, "Current branch target is invalid.")
    return _parse_object_id(result.stdout, "Current branch target is invalid.")


def _commit_tree_id(git: str, root: Path, policy: SafeGitPolicy, commit_id: str) -> str:
    result = _run_git(
        git,
        ["rev-parse", "--verify", f"{commit_id}^{{tree}}"],
        root,
        policy,
    )
    _require_success(result, "Commit tree is unavailable.", root, policy)
    _require_parseable_stdout(result, "Commit tree response is invalid.")
    return _parse_object_id(result.stdout, "Commit tree response is invalid.")


def _status_porcelain(git: str, root: Path, policy: SafeGitPolicy) -> bytes:
    result = _run_git_bytes(
        git,
        ["status", "--ignore-submodules=all", "--porcelain=v1", "-z"],
        root,
        policy,
        stdout_limit=MAX_REWRITE_STATUS_BYTES,
    )
    _require_bytes_success(result, "Git could not inspect worktree and index status.")
    if result.stdout_truncated:
        raise SafeGitOperationError("Git worktree and index status exceeds the safety limit.")
    return result.stdout


def _changed_paths(git: str, root: Path, policy: SafeGitPolicy) -> set[str]:
    changed: set[str] = set()
    for args in (
        [
            "diff",
            "--no-ext-diff",
            "--ignore-submodules=all",
            "--no-renames",
            "--name-only",
            "-z",
        ],
        [
            "diff",
            "--no-ext-diff",
            "--ignore-submodules=all",
            "--cached",
            "--no-renames",
            "--name-only",
            "-z",
        ],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ):
        result = _run_git(git, args, root, policy)
        _require_success(result, "Git could not inspect changed paths.", root, policy)
        _require_parseable_stdout(result, "Git changed-path response is incomplete or invalid.")
        changed.update(_parse_nul_paths(result.stdout))
    return changed


def _staged_paths(git: str, root: Path, policy: SafeGitPolicy) -> tuple[str, ...]:
    result = _run_git(
        git,
        [
            "diff",
            "--no-ext-diff",
            "--ignore-submodules=all",
            "--cached",
            "--no-renames",
            "--name-only",
            "-z",
        ],
        root,
        policy,
    )
    _require_success(result, "Git could not inspect staged paths.", root, policy)
    _require_parseable_stdout(result, "Git staged-path response is incomplete or invalid.")
    return tuple(sorted(set(_parse_nul_paths(result.stdout))))


def _parse_nul_paths(output: str) -> tuple[str, ...]:
    if not output:
        return ()
    if not output.endswith("\0"):
        raise SafeGitOperationError("Git returned an invalid path list.")
    paths = output[:-1].split("\0")
    if any(not path or _has_control(path) for path in paths):
        raise SafeGitOperationError("Git returned an unsafe path.")
    return tuple(paths)


def _validate_requested_paths(root: Path, paths: list[str]) -> tuple[str, ...]:
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_PATHS:
        raise SafeGitOperationError("Git stage requires between 1 and 100 paths.")
    validated: list[str] = []
    seen: set[str] = set()
    for path in paths:
        _validate_safe_relative_file(root, path, allow_deleted=True)
        if path in seen:
            raise SafeGitOperationError("Duplicate Git stage paths are not allowed.")
        seen.add(path)
        validated.append(path)
    return tuple(validated)


def _validate_safe_relative_file(root: Path, path: str, *, allow_deleted: bool) -> None:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > MAX_PATH_CHARS
        or _has_control(path)
        or Path(path).is_absolute()
        or "\\" in path
    ):
        raise SafeGitOperationError("Git path must be an exact safe relative file path.")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts) or Path(path).as_posix() != path:
        raise SafeGitOperationError("Git path must be an exact safe relative file path.")
    try:
        candidate = safe_project_path(root, path)
    except (ProjectContextError, OSError, RuntimeError) as exc:
        raise SafeGitOperationError("Git path is private, excluded, or outside the project.") from exc
    if candidate.exists() and not (candidate.is_file() or candidate.is_symlink()):
        raise SafeGitOperationError("Git path must identify a file, not a directory.")
    if not candidate.exists() and not allow_deleted:
        raise SafeGitOperationError("Git path does not identify a file.")


def _validate_message(message: str) -> str:
    if (
        not isinstance(message, str)
        or not 1 <= len(message) <= MAX_MESSAGE_CHARS
        or not message.strip()
        or _has_control(message)
        or "\n" in message
        or "\r" in message
    ):
        raise SafeGitOperationError("Commit subject must be one physical line of 1 to 160 characters.")
    return message


def _parse_object_id(value: str, error: str) -> str:
    object_id = value.rstrip("\r\n")
    if _OBJECT_ID_RE.fullmatch(object_id) is None:
        raise SafeGitOperationError(error)
    return object_id.lower()


def _public_paths(paths: Sequence[str], root: Path, policy: SafeGitPolicy) -> tuple[str, ...]:
    return tuple(_sanitize_text(path, root, policy, MAX_PATH_CHARS) for path in paths)


def _require_success(
    result: _GitResult,
    message: str,
    root: Path,
    policy: SafeGitPolicy,
) -> None:
    if result.returncode == 0 and not result.timed_out:
        return
    detail = _sanitize_text(result.stderr or result.stdout, root, policy, 500).strip()
    if detail:
        raise SafeGitOperationError(f"{message} {detail}")
    raise SafeGitOperationError(message)


def _require_parseable_stdout(result: _GitResult, message: str) -> None:
    if result.stdout_truncated or not result.stdout_valid_utf8:
        raise SafeGitOperationError(message)


def _run_git(
    git: str,
    args: Sequence[str],
    root: Path,
    policy: SafeGitPolicy,
    *,
    timeout: float = LOCAL_TIMEOUT_SECONDS,
    controlled_env: Mapping[str, str] | None = None,
    allow_host_github_auth: bool = False,
) -> _GitResult:
    trusted_helpers: tuple[str, ...] = ()
    if allow_host_github_auth and _supports_pinned_github_auth(policy):
        trusted_helpers = _host_github_credential_helpers(git, root)
    argv = [
        git,
        "--no-pager",
        *_safe_git_config_args(policy, trusted_helpers=trusted_helpers),
        *args,
    ]
    env = _git_child_env(controlled_env)

    try:
        process = subprocess.Popen(
            argv,
            cwd=root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
    except OSError as exc:
        raise SafeGitOperationError("Git process could not be started.") from exc

    stdout, stderr, stdout_truncated, stderr_truncated, timed_out = _collect_process_output(
        process,
        timeout=max(0.1, min(timeout, REMOTE_TIMEOUT_SECONDS)),
    )
    try:
        decoded_stdout = stdout.decode("utf-8", errors="strict")
        stdout_valid_utf8 = True
    except UnicodeDecodeError:
        decoded_stdout = stdout.decode("utf-8", errors="replace")
        stdout_valid_utf8 = False
    return _GitResult(
        returncode=process.returncode if process.returncode is not None else 124,
        stdout=decoded_stdout,
        stderr=stderr.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        stdout_valid_utf8=stdout_valid_utf8,
    )


def _run_git_bytes(
    git: str,
    args: Sequence[str],
    root: Path,
    policy: SafeGitPolicy,
    *,
    stdout_limit: int,
) -> _GitBytesResult:
    if not 1 <= stdout_limit <= MAX_REWRITE_STATUS_BYTES:
        raise SafeGitOperationError("Raw Git output limit is invalid.")
    try:
        process = subprocess.Popen(
            [git, "--no-pager", *_safe_git_config_args(policy), *args],
            cwd=root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_child_env(None),
            bufsize=0,
        )
    except OSError as exc:
        raise SafeGitOperationError("Git process could not be started.") from exc
    stdout, stderr, stdout_truncated, stderr_truncated, timed_out = _collect_process_output(
        process,
        timeout=LOCAL_TIMEOUT_SECONDS,
        stdout_limit=stdout_limit,
        stderr_limit=MAX_STDERR_BYTES,
    )
    return _GitBytesResult(
        returncode=process.returncode if process.returncode is not None else 124,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _run_commit_tree(
    git: str,
    root: Path,
    policy: SafeGitPolicy,
    *,
    tree: str,
    parent: str,
    message: bytes,
    controlled_env: Mapping[str, str],
) -> _GitBytesResult:
    if (
        _OBJECT_ID_RE.fullmatch(tree) is None
        or _OBJECT_ID_RE.fullmatch(parent) is None
        or not isinstance(message, bytes)
        or len(message) > MAX_RAW_MESSAGE_BYTES
    ):
        raise SafeGitOperationError("Rewritten commit input is invalid.")
    try:
        process = subprocess.Popen(
            [
                git,
                "--no-pager",
                *_safe_git_config_args(policy),
                "commit-tree",
                tree,
                "-p",
                parent,
            ],
            cwd=root,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_child_env(controlled_env),
            bufsize=0,
        )
    except OSError as exc:
        raise SafeGitOperationError("Git process could not be started.") from exc

    timed_out = False
    try:
        stdout, stderr = process.communicate(input=message, timeout=LOCAL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate()
    stdout_truncated = len(stdout) > 256
    stderr_truncated = len(stderr) > MAX_STDERR_BYTES
    return _GitBytesResult(
        returncode=process.returncode if process.returncode is not None else 124,
        stdout=stdout[:256],
        stderr=stderr[:MAX_STDERR_BYTES],
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _git_child_env(controlled_env: Mapping[str, str] | None) -> dict[str, str]:
    env = {key: os.environ[key] for key in _INHERITED_ENV_KEYS if key in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_PAGER"] = ""
    env["PAGER"] = ""
    env["GIT_EDITOR"] = "/bin/false"
    env["GIT_SEQUENCE_EDITOR"] = "/bin/false"
    env["GIT_MERGE_AUTOEDIT"] = "no"
    env["GIT_ASKPASS"] = "/bin/false"
    env["SSH_ASKPASS"] = "/bin/false"
    env["SSH_ASKPASS_REQUIRE"] = "never"
    env["GIT_SSH_COMMAND"] = "/bin/false"
    env["GIT_PROTOCOL_FROM_USER"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env["GH_PROMPT_DISABLED"] = "1"
    env["LC_ALL"] = "C"
    if controlled_env is not None:
        for key, value in controlled_env.items():
            if key not in _CONTROLLED_GIT_ENV_KEYS:
                raise SafeGitOperationError("Controlled Git environment key is not allowed.")
            if not isinstance(value, str) or not value or _has_control(value):
                raise SafeGitOperationError("Controlled Git environment value is invalid.")
        env.update(controlled_env)
    return env


def _safe_git_config_args(
    policy: SafeGitPolicy,
    *,
    trusted_helpers: Sequence[str] = (),
) -> tuple[str, ...]:
    args = list(_SAFE_GIT_BASE_CONFIG_ARGS)
    # Reset the complete lower-scope helper list first. Exact pinned GitHub
    # remote commands may then re-add only helpers read from host scopes.
    args.extend(("-c", "credential.helper="))
    if trusted_helpers and not _supports_pinned_github_auth(policy):
        raise SafeGitOperationError("Host credential helpers are not allowed for this remote.")
    for helper in trusted_helpers:
        if (
            not isinstance(helper, str)
            or len(helper) > MAX_CREDENTIAL_HELPER_CHARS
            or _has_control(helper)
        ):
            raise SafeGitOperationError("Host GitHub credential helper configuration is invalid.")
        args.extend(("-c", f"credential.helper={helper}"))
    return tuple(args)


def _supports_pinned_github_auth(policy: SafeGitPolicy) -> bool:
    parsed = urlparse(policy.expected_remote_url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "github.com"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _host_github_credential_helpers(git: str, root: Path) -> tuple[str, ...]:
    """Read only bounded system/global helpers applicable to standard github.com."""

    accepted_keys = {
        "credential.helper",
        "credential.https://github.com.helper",
    }
    helpers: list[str] = []
    for scope in ("--system", "--global"):
        try:
            process = subprocess.Popen(
                [
                    git,
                    "--no-pager",
                    "config",
                    scope,
                    "--includes",
                    "-z",
                    "--get-regexp",
                    r"^credential(\..*)?\.helper$",
                ],
                cwd=root,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_git_child_env(None),
                bufsize=0,
            )
        except OSError as exc:
            raise SafeGitOperationError(
                "Host GitHub credential helper configuration could not be inspected."
            ) from exc
        stdout, _stderr, stdout_truncated, _stderr_truncated, timed_out = (
            _collect_process_output(process, timeout=LOCAL_TIMEOUT_SECONDS)
        )
        if process.returncode == 1 and not timed_out and not stdout:
            continue
        if process.returncode != 0 or timed_out or stdout_truncated:
            raise SafeGitOperationError(
                "Host GitHub credential helper configuration could not be inspected."
            )
        for record in stdout.split(b"\0"):
            if not record:
                continue
            raw_key, separator, raw_value = record.partition(b"\n")
            if not separator:
                raise SafeGitOperationError(
                    "Host GitHub credential helper configuration is invalid."
                )
            try:
                key = raw_key.decode("utf-8", errors="strict").casefold()
                value = raw_value.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise SafeGitOperationError(
                    "Host GitHub credential helper configuration is invalid."
                ) from exc
            if key not in accepted_keys:
                continue
            if len(value) > MAX_CREDENTIAL_HELPER_CHARS or _has_control(value):
                raise SafeGitOperationError(
                    "Host GitHub credential helper configuration is invalid."
                )
            helpers.append(value)
            if len(helpers) > MAX_CREDENTIAL_HELPERS:
                raise SafeGitOperationError(
                    "Host GitHub credential helper configuration exceeds the safety limit."
                )
    return tuple(helpers)


def _require_bytes_success(result: _GitBytesResult, message: str) -> None:
    if result.returncode != 0 or result.timed_out:
        raise SafeGitOperationError(message)


def _collect_process_output(
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
    stdout_limit: int = MAX_STDOUT_BYTES,
    stderr_limit: int = MAX_STDERR_BYTES,
) -> tuple[bytes, bytes, bool, bool, bool]:
    selector = selectors.DefaultSelector()
    streams = {process.stdout: (bytearray(), stdout_limit), process.stderr: (bytearray(), stderr_limit)}
    truncated = {process.stdout: False, process.stderr: False}
    for stream in streams:
        if stream is not None:
            selector.register(stream, selectors.EVENT_READ)

    deadline = time.monotonic() + timeout
    drain_deadline: float | None = None
    timed_out = False
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0 and not timed_out:
            timed_out = True
            process.kill()
            drain_deadline = time.monotonic() + 1.0
        if drain_deadline is not None and time.monotonic() >= drain_deadline:
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
                key.fileobj.close()
            break
        events = selector.select(0.1 if timed_out else min(0.1, max(remaining, 0.0)))
        for key, _ in events:
            stream = key.fileobj
            try:
                chunk = os.read(stream.fileno(), 8192)
            except OSError:
                chunk = b""
            if not chunk:
                selector.unregister(stream)
                stream.close()
                continue
            buffer, limit = streams[stream]
            available = limit - len(buffer)
            if available > 0:
                buffer.extend(chunk[:available])
            if len(chunk) > available:
                truncated[stream] = True
    selector.close()
    process.wait()
    stdout_stream = process.stdout
    stderr_stream = process.stderr
    return (
        bytes(streams[stdout_stream][0]),
        bytes(streams[stderr_stream][0]),
        truncated[stdout_stream],
        truncated[stderr_stream],
        timed_out,
    )


def _sanitize_text(text: str, root: Path, policy: SafeGitPolicy, limit: int) -> str:
    sanitized = redact_sensitive_text(text)
    replacements = (
        (policy.expected_remote_url, "<REMOTE_URL>"),
        (policy.public_email, "<PUBLIC_EMAIL>"),
        (policy.public_name, "<PUBLIC_NAME>"),
        (str(root), "<PROJECT_ROOT>"),
        (str(Path.home()), "<HOME>"),
    )
    for private, replacement in replacements:
        if private:
            sanitized = sanitized.replace(private, replacement)
    return sanitized[:limit]


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in value)
