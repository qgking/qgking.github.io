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
            "没有获取到有效的 HTTP 代理候选地址。"
            "可能是免费代理列表不可用，或 GitHub Runner 无法访问列表源。"
        )

    random.shuffle(candidates)
    print(f"共获取到 {len(candidates)} 个格式有效的代理候选地址。")
    return candidates[:MAX_PROXY_LINES_TO_TEST]


def proxy_has_internet_access(proxy_url):
    """Check that the proxy can complete a verified HTTPS request."""
    session = requests.Session()
    session.trust_env = False

    try:
        response = session.get(
            IP_CHECK_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            headers=HEADERS,
            timeout=PROXY_CHECK_TIMEOUT,
        )
        response.raise_for_status()
        return bool(response.json().get("ip"))
    except (requests.RequestException, ValueError):
        return False
    finally:
        session.close()


def fetch_author_through_free_proxies(author_id):
    candidates = download_proxy_candidates()

    live_proxies = []
    for index, proxy in enumerate(candidates, start=1):
        print(f"正在检测代理连通性：{index}/{len(candidates)}")

        if proxy_has_internet_access(proxy):
            live_proxies.append(proxy)
            print("发现可用 HTTPS 代理。")

        if len(live_proxies) >= MAX_LIVE_PROXIES:
            break

    if not live_proxies:
        raise RuntimeError(
            "免费代理列表中没有通过 HTTPS 连通性检测的代理。"
            "本次不更新数据，保留上一次成功结果。"
        )

    last_error = None

    for attempt, proxy_url in enumerate(
        live_proxies[:MAX_SCHOLAR_ATTEMPTS],
        start=1,
    ):
        print(
            f"正在尝试通过免费代理访问 Google Scholar "
            f"({attempt}/{min(len(live_proxies), MAX_SCHOLAR_ATTEMPTS)})..."
        )

        try:
            pg = ProxyGenerator()

            # SingleProxy tests the supplied proxy with scholarly's own
            # Google Scholar connection before it is used for the crawl.
            if not pg.SingleProxy(http=proxy_url, https=proxy_url):
                print("该代理无法通过 scholarly 的 Scholar 连通性检测，跳过。")
                continue

            scholarly.use_proxy(pg)

            author = scholarly.search_author_id(author_id)
            scholarly.fill(
                author,
                sections=["basics", "indices", "counts", "publications"],
            )

            return author

        except Exception as exc:
            last_error = exc
            print(f"该代理抓取失败：{type(exc).__name__}: {exc}")

        if attempt < min(len(live_proxies), MAX_SCHOLAR_ATTEMPTS):
            print("等待 20 秒后尝试下一个代理...")
            time.sleep(20)

    if last_error:
        raise RuntimeError(
            "已尝试的免费代理均无法成功抓取 Google Scholar。"
            "请等待下一次低频任务，不要连续手动重跑。"
            f"最后错误：{type(last_error).__name__}: {last_error}"
        ) from last_error

    raise RuntimeError(
        "已尝试的免费代理均无法成功抓取 Google Scholar。"
        "请等待下一次低频任务，不要连续手动重跑。"
    )


def validate_author(author):
    """Reject incomplete responses so old published data is not overwritten."""
    if not isinstance(author, dict):
        raise RuntimeError("Google Scholar 返回的数据格式不正确。")

    if not author.get("name"):
        raise RuntimeError("作者姓名缺失，拒绝写入不完整数据。")

    if "citedby" not in author:
        raise RuntimeError("引用次数字段缺失，拒绝写入不完整数据。")

    publications = author.get("publications")
    if not isinstance(publications, list) or not publications:
        raise RuntimeError(
            "论文列表为空或格式不正确，拒绝覆盖上一次成功数据。"
        )

    publication_map = {}
    for publication in publications:
        if not isinstance(publication, dict):
            continue

        publication_id = publication.get("author_pub_id")
        if publication_id:
            publication_map[publication_id] = publication

    if not publication_map:
        raise RuntimeError(
            "没有找到有效的 author_pub_id，拒绝覆盖上一次成功数据。"
        )

    author["publications"] = publication_map
    author["updated"] = datetime.now(timezone.utc).isoformat()

    return author


def write_json_atomically(path, data):
    """Write a temporary file first, then replace the final file."""
    path = Path(path)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as outfile:
        json.dump(data, outfile, ensure_ascii=False)

    os.replace(temp_path, path)


def main():
    author_id = os.environ.get("GOOGLE_SCHOLAR_ID", "").strip()

    if not author_id:
        raise RuntimeError(
            "环境变量 GOOGLE_SCHOLAR_ID 为空。请检查 GitHub Secrets。"
        )

    print("正在查找作者信息...")
    author = fetch_author_through_free_proxies(author_id)
    author = validate_author(author)

    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)

    shieldio_data = {
        "schemaVersion": 1,
        "label": "citations",
        "message": str(author.get("citedby", 0)),
    }

    print(f"作者：{author['name']}")
    print(f"总引用次数：{author.get('citedby', 0)}")
    print(f"论文数量：{len(author['publications'])}")

    # Only write files after the complete fetch and validation succeeded.
    write_json_atomically(results_dir / "gs_data.json", author)
    write_json_atomically(
        results_dir / "gs_data_shieldsio.json",
        shieldio_data,
    )

    print("数据抓取、验证和保存全部完成。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"程序失败：{type(exc).__name__}: {exc}")
        raise
