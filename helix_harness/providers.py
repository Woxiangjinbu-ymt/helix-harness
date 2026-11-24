"""服务端解析 Coding 使用的原生 Provider，不依赖评测目录。"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from .paths import PROVIDER_CONFIG
from mewcode.client import create_client
from mewcode.config import ConfigError, load_config


def configuration():
    path = Path(os.getenv("CODING_PROVIDER_CONFIG", PROVIDER_CONFIG)).expanduser()
    try:
        return load_config(path)
    except (ConfigError, ValueError, OSError) as exc:
        raise ValueError("Coding Provider 配置无法读取或解析") from exc


def select(profile=""):
    config = configuration()
    for provider in config.providers:
        if profile and provider.name != profile:
            continue
        url = urlsplit(provider.base_url)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Provider 端点不能含凭据或查询参数")
        return config, provider
    raise ValueError("没有可用的 Coding Provider")


def client(provider):
    return create_client(provider)
