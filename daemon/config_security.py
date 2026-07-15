from __future__ import annotations

import errno
import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from daemon.errors import ConfigurationError


MIN_AUTH_TOKEN_BYTES = 32


def authentication_token_reason(token: str | None) -> str | None:
    """Return a stable, non-secret reason when a bearer token is unusable."""
    if not isinstance(token, str) or not token:
        return "required_token_environment_variable_is_empty"
    try:
        encoded = token.encode("utf-8")
    except UnicodeEncodeError:
        return "required_token_encoding_is_invalid"
    if len(encoded) < MIN_AUTH_TOKEN_BYTES:
        return "required_token_is_too_short"
    return None


def validate_authentication_token(token: str | None, *, token_env: str) -> str:
    """Return a strong bearer token or raise without exposing token material."""
    reason = authentication_token_reason(token)
    if reason is not None:
        raise ConfigurationError(
            "Authentication token must contain at least 32 encoded bytes",
            details={
                "token_env": token_env,
                "minimum_encoded_bytes": MIN_AUTH_TOKEN_BYTES,
                "reason": reason,
            },
        )
    assert isinstance(token, str)
    return token


def load_secure_config_mapping(path: str | Path) -> Mapping[str, Any]:
    """Read TOML from the exact descriptor that passed local policy checks."""
    config_path = Path(path).expanduser()
    parent_fd = -1
    config_fd = -1
    opening = "parent"
    try:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None:
            raise _config_policy_error(config_path, "secure_open_not_supported")

        common_flags = os.O_RDONLY | os.O_CLOEXEC | nofollow | os.O_NONBLOCK
        parent_fd = os.open(
            os.fspath(config_path.parent),
            common_flags | directory,
        )
        _validate_config_parent(config_path, os.fstat(parent_fd))

        opening = "file"
        config_fd = os.open(config_path.name, common_flags, dir_fd=parent_fd)
        _validate_config_file(config_path, os.fstat(config_fd))

        with os.fdopen(config_fd, "rb", closefd=True) as stream:
            config_fd = -1
            return tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ConfigurationError(
            "Bridge configuration file was not found",
            details={"path": str(config_path)},
        ) from exc
    except ConfigurationError:
        raise
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            "Bridge configuration is not valid TOML",
            details={"path": str(config_path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            reason = (
                "config_parent_symlink_not_allowed"
                if opening == "parent"
                else "config_file_symlink_not_allowed"
            )
            raise _config_policy_error(config_path, reason) from exc
        raise ConfigurationError(
            "Bridge configuration file could not be read",
            details={"path": str(config_path), "error": type(exc).__name__},
        ) from exc
    finally:
        if config_fd >= 0:
            os.close(config_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def _validate_config_parent(config_path: Path, metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise _config_policy_error(config_path, "config_parent_not_directory")
    if metadata.st_uid != os.getuid():
        raise _config_policy_error(config_path, "config_parent_owner_mismatch")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _config_policy_error(config_path, "config_parent_permissions_unsafe")


def _validate_config_file(config_path: Path, metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise _config_policy_error(config_path, "config_file_not_regular")
    if metadata.st_uid != os.getuid():
        raise _config_policy_error(config_path, "config_file_owner_mismatch")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _config_policy_error(config_path, "config_file_permissions_unsafe")


def _config_policy_error(config_path: Path, reason: str) -> ConfigurationError:
    return ConfigurationError(
        "Bridge configuration file failed local security policy",
        details={"path": str(config_path), "reason": reason},
    )
