#!/usr/bin/env python3

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


APP_SLUG = "openrelix"
PREVIOUS_PUBLIC_APP_SLUG = "open" + "keepsake"
LEGACY_APP_SLUGS = (PREVIOUS_PUBLIC_APP_SLUG, "ai-personal-assets", "codex-personal-assets")
REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_PACKAGE_NAME = APP_SLUG
DEFAULT_PROJECT_VERSION = "0.0.0"
DEFAULT_LANGUAGE = "zh"
SUPPORTED_LANGUAGES = ("zh", "en")
DEFAULT_MEMORY_MODE = "integrated"
SUPPORTED_MEMORY_MODES = ("integrated", "local-only", "off")
SUPPORTED_ACTIVITY_SOURCES = ("history", "app-server", "auto")
DEFAULT_ACTIVITY_SOURCE = "auto"
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_MEMORY_SUMMARY_MAX_TOKENS = 8000
MIN_MEMORY_SUMMARY_MAX_TOKENS = 2000
MAX_MEMORY_SUMMARY_MAX_TOKENS = 20000
LANGUAGE_ALIASES = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "cn": "zh",
    "chinese": "zh",
    "中文": "zh",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
}
MEMORY_MODE_ALIASES = {
    "local": "local-only",
    "local-only": "local-only",
    "record": "local-only",
    "record-only": "local-only",
    "record-memory-only": "local-only",
    "personal": "local-only",
    "personal-only": "local-only",
    "integrated": "integrated",
    "full": "integrated",
    "host-context": "integrated",
    "codex": "integrated",
    "codex-context": "integrated",
    "use-codex-context": "integrated",
    "native": "integrated",
    "native-context": "integrated",
    "off": "off",
    "none": "off",
    "disabled": "off",
    "disable": "off",
    "false": "off",
    "0": "off",
}
ACTIVITY_SOURCE_ALIASES = {
    "history": "history",
    "cli": "history",
    "codex-cli": "history",
    "codex_cli": "history",
    "app": "app-server",
    "app-server": "app-server",
    "app_server": "app-server",
    "codex-app": "app-server",
    "codex_app": "app-server",
    "codex-app-server": "app-server",
    "codex_app_server": "app-server",
    "auto": "auto",
    "read-codex-app": "auto",
    "read_codex_app": "auto",
}
CODEX_MODEL_ALIASES = {
    "mini": DEFAULT_CODEX_MODEL,
    "gpt54mini": DEFAULT_CODEX_MODEL,
    "gpt5.4mini": DEFAULT_CODEX_MODEL,
    "gpt5.4min": DEFAULT_CODEX_MODEL,
    "gpt5.4": "gpt-5.4",
    "gpt54": "gpt-5.4",
    "gpt5.5": "gpt-5.5",
    "gpt55": "gpt-5.5",
    "gpt5.3codex": "gpt-5.3-codex",
    "gpt53codex": "gpt-5.3-codex",
}
LEGACY_REPO_STATE_ENV = "AI_ASSET_USE_REPO_STATE"
LEGACY_STATE_DIR_NAMES = (
    "registry",
    "reviews",
    "raw",
    "consolidated",
    "reports",
    "runtime",
    "log",
)
LEGACY_STATE_MARKERS = tuple(REPO_ROOT / name for name in LEGACY_STATE_DIR_NAMES)


def _expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _is_truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_language(value: Optional[str], *, strict: bool = False) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        if strict:
            raise ValueError(
                "Unsupported language: {}. Supported languages: {}".format(
                    value,
                    ", ".join(SUPPORTED_LANGUAGES),
                )
            )
        return DEFAULT_LANGUAGE

    language = LANGUAGE_ALIASES.get(text, text)
    if language in SUPPORTED_LANGUAGES:
        return language

    if strict:
        raise ValueError(
            "Unsupported language: {}. Supported languages: {}".format(
                value,
                ", ".join(SUPPORTED_LANGUAGES),
            )
        )
    return DEFAULT_LANGUAGE


def normalize_memory_mode(value: Optional[str], *, strict: bool = False) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        if strict:
            raise ValueError(
                "Unsupported memory mode: {}. Supported memory modes: {}".format(
                    value,
                    ", ".join(SUPPORTED_MEMORY_MODES),
                )
            )
        return DEFAULT_MEMORY_MODE

    memory_mode = MEMORY_MODE_ALIASES.get(text, text)
    if memory_mode in SUPPORTED_MEMORY_MODES:
        return memory_mode

    if strict:
        raise ValueError(
            "Unsupported memory mode: {}. Supported memory modes: {}".format(
                value,
                ", ".join(SUPPORTED_MEMORY_MODES),
            )
        )
    return DEFAULT_MEMORY_MODE


