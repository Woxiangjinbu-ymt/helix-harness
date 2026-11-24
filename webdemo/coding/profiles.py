"""轮次受理时固定非秘密配置。凭据变更后旧引用立即失效。"""

import hashlib
import hmac
import os
import secrets
from dataclasses import asdict

from webdemo import providers


def redact(value, secret):
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[redacted]")
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, secret) for key, item in value.items()}
    return value


def credential_version(root, secret):
    keyfile = root / "credential-salt"
    if not keyfile.exists():
        private = root / (".salt-" + secrets.token_hex(12))
        try:
            with open(
                os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb"
            ) as stream:
                stream.write(os.urandom(32))
            try:
                os.link(private, keyfile)
            except FileExistsError:
                pass
        finally:
            private.unlink(missing_ok=True)
    return hmac.new(keyfile.read_bytes(), secret.encode(), hashlib.sha256).hexdigest()


def command_hooks(config):
    from mewcode.hooks.loader import load_hooks

    from .store import digest

    # 同步命令 Hook 经管理器取得自己的动作许可。
    selected = [
        raw
        for raw in config.raw_hooks
        if isinstance(raw, dict)
        and raw.get("event") in ("pre_tool_use", "post_tool_use")
        and not raw.get("async")
        and raw.get("action", {}).get("type") == "command"
    ]
    hooks = load_hooks(selected)
    if len({hook.id for hook in hooks}) != len(hooks):
        raise ValueError("Coding Hook ID 重复")
    return selected, [
        {"id": hook.id, "digest": digest(raw)} for hook, raw in zip(hooks, selected)
    ]


def snapshot(profile, root):
    config, provider = providers.select(profile)
    return {
        "schema_version": 1,
        "provider": provider.name,
        "protocol": provider.protocol,
        "endpoint": provider.base_url,
        "model": provider.model,
        "thinking": provider.thinking,
        "context_window": provider.get_context_window(),
        "max_output_tokens": provider.get_max_output_tokens(),
        "credential_ref": credential_version(root, provider.resolve_api_key()),
        "permission_mode": config.permission_mode,
        "sandbox": asdict(config.sandbox),
        "extensions": {"skills": [], "mcp": [], "hooks": command_hooks(config)[1]},
        "harness_ref": None,
        "budget": {"max_iterations": 40, "command_timeout": 3600},
    }


def capabilities(model, protocol, endpoint=""):
    from urllib.parse import urlsplit

    deepseek = urlsplit(endpoint).hostname == "api.deepseek.com"
    levels = ["default"]
    vision = False
    if (
        deepseek
        and protocol == "openai-compat"
        and model in {"deepseek-flash", "deepseek-v4-pro"}
    ):
        levels += ["off", "low", "high", "max"]
        vision = model == "deepseek-flash"
    elif protocol == "anthropic" and model.startswith(
        ("claude-sonnet-4", "claude-opus-4")
    ):
        levels += ["off", "on"]
        vision = True
    elif protocol in ("openai", "openai-compat"):
        vision = model.startswith(("gpt-4o", "gpt-4.1", "gpt-5", "o3", "o4-mini"))
        if model in ("o3", "o4-mini", "gpt-5", "gpt-5-mini"):
            levels += ["low", "medium", "high"]
    return {"thinking_levels": levels, "vision": vision}


def list_profiles(store):
    import json

    from webdemo import providers

    try:
        config = providers.configuration()
        native = [
            {
                "profile_id": p.name,
                "provider": p.name,
                "model": p.model,
                "protocol": p.protocol,
                "source": "native",
                "permission_mode": config.permission_mode,
                "context_window": p.get_context_window(),
                "window_source": "configured" if p.context_window else "runtime",
                **capabilities(p.model, p.protocol, p.base_url),
                "configured": bool(p.resolve_api_key()),
                "revision": 0,
            }
            for p in config.providers
        ]
    except (ValueError, OSError):
        native = []
    with store.connection() as db:
        rows = db.execute(
            "SELECT id,revision,document FROM coding_profiles ORDER BY rowid"
        ).fetchall()
        for key, revision, text in rows:
            d = json.loads(text)
            ref = d.pop("credential_ref")
            configured = db.execute(
                "SELECT 1 FROM credentials WHERE id=? AND revoked IS NULL", (ref,)
            ).fetchone()
            native.append(
                dict(
                    d,
                    profile_id=key,
                    revision=revision,
                    configured=bool(configured),
                    source="web",
                    window_source="configured",
                    **capabilities(d["model"], d["protocol"], d["endpoint"]),
                )
            )
    return native + [
        {
            "profile_id": "demo:local",
            "provider": "MewCode Demo",
            "model": "coding-demo",
            "source": "demo",
            "context_window": 128000,
            "window_source": "demo",
            "permission_mode": "default",
            **capabilities("coding-demo", ""),
            "configured": True,
            "revision": 1,
        }
    ]


