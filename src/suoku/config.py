"""Private local configuration; values are parsed literally, never evaluated."""

from __future__ import annotations

import os
import secrets
import shlex
import stat
from collections.abc import Mapping
from pathlib import Path

DEFAULT_CONFIG = Path(".suoku/config.env")
DEFAULT_URL = "http://127.0.0.1:8000"
CONFIG_KEYS = frozenset(
    {
        "SUOKU_API_TOKEN",
        "SUOKU_INSIGHT_MODEL",
        "SUOKU_PROVIDER_API_KEY",
        "SUOKU_BASE_URL",
    }
)
MAX_CONFIG_BYTES = 65536


class ConfigError(ValueError):
    """A config failure whose message never contains a configuration value."""


def _check_path(path: Path) -> None:
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ConfigError("Refusing a symbolic link in the configuration path.")


def _validate_value(value: str) -> None:
    if len(value) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError(
            "Configuration values must be single-line text without control characters."
        )


def load_config(
    path: str | Path = DEFAULT_CONFIG, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Read a config explicitly. Process environment wins, including empty values.

    A missing file is allowed for deployments configured entirely through environment
    variables. Shell quoting is supported, but expansion and execution never occur.
    """
    path = Path(path)
    _check_path(path)
    values = {"SUOKU_BASE_URL": DEFAULT_URL}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ConfigError(
            "Cannot read configuration; check --config and file permissions."
        ) from exc
    else:
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ConfigError(
                    "Configuration must be a private regular file; run chmod 600 on it."
                )
            content = stream.read(MAX_CONFIG_BYTES + 1)
        if len(content) > MAX_CONFIG_BYTES:
            raise ConfigError("Configuration is too large; recreate it with suoku init.")
        try:
            seen = set()
            for line in content.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                name, separator, literal = line.partition("=")
                if not separator or name not in CONFIG_KEYS or name in seen:
                    raise ConfigError("Configuration contains an unknown or repeated setting.")
                seen.add(name)
                parsed = shlex.split(literal, comments=False, posix=True)
                if len(parsed) != 1:
                    raise ConfigError(
                        "Configuration values must be single, properly quoted strings."
                    )
                _validate_value(parsed[0])
                values[name] = parsed[0]
        except (ValueError, UnicodeError) as exc:
            raise ConfigError(
                "Invalid configuration; recreate it with suoku init --force."
            ) from exc
    environment = os.environ if environ is None else environ
    for name in CONFIG_KEYS:
        if name in environment:
            _validate_value(environment[name])
            values[name] = environment[name]
    return values


def _ignore_private_config(project: Path, config_path: Path) -> None:
    target = project / ".gitignore"
    try:
        fd = os.open(target, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ConfigError("Refusing to edit a linked or non-regular .gitignore.")
            content = stream.read(MAX_CONFIG_BYTES + 1)
            if len(content) > MAX_CONFIG_BYTES:
                raise ConfigError("The project .gitignore is too large to update safely.")
            patterns = [".suoku/"]
            try:
                relative = config_path.absolute().relative_to(project.absolute())
                if relative.parts[0] != ".suoku":
                    # Escaping Git's pattern characters prevents a custom path broadening the rule.
                    escaped = relative.as_posix()
                    for char in ("\\", "*", "?", "[", " ", "!", "#"):
                        escaped = escaped.replace(char, "\\" + char)
                    patterns.append("/" + escaped)
            except ValueError:
                pass
            suffix = "\n".join(patterns) + "\n"
            if content.splitlines()[-len(patterns) :] != patterns:
                stream.write(("\n" if content and not content.endswith("\n") else "") + suffix)
                stream.flush()
                os.fsync(stream.fileno())
    except OSError as exc:
        raise ConfigError(
            "Cannot safely update .gitignore; remove symbolic links and check permissions."
        ) from exc


def write_config(
    path: str | Path, values: Mapping[str, str], *, force: bool = False, project: Path | None = None
) -> Path:
    """Atomically create a private config without overwriting by default."""
    path = Path(path).absolute()
    _check_path(path)
    if path.exists() and not force:
        raise ConfigError("Configuration already exists. Use --force to replace it deliberately.")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ConfigError("Refusing to replace a linked or non-regular configuration file.")
    for name, value in values.items():
        if name not in CONFIG_KEYS:
            raise ConfigError("Unknown configuration setting.")
        _validate_value(value)
    _ignore_private_config(project or Path.cwd(), path)
    created = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".config-" + secrets.token_hex(16)
    try:
        # Custom config paths must use a private directory; never chmod a project root.
        if created or path.parent.name == ".suoku":
            os.fchmod(parent_fd, 0o700)
        elif os.fstat(parent_fd).st_mode & 0o077:
            raise ConfigError(
                "Use a private config directory (chmod 700), or the default .suoku directory."
            )
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write("# Private Suoku configuration. Loaded explicitly by the CLI.\n")
            for name, value in sorted(values.items()):
                stream.write(name + "=" + shlex.quote(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            _check_path(path)
            os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        else:
            try:
                os.link(
                    temporary,
                    path.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ConfigError(
                    "Configuration already exists. Use --force to replace it deliberately."
                ) from exc
        os.fsync(parent_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)
    return path
