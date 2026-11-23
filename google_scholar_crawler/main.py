
from scholarly import scholarly
import json
from datetime import datetime
import os


try:
    print("正在查找作者信息...")

    # 不使用免费代理，直接访问 Google Scholar
    author = scholarly.search_author_id(
        os.environ["GOOGLE_SCHOLAR_ID"]
    )

    print("正在填充作者详细信息...")
    scholarly.fill(
        author,
        sections=["basics", "indices", "counts", "publications"]
    )

    name = author["name"]
    author["updated"] = str(datetime.now())

    author["publications"] = {
        v["author_pub_id"]: v
        for v in author["publications"]
    }

    print(json.dumps(author, indent=2, ensure_ascii=False))

    print("正在创建结果目录...")
    os.makedirs("results", exist_ok=True)

    print("正在保存作者数据...")
    with open("results/gs_data.json", "w", encoding="utf-8") as outfile:
        json.dump(author, outfile, ensure_ascii=False)

    print("正在生成 Shields.io 数据...")
    shieldio_data = {
        "schemaVersion": 1,
        "label": "citations",
        "message": f"{author.get('citedby', 0)}",
    }

    print("正在保存 Shields.io 数据...")
    with open("results/gs_data_shieldsio.json", "w", encoding="utf-8") as outfile:
        json.dump(shieldio_data, outfile, ensure_ascii=False)

    print("数据处理完成。")

except Exception as e:
    print(f"发生异常: {type(e).__name__}: {e}")
    raise