def save_profile(store, form):
    import json
    from urllib.parse import urlsplit

    from .models import Conflict
    from .store import encode, uid

    url = urlsplit(form.endpoint)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Provider 端点须为无凭据的 HTTP(S) 地址")
    with store.connection(True) as db:
        key = form.profile_id or "web:" + uid()
        old = db.execute(
            "SELECT revision,document FROM coding_profiles WHERE id=?", (key,)
        ).fetchone()
        if form.profile_id and not old:
            raise KeyError(key)
        if old and old["revision"] != form.revision:
            raise Conflict("配置版本已更改，请重新读取")
        previous = json.loads(old["document"]) if old else {}
        ref = previous.get("credential_ref")
        if form.api_key and form.api_key.get_secret_value():
            ref = "key:" + uid()
            db.execute(
                "INSERT INTO credentials VALUES(?,?,NULL)",
                (ref, form.api_key.get_secret_value()),
            )
        elif not ref or form.endpoint != previous.get("endpoint"):
            raise ValueError("为此端点输入凭据")
        doc = form.model_dump(exclude={"api_key", "revision", "profile_id"})
        db.execute("INSERT OR IGNORE INTO profile_keys VALUES(?,?)", (key, ref))
        doc["credential_ref"] = ref
        revision = (old["revision"] if old else 0) + 1
        db.execute(
            "INSERT OR REPLACE INTO coding_profiles VALUES(?,?,?)",
            (key, revision, encode(doc)),
        )
    return {"profile_id": key, "revision": revision}


def revoke(store, key):
    import json

    from .store import now

    with store.connection(True) as db:
        row = db.execute(
            "SELECT document FROM coding_profiles WHERE id=?", (key,)
        ).fetchone()
        if not row:
            raise KeyError(key)
        ref = json.loads(row[0])["credential_ref"]
        db.execute(
            "UPDATE credentials SET secret=NULL,revoked=coalesce(revoked,?) WHERE id=? OR id IN (SELECT credential FROM profile_keys WHERE profile=?)",
            (now(), ref, key),
        )
    return {"profile_id": key, "revoked": True}


def secret_for(snap, store):
    ref = snap["credential_ref"]
    if snap.get("source") == "demo":
        return ""
    if ref.startswith("key:"):
        with store.connection() as db:
            row = db.execute(
                "SELECT secret FROM credentials WHERE id=? AND revoked IS NULL", (ref,)
            ).fetchone()
            if row:
                return row[0]
    else:
        _, p = providers.select(snap["provider"])
        secret = p.resolve_api_key()
        if credential_version(store.root, secret) == ref:
            return secret
    raise ValueError("模型凭据已撤销")


def stage_skills(base, target, errors):
    """逐层打开 Skill 来源，避免在检查与读取之间跟随替换的链接。"""
    import stat
    from contextlib import ExitStack

    from .worktrees import READ_LIMIT

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def copy_file(parent, name, dest):
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > READ_LIMIT
            ):
                raise ValueError("Skill 材料须为有界常规文件")
            content = stream.read(READ_LIMIT + 1)
            if len(content) > READ_LIMIT:
                raise ValueError("Skill 文件过大")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    with ExitStack() as stack:
        try:
            fd = os.open(base.anchor, directory_flags)
            stack.callback(os.close, fd)
            for part in base.parts[1:]:
                fd = os.open(part, directory_flags, dir_fd=fd)
                stack.callback(os.close, fd)
        except FileNotFoundError:
            return
        except OSError:
            errors.append("Skill 目录不可读取或含链接 " + base.name)
            return
        target.mkdir(parents=True, exist_ok=True)
        for name in sorted(os.listdir(fd)):
            try:
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    child = os.open(name, directory_flags, dir_fd=fd)
                    try:
                        for file in ("SKILL.md", "skill.yaml", "prompt.md"):
                            try:
                                copy_file(child, file, target / name / file)
                            except FileNotFoundError:
                                continue
                    finally:
                        os.close(child)
                elif stat.S_ISLNK(mode):
                    raise ValueError("链接不可用")
                elif name.endswith(".md"):
                    copy_file(fd, name, target / name)
            except (OSError, ValueError):
                errors.append("Skill 文件不可用 " + name)


