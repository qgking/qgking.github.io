import ipaddress
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from scholarly import ProxyGenerator, scholarly


# Public HTTP proxy lists. These are community-maintained and may be stale.
# The script validates each proxy before asking scholarly to use it.
PROXY_LIST_URLS = [
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
]

IP_CHECK_URL = "https://api.ipify.org?format=json"
MAX_PROXY_LINES_TO_TEST = 20
MAX_LIVE_PROXIES = 5
MAX_SCHOLAR_ATTEMPTS = 3
PROXY_CHECK_TIMEOUT = 6
PROXY_LIST_TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ScholarStatsUpdater/1.0)"
}


def normalize_proxy_line(line):
    """Accept only plain IPv4:port or http://IPv4:port entries."""
    line = line.strip()

    if not line or line.startswith("#"):
        return None

    if line.lower().startswith("http://"):
        line = line[7:]
    elif "://" in line:
        # SOCKS and HTTPS proxy entries are intentionally excluded.
        return None

    # Remove a trailing slash, but reject credentials and other formats.
    line = line.rstrip("/")

    if not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}", line):
        return None

    host, port_text = line.rsplit(":", 1)

    try:
        ipaddress.IPv4Address(host)
        port = int(port_text)
    except (ipaddress.AddressValueError, ValueError):
        return None

    if not 1 <= port <= 65535:
        return None

    return f"http://{host}:{port}"


def download_proxy_candidates():
    """Download and normalize candidates from public GitHub proxy lists."""
    candidates = []
    seen = set()

    session = requests.Session()
    session.trust_env = False

    for url in PROXY_LIST_URLS:
        try:
            print(f"正在读取免费代理列表：{url}")
            response = session.get(
                url,
                headers=HEADERS,
                timeout=PROXY_LIST_TIMEOUT,
            )
            response.raise_for_status()

            for line in response.text.splitlines():
                proxy = normalize_proxy_line(line)
                if proxy and proxy not in seen:
                    seen.add(proxy)
                    candidates.append(proxy)

        except requests.RequestException as exc:
            print(f"代理列表读取失败，跳过此来源：{type(exc).__name__}: {exc}")

    session.close()

    if not candidates:
        raise RuntimeError(