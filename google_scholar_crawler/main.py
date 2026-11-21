"""Fetch Google Scholar author metrics for a GitHub Pages academic website.

No proxy or paid API is used. If fetching fails, exit nonzero so the
workflow leaves the previously published google-scholar-stats data intact.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from scholarly import scholarly
from scholarly._proxy_generator import MaxTriesExceededException

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 90


def fetch_author(scholar_id: str) -> dict:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"正在查找作者信息（第 {attempt}/{MAX_ATTEMPTS} 次）...", flush=True)
            author = scholarly.search_author_id(scholar_id)

            # Avoid making two batches of requests back-to-back.
            time.sleep(3)

            print("正在填充作者详细信息...", flush=True)
            author = scholarly.fill(
                author,
                sections=["basics", "indices", "counts", "publications"],
            )
            return author
        except MaxTriesExceededException as exc:
            print(f"Google Scholar 访问失败: {exc}", flush=True)
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    "Google Scholar 连续访问失败；保留远程分支中的历史数据。"
                ) from exc
            print(f"等待 {RETRY_DELAY_SECONDS} 秒后重试...", flush=True)
            time.sleep(RETRY_DELAY_SECONDS)

    raise RuntimeError("未能获得 Google Scholar 作者数据")


def main() -> None:
    scholar_id = os.environ.get("GOOGLE_SCHOLAR_ID", "").strip()
    if not scholar_id:
        raise RuntimeError("缺少 GOOGLE_SCHOLAR_ID：请检查 GitHub Actions Secret。")

    author = fetch_author(scholar_id)

    # Never publish blank or partial data as a successful update.
    if not isinstance(author, dict) or not author.get("name"):
        raise ValueError("作者数据缺失 name，拒绝覆盖历史数据。")
    if not isinstance(author.get("citedby"), int) or author["citedby"] < 0:
        raise ValueError("引用量 citedby 缺失或不是有效整数，拒绝覆盖历史数据。")
    if not isinstance(author.get("hindex"), int):
        raise ValueError("H-index 缺失，拒绝覆盖历史数据。")
    publications = author.get("publications")
    if not isinstance(publications, list):
        raise ValueError("publications 不完整，拒绝覆盖历史数据。")

    by_id = {}
    for publication in publications:
        if not isinstance(publication, dict) or not publication.get("author_pub_id"):
            raise ValueError("有论文缺少 author_pub_id，拒绝覆盖历史数据。")
        by_id[publication["author_pub_id"]] = publication
    if len(by_id) != len(publications):
        raise ValueError("论文 ID 重复，拒绝覆盖历史数据。")

    author["publications"] = by_id
    author["updated"] = str(datetime.now(timezone.utc))

    shieldio_data = {
        "schemaVersion": 1,
        "label": "citations",
        "message": str(author["citedby"]),
    }

    # Serialize before writing: serialization errors should never publish bad data.
    author_json = json.dumps(author, ensure_ascii=False)
    shield_json = json.dumps(shieldio_data, ensure_ascii=False)
    results = Path("results")
    results.mkdir(exist_ok=True)
    (results / "gs_data.json").write_text(author_json, encoding="utf-8")
    (results / "gs_data_shieldsio.json").write_text(shield_json, encoding="utf-8")

    print(
        f"数据已保存：{author['name']}，citations={author['citedby']}，"
        f"H-index={author['hindex']}，publications={len(by_id)}",
        flush=True,
    )


if __name__ == "__main__":
    main()