#!/usr/bin/env python3
"""
保单上传脚本
用法: python upload_policy.py --file policy.pdf [--host http://localhost]

功能:
1. 上传 PDF 到 pdf-parser 服务
2. 获取结构化保单信息
3. 自动推送到 Dify 知识库
4. 调用 identity-resolver 归并家庭成员
"""

import argparse
import json
import sys
import httpx
from pathlib import Path


def upload_policy(pdf_path: str, host: str = "http://localhost") -> dict:
    """上传保单 PDF 并解析"""
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        print(f"❌ 文件不存在: {pdf_path}")
        sys.exit(1)

    print(f"📄 正在上传: {pdf_file.name}")

    with open(pdf_file, "rb") as f:
        resp = httpx.post(
            f"{host}/pdf/parse",
            files={"file": (pdf_file.name, f, "application/pdf")},
            timeout=120,  # Docling 解析可能需要较长时间
        )

    if resp.status_code != 200:
        print(f"❌ 解析失败: {resp.status_code} {resp.text}")
        sys.exit(1)

    result = resp.json()
    policy = result.get("policy", {})

    print("\n✅ 解析成功！提取到以下信息：")
    print(f"  保险公司: {policy.get('insurance_company', 'N/A')}")
    print(f"  险种:     {policy.get('insurance_type', 'N/A')}")
    print(f"  投保人:   {policy.get('policy_holder', 'N/A')}")
    print(f"  被保人:   {policy.get('insured_person', 'N/A')}")
    print(f"  保额:     {policy.get('coverage_amount', 'N/A')}")
    print(f"  保障期:   {policy.get('policy_start_date', 'N/A')} ~ {policy.get('policy_end_date', 'N/A')}")
    print(f"  地区:     {policy.get('country', 'N/A')}")

    if result.get("dify_document_id"):
        print(f"\n📚 已入库 Dify 知识库，文档 ID: {result['dify_document_id']}")
    else:
        print("\n⚠️  未配置 Dify 知识库，跳过入库（请在 .env 中设置 DIFY_DATASET_ID）")

    return result


def check_family_member(name_a: str, name_b: str, host: str = "http://localhost") -> dict:
    """检查两个名字是否是同一个人"""
    resp = httpx.post(
        f"{host}/identity/check",
        json={"name_a": name_a, "name_b": name_b},
        timeout=30,
    )
    return resp.json()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="保单上传工具")
    parser.add_argument("--file", "-f", required=True, help="保单 PDF 路径")
    parser.add_argument("--host", default="http://localhost", help="服务地址（默认 http://localhost）")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    args = parser.parse_args()

    result = upload_policy(args.file, args.host)

    if args.json:
        print("\n--- 完整 JSON ---")
        print(json.dumps(result, ensure_ascii=False, indent=2))