def normalize_memory_summary_max_tokens(value: Optional[Union[int, str]], *, strict: bool = False) -> int:
    if value is None or str(value).strip() == "":
        return DEFAULT_MEMORY_SUMMARY_MAX_TOKENS
    try:
        tokens = int(value)
    except (TypeError, ValueError) as exc:
        if strict:
            raise ValueError("memory_summary_max_tokens must be an integer") from exc
        return DEFAULT_MEMORY_SUMMARY_MAX_TOKENS

    if MIN_MEMORY_SUMMARY_MAX_TOKENS <= tokens <= MAX_MEMORY_SUMMARY_MAX_TOKENS:
        return tokens
    if strict:
        raise ValueError(
            "memory_summary_max_tokens must be between {} and {}".format(
                MIN_MEMORY_SUMMARY_MAX_TOKENS,
                MAX_MEMORY_SUMMARY_MAX_TOKENS,
            )
        )
    return min(max(tokens, MIN_MEMORY_SUMMARY_MAX_TOKENS), MAX_MEMORY_SUMMARY_MAX_TOKENS)


def normalize_activity_source(value: Optional[str], *, strict: bool = False) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        if strict:
            raise ValueError(
                "Unsupported activity source: {}. Supported activity sources: {}".format(
                    value,
                    ", ".join(SUPPORTED_ACTIVITY_SOURCES),
                )
            )
        return DEFAULT_ACTIVITY_SOURCE

    activity_source = ACTIVITY_SOURCE_ALIASES.get(text, text)
    if activity_source in SUPPORTED_ACTIVITY_SOURCES:
        return activity_source

    if strict:
        raise ValueError(
            "Unsupported activity source: {}. Supported activity sources: {}".format(
                value,
                ", ".join(SUPPORTED_ACTIVITY_SOURCES),
            )
        )
    return DEFAULT_ACTIVITY_SOURCE