def mcp_definitions(config, store):
    """仅解析该服务显式声明的环境和请求头，冻结到受保护的绑定。"""
    from mewcode.config import resolve_env_vars

    if not config.mcp_servers:
        return {}
    model_keys = {p.resolve_api_key() for p in config.providers}
    if store:
        with store.connection() as db:
            model_keys.update(
                r[0]
                for r in db.execute(
                    "SELECT secret FROM credentials WHERE revoked IS NULL"
                )
            )
    result = {}
    for server in config.mcp_servers:
        definition = asdict(server)
        env_fields = (server.env, server.headers)
        if not (
            all(
                isinstance(group, dict)
                and all(
                    isinstance(k, str) and isinstance(v, str) for k, v in group.items()
                )
                for group in env_fields
            )
            and isinstance(server.args, list)
            and all(isinstance(v, str) for v in server.args)
            and isinstance(server.url or "", str)
        ):
            result[server.name] = {
                "name": server.name,
                "binding_error": "MCP 启动参数、环境或请求头格式无效",
            }
            continue
        for field in ("env", "headers"):
            definition[field] = {
                k: resolve_env_vars(v) for k, v in definition[field].items()
            }
        configured = [
            *definition["env"].values(),
            *definition["headers"].values(),
            *definition["args"],
            definition["url"] or "",
        ]
        if any(key and key in value for key in model_keys for value in configured):
            definition = {
                "name": server.name,
                "binding_error": "MCP 配置引用了模型凭据",
            }
        result[server.name] = definition
    return result


def extension_catalog(directory, store=None):
    from pathlib import Path

    from mewcode.config import AppConfig
    from mewcode.skills.loader import SkillLoader

    try:
        config = providers.configuration()
    except ValueError:
        config = AppConfig()
    definitions, _ = command_hooks(config)
    result = {
        "skills": {},
        "mcp": mcp_definitions(config, store),
        "hooks": {},
        "errors": [],
    }
    from mewcode.hooks.loader import load_hooks

    result["hooks"] = {
        h.id: raw for h, raw in zip(load_hooks(definitions), definitions)
    }
    if not directory:
        return result
    # 仅向原生加载器提供验过范围的常规材料，防止随仓库链接打开其他私有文件。
    import tempfile

    from .worktrees import Worktree

    tree, _ = Worktree.identify(directory)
    with tempfile.TemporaryDirectory() as temp:
        staged = Path(temp)
        bases = [
            (Path(tree.root) / ".mewcode/skills", staged / "project/.mewcode/skills"),
            (Path.home() / ".mewcode/skills", staged / "user"),
        ]
        for base, target in bases:
            stage_skills(base, target, result["errors"])
        loader = SkillLoader(str(staged / "project"))
        loader._user_dir = staged / "user"
        skills = loader.load_all()
        result["skills"] = {
            k: s.prompt_body for k, s in skills.items() if s.prompt_body
        }
        valid = {s.source_path for s in skills.values()}
        for _, target in bases:
            if target.exists():
                for entry in target.iterdir():
                    if not any(
                        p and (p == entry or p.is_relative_to(entry)) for p in valid
                    ):
                        result["errors"].append("Skill 无法加载 " + entry.name)
    return result


def save_material(root, material):
    from .store import encode

    data = encode(material)
    key = credential_version(root, data)
    folder = root / "bindings"
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / key
    if not path.exists():
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(dir=folder, delete=False) as f:
            f.write(data.encode())
        from pathlib import Path

        Path(f.name).replace(path)
    return key


def _profile_snapshot(store, profile):
    import json

    with store.connection() as db:
        if profile.startswith("web:"):
            row = db.execute(
                "SELECT document,revision FROM coding_profiles WHERE id=?", (profile,)
            ).fetchone()
            if not row:
                raise ValueError("Coding 配置不存在")
            data = json.loads(row["document"])
            snap = {
                **data,
                "schema_version": 2,
                "source": "web",
                "profile_id": profile,
                "revision": row["revision"],
                "thinking": False,
                "max_output_tokens": 4096,
                "sandbox": {
                    "enabled": False,
                    "auto_allow": False,
                    "network_enabled": False,
                },
                "budget": {
                    "max_iterations": data["max_iterations"],
                    "command_timeout": data["command_timeout"],
                },
                "harness_ref": None,
            }
        elif profile == "demo:local":
            snap = {
                "schema_version": 2,
                "source": "demo",
                "profile_id": profile,
                "provider": "MewCode Demo",
                "protocol": "openai-compat",
                "endpoint": "local:demo",
                "model": "coding-demo",
                "thinking": False,
                "context_window": 128000,
                "max_output_tokens": 4096,
                "credential_ref": "demo",
                "permission_mode": "default",
                "sandbox": {
                    "enabled": False,
                    "auto_allow": False,
                    "network_enabled": False,
                },
                "budget": {"max_iterations": 40, "command_timeout": 3600},
                "harness_ref": None,
            }
        else:
            snap = snapshot(profile, store.root)
            snap.update(
                schema_version=2,
                source="native",
                profile_id=profile or snap["provider"],
            )
    return snap


def for_session(store, sid, profile, preferences=None):
    import json

    with store.connection() as db:
        session = store.session(sid, db)
        selected = db.execute(
            "SELECT document FROM session_options WHERE session=?", (sid,)
        ).fetchone()
    prefs = {**session.get("composer", {}), **(preferences or {})}
    snap = _profile_snapshot(store, profile)
    model_profile = prefs.get("model_profile_id")
    if model_profile and model_profile != snap["profile_id"]:
        model = _profile_snapshot(store, model_profile)
        for key in (
            "provider",
            "protocol",
            "endpoint",
            "model",
            "thinking",
            "context_window",
            "max_output_tokens",
            "credential_ref",
            "source",
            "profile_id",
            "revision",
        ):
            if key in model:
                snap[key] = model[key]
            else:
                snap.pop(key, None)
    caps = capabilities(snap["model"], snap["protocol"], snap["endpoint"])
    level = prefs.get("thinking_level", "default")
    if level not in caps["thinking_levels"]:
        raise ValueError("所选模型不支持此思考档位")
    snap.update(vision=caps["vision"], thinking_level=level)
    if level in ("on", "off"):
        snap["thinking"] = level == "on"
    if "permission_mode" in prefs:
        snap["permission_mode"] = prefs["permission_mode"]
    catalog = extension_catalog(session["directory"], store)
    options = (
        json.loads(selected[0])
        if selected
        else {"skills": [], "mcp": [], "hooks": list(catalog["hooks"])}
    )
    if "skills" in prefs:
        options["skills"] = prefs["skills"]
    material = {kind: {} for kind in ("skills", "mcp", "hooks")}
    for kind, items in material.items():
        for name in options.get(kind, []):
            if name not in catalog[kind]:
                raise ValueError(f"{kind} 无法加载 {name}")
            items[name] = catalog[kind][name]
    snap["extensions"] = {
        kind: [
            {
                "id": k,
                "digest": credential_version(
                    store.root, __import__("json").dumps(v, sort_keys=True)
                ),
            }
            for k, v in values.items()
        ]
        for kind, values in material.items()
    }
    from pathlib import Path

    from mewcode.permissions.rules import RuleEngine

    from .worktrees import Worktree

    tree, _ = Worktree.identify(session["directory"])
    root = Path(tree.root)
    rules = RuleEngine(
        user_rules_path=Path.home() / ".mewcode/permissions.yaml",
        project_rules_path=root / ".mewcode/permissions.yaml",
        local_rules_path=root / ".mewcode/permissions.local.yaml",
    )
    material["policy"] = [asdict(rule) for rule in rules.snapshot()]
    snap["bindings"] = save_material(store.root, material)
    return snap