def normalize_codex_model(value: Optional[str], *, strict: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        if strict:
            raise ValueError("codex_model cannot be empty")
        return DEFAULT_CODEX_MODEL

    alias_key = "".join(ch for ch in text.lower() if ch.isalnum() or ch == ".")
    if alias_key in CODEX_MODEL_ALIASES:
        return CODEX_MODEL_ALIASES[alias_key]

    if text.startswith("-") or any(ch.isspace() for ch in text):
        if strict:
            raise ValueError(
                "codex_model must be a single model id, for example {}".format(
                    DEFAULT_CODEX_MODEL
                )
            )
        return DEFAULT_CODEX_MODEL

    return text


def _round_token_budget(value: float) -> int:
    return int(round(value / 100.0) * 100)


def memory_summary_budget_from_max(max_tokens: Optional[Union[int, str]]) -> dict:
    normalized_max = normalize_memory_summary_max_tokens(max_tokens)
    target_tokens = min(normalized_max - 200, max(1200, _round_token_budget(normalized_max * 0.84)))
    warn_tokens = min(normalized_max - 100, max(target_tokens + 100, _round_token_budget(normalized_max * 0.92)))
    personal_memory_tokens = min(
        normalized_max - 500,
        max(300, _round_token_budget(normalized_max * 0.30)),
    )
    return {
        "target_tokens": target_tokens,
        "warn_tokens": warn_tokens,
        "max_tokens": normalized_max,
        "personal_memory_tokens": personal_memory_tokens,
    }


def get_memory_summary_budget(paths: Optional["RuntimePaths"] = None) -> dict:
    explicit = os.environ.get("AI_ASSET_MEMORY_SUMMARY_MAX_TOKENS")
    if explicit:
        return memory_summary_budget_from_max(explicit)

    config = load_runtime_config(paths)
    return memory_summary_budget_from_max(config.get("memory_summary_max_tokens"))


def read_project_package_metadata(repo_root: Optional[Path] = None) -> dict:
    package_json = (repo_root or REPO_ROOT) / "package.json"
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def get_project_version(
    repo_root: Optional[Path] = None,
    *,
    fallback: str = DEFAULT_PROJECT_VERSION,
) -> str:
    version = str(read_project_package_metadata(repo_root).get("version") or "").strip()
    return version or fallback


def iter_legacy_state_paths():
    for path in LEGACY_STATE_MARKERS:
        if path.exists():
            yield path


def repo_has_legacy_state() -> bool:
    for marker in iter_legacy_state_paths():
        if marker.is_dir():
            try:
                next(marker.iterdir())
            except StopIteration:
                continue
            except OSError:
                return True
            return True
        return True
    return False


def _state_root_for_slug(slug: str) -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / slug

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return _expand_path(xdg_state_home) / slug

    return home / ".local" / "state" / slug


def default_state_root() -> Path:
    explicit = os.environ.get("AI_ASSET_STATE_DIR")
    if explicit:
        return _expand_path(explicit)

    if _is_truthy(os.environ.get(LEGACY_REPO_STATE_ENV, "")) and repo_has_legacy_state():
        return REPO_ROOT

    current = _state_root_for_slug(APP_SLUG)
    for legacy_slug in LEGACY_APP_SLUGS:
        legacy = _state_root_for_slug(legacy_slug)
        if legacy.exists() and not current.exists():
            return legacy
    return current


def default_codex_home() -> Path:
    explicit = os.environ.get("CODEX_HOME")
    if explicit:
        return _expand_path(explicit)
    return Path.home() / ".codex"


def default_codex_binary() -> str:
    explicit = os.environ.get("CODEX_BIN")
    if explicit:
        return str(_expand_path(explicit))

    home = Path.home()
    candidates = [
        shutil.which("codex"),
        str(home / ".npm-global/bin/codex"),
        str(home / ".volta/bin/codex"),
        str(home / ".bun/bin/codex"),
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
    ]
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        for version_dir in sorted(nvm_root.iterdir(), reverse=True):
            candidates.append(str(version_dir / "bin" / "codex"))
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return "codex"


def default_user_skill_root() -> Path:
    explicit = os.environ.get("AI_ASSET_GLOBAL_SKILL_DIR")
    if explicit:
        return _expand_path(explicit)
    return default_codex_home() / "skills"


def default_launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def render_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def atomic_write_json(path: Path, payload) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _remove_runtime_file(path: Path) -> None:
    try:
        if not path.is_symlink() and not path.exists():
            return
        if path.is_dir() and not path.is_symlink():
            raise IsADirectoryError(str(path))
        path.unlink()
    except FileNotFoundError:
        return


def _runtime_symlink_points_to_source(link_path: Path, source: Path) -> bool:
    if not link_path.is_symlink():
        return False
    try:
        return Path(os.readlink(link_path)) == source
    except OSError:
        return False


def _ensure_runtime_symlink(source: Path, link_path: Path) -> None:
    if _runtime_symlink_points_to_source(link_path, source):
        return
    for attempt in range(2):
        _remove_runtime_file(link_path)
        try:
            link_path.symlink_to(source)
            return
        except FileExistsError:
            if _runtime_symlink_points_to_source(link_path, source):
                return
            if attempt == 0:
                continue
            raise


def _sync_runtime_text_file(source: Path, target: Path) -> None:
    try:
        if target.is_symlink():
            target.unlink()
        elif target.exists() and target.is_dir():
            raise IsADirectoryError(str(target))
    except FileNotFoundError:
        pass
    if target.exists() and target.is_dir():
        raise IsADirectoryError(str(target))
    atomic_write_text(target, source.read_text(encoding="utf-8"))
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def sync_codex_exec_home(main_codex_home: Path, exec_codex_home: Path) -> None:
    """Prepare an isolated CODEX_HOME for non-interactive Codex exec runs."""
    exec_codex_home.mkdir(parents=True, exist_ok=True)
    for name, mode in (("auth.json", "symlink"), ("config.toml", "copy")):
        source = main_codex_home / name
        target = exec_codex_home / name
        if source.exists():
            if mode == "symlink":
                _ensure_runtime_symlink(source, target)
            else:
                _sync_runtime_text_file(source, target)
            continue
        _remove_runtime_file(target)


def runtime_config_path(paths: Optional["RuntimePaths"] = None) -> Path:
    paths = paths or get_runtime_paths()
    return paths.runtime_dir / "config.json"


def load_runtime_config(paths: Optional["RuntimePaths"] = None) -> dict:
    path = runtime_config_path(paths)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def get_runtime_language(paths: Optional["RuntimePaths"] = None) -> str:
    explicit = os.environ.get("AI_ASSET_LANGUAGE")
    if explicit:
        return normalize_language(explicit)

    config = load_runtime_config(paths)
    return normalize_language(config.get("language"))


def get_memory_mode(paths: Optional["RuntimePaths"] = None) -> str:
    explicit = os.environ.get("AI_ASSET_MEMORY_MODE")
    if explicit:
        return normalize_memory_mode(explicit)

    config = load_runtime_config(paths)
    return normalize_memory_mode(config.get("memory_mode"))


def get_activity_source(paths: Optional["RuntimePaths"] = None) -> str:
    explicit = os.environ.get("OPENRELIX_ACTIVITY_SOURCE") or os.environ.get("AI_ASSET_ACTIVITY_SOURCE")
    if explicit:
        return normalize_activity_source(explicit)

    config = load_runtime_config(paths)
    return normalize_activity_source(config.get("activity_source"))


def get_codex_model(paths: Optional["RuntimePaths"] = None) -> str:
    explicit = os.environ.get("OPENRELIX_CODEX_MODEL") or os.environ.get("AI_ASSET_CODEX_MODEL")
    if explicit:
        return normalize_codex_model(explicit)

    config = load_runtime_config(paths)
    return normalize_codex_model(config.get("codex_model"))


def personal_memory_enabled(paths: Optional["RuntimePaths"] = None) -> bool:
    return get_memory_mode(paths) != "off"


def codex_context_enabled(paths: Optional["RuntimePaths"] = None) -> bool:
    return get_memory_mode(paths) == "integrated"


def write_runtime_config(
    language: Optional[str] = None,
    memory_mode: Optional[str] = None,
    activity_source: Optional[str] = None,
    codex_model: Optional[str] = None,
    memory_summary_max_tokens: Optional[Union[int, str]] = None,
    paths: Optional["RuntimePaths"] = None,
) -> dict:
    paths = paths or get_runtime_paths()
    config = load_runtime_config(paths)
    config["schema_version"] = int(config.get("schema_version") or 1)
    config["language"] = normalize_language(
        language if language is not None else config.get("language")
    )
    normalized_memory_mode = normalize_memory_mode(
        memory_mode if memory_mode is not None else config.get("memory_mode")
    )
    config["memory_mode"] = normalized_memory_mode
    config["personal_memory_enabled"] = normalized_memory_mode != "off"
    config["codex_context_enabled"] = normalized_memory_mode == "integrated"
    config["activity_source"] = normalize_activity_source(
        activity_source
        if activity_source is not None
        else config.get("activity_source")
    )
    config["codex_model"] = normalize_codex_model(
        codex_model
        if codex_model is not None
        else config.get("codex_model")
    )
    config["memory_summary_max_tokens"] = normalize_memory_summary_max_tokens(
        memory_summary_max_tokens
        if memory_summary_max_tokens is not None
        else config.get("memory_summary_max_tokens")
    )
    atomic_write_json(runtime_config_path(paths), config)
    return config


@dataclass(frozen=True)
class RuntimePaths:
    repo_root: Path
    state_root: Path
    codex_home: Path
    codex_bin: str
    repo_skill_root: Path
    user_skill_root: Path
    templates_dir: Path
    raw_dir: Path
    raw_daily_dir: Path
    raw_windows_dir: Path
    registry_dir: Path
    reviews_dir: Path
    reports_dir: Path
    consolidated_dir: Path
    consolidated_daily_dir: Path
    runtime_dir: Path
    nightly_runner_dir: Path
    nightly_codex_home: Path
    log_dir: Path
    launch_agents_dir: Path
    schema_path: Path


def get_runtime_paths() -> RuntimePaths:
    state_root = default_state_root()
    return RuntimePaths(
        repo_root=REPO_ROOT,
        state_root=state_root,
        codex_home=default_codex_home(),
        codex_bin=default_codex_binary(),
        repo_skill_root=REPO_ROOT / ".agents" / "skills",
        user_skill_root=default_user_skill_root(),
        templates_dir=REPO_ROOT / "templates",
        raw_dir=state_root / "raw",
        raw_daily_dir=state_root / "raw" / "daily",
        raw_windows_dir=state_root / "raw" / "windows",
        registry_dir=state_root / "registry",
        reviews_dir=state_root / "reviews",
        reports_dir=state_root / "reports",
        consolidated_dir=state_root / "consolidated",
        consolidated_daily_dir=state_root / "consolidated" / "daily",
        runtime_dir=state_root / "runtime",
        nightly_runner_dir=state_root / "runtime" / "nightly-runner",
        nightly_codex_home=state_root / "runtime" / "codex-nightly-home",
        log_dir=state_root / "log",
        launch_agents_dir=default_launch_agents_dir(),
        schema_path=REPO_ROOT / "templates" / "nightly-summary-schema.json",
    )


def ensure_state_layout(paths: Optional[RuntimePaths] = None) -> RuntimePaths:
    paths = paths or get_runtime_paths()
    for directory in (
        paths.raw_daily_dir,
        paths.raw_windows_dir,
        paths.registry_dir,
        paths.reviews_dir,
        paths.reports_dir,
        paths.consolidated_daily_dir,
        paths.runtime_dir,
        paths.nightly_runner_dir,
        paths.nightly_codex_home,
        paths.log_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    for file_path in (
        paths.registry_dir / "assets.jsonl",
        paths.registry_dir / "usage_events.jsonl",
        paths.registry_dir / "memory_items.jsonl",
    ):
        file_path.touch(exist_ok=True)

    return paths
