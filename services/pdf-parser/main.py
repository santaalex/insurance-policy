"""
PDF 解析微服务
- 接收保单 PDF 上传
- 使用 Docling 提取文本（支持扫描件、中英繁体）
- 使用 LLM 提取结构化保单字段
- 将解析结果推送到 Dify 知识库
"""

import os
import json
import tempfile
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import OpenAI
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="保单 PDF 解析服务",
    description="上传保险保单 PDF，自动提取结构化信息",
    version="1.0.0"
)

# ---- 配置 ----
LLM_CLIENT = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ.get("LLM_API_BASE", "https://api.openai.com/v1"),
)
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
DIFY_API_BASE = os.environ.get("DIFY_API_BASE", "http://dify-api:5001")
DIFY_DATASET_ID = os.environ.get("DIFY_DATASET_ID", "")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")

# ---- Docling 配置（支持 OCR 扫描件）----
def get_converter():
    pipeline_options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
    pipeline_options.ocr_options.lang = ["chi_sim", "chi_tra", "eng"]
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

CONVERTER = get_converter()

# ---- 数据模型 ----
class PolicyInfo(BaseModel):
    """提取的保单核心信息"""
    policy_number: Optional[str] = None          # 保单号
    insurance_company: Optional[str] = None       # 保险公司名称
    insurance_type: Optional[str] = None          # 险种名称
    policy_holder: Optional[str] = None           # 投保人姓名
    insured_person: Optional[str] = None          # 被保人姓名
    beneficiaries: list[str] = []                 # 受益人列表
    coverage_amount: Optional[str] = None         # 保额
    premium: Optional[str] = None                 # 保费
    payment_frequency: Optional[str] = None       # 缴费方式（年缴/月缴等）
    policy_start_date: Optional[str] = None       # 保障开始日期
    policy_end_date: Optional[str] = None         # 保障结束日期
    country: Optional[str] = None                 # 保单所属国家/地区
    currency: Optional[str] = None                # 货币
    raw_text_summary: Optional[str] = None        # 原文摘要（用于 RAG）

class ParseResponse(BaseModel):
    success: bool
    policy: Optional[PolicyInfo] = None
    message: str
    dify_document_id: Optional[str] = None

# ---- 核心逻辑 ----

EXTRACTION_PROMPT = """你是一个专业的保险保单信息提取助手。
请从以下保单文本中提取核心结构化信息，以 JSON 格式返回。
支持中文（简体/繁体）、英文混合内容。

保单文本：
{text}

请提取并以 JSON 返回以下字段（找不到的填 null）：
{{
  "policy_number": "保单号/证书号",
  "insurance_company": "保险公司全称",
  "insurance_type": "险种名称（如：终身寿险、重疾险、医疗险等）",
  "policy_holder": "投保人姓名",
  "insured_person": "被保人姓名（主要被保障人）",
  "beneficiaries": ["受益人1", "受益人2"],
  "coverage_amount": "保额（含货币单位）",
  "premium": "保费金额（含货币单位和频率）",
  "payment_frequency": "缴费方式（年缴/月缴/趸缴等）",
  "policy_start_date": "保障生效日期（YYYY-MM-DD或原始格式）",
  "policy_end_date": "保障到期日期或'终身'",
  "country": "保单所属国家/地区（如：中国大陆/香港/新加坡/美国等）",
  "currency": "货币（如：CNY/HKD/SGD/USD）",
  "raw_text_summary": "用1-2句话总结该保单的核心保障内容"
}}

只返回 JSON，不要其他说明文字。"""


def extract_text_with_docling(pdf_path: str) -> str:
    """使用 Docling 解析 PDF 文本"""
    result = CONVERTER.convert(pdf_path)
    return result.document.export_to_markdown()


def extract_policy_info_with_llm(text: str) -> PolicyInfo:
    """使用 LLM 从文本中提取结构化保单信息"""
    # 截断超长文本
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[文档已截断]"

    response = LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": EXTRACTION_PROMPT.format(text=text)
            }
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    data = json.loads(raw)
    return PolicyInfo(**data)


async def push_to_dify_knowledge_base(policy: PolicyInfo, original_text: str, filename: str):
    """将保单信息推送到 Dify 知识库"""
    if not DIFY_DATASET_ID or not DIFY_API_KEY:
        logger.warning("未配置 DIFY_DATASET_ID 或 DIFY_API_KEY，跳过推送到知识库")
        return None

    # 构建知识库文档内容（包含结构化信息 + 原始文本）
    doc_content = f"""# 保单信息：{policy.insurance_company} - {policy.insurance_type}

## 基本信息
- **保单号**: {policy.policy_number}
- **保险公司**: {policy.insurance_company}
- **险种**: {policy.insurance_type}
- **所属地区**: {policy.country}
- **货币**: {policy.currency}

## 投被保人
- **投保人**: {policy.policy_holder}
- **被保人**: {policy.insured_person}
- **受益人**: {', '.join(policy.beneficiaries) if policy.beneficiaries else '未指定'}

## 保障详情
- **保额**: {policy.coverage_amount}
- **保费**: {policy.premium}
- **缴费方式**: {policy.payment_frequency}
- **保障期间**: {policy.policy_start_date} 至 {policy.policy_end_date}

## 保障摘要
{policy.raw_text_summary}

---
*原始文件: {filename}*
"""

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{DIFY_API_BASE}/v1/datasets/{DIFY_DATASET_ID}/document/create_by_text",
            headers={
                "Authorization": f"Bearer {DIFY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "name": f"{policy.policy_holder or '未知'} - {policy.insurance_company} - {policy.insurance_type}",
                "text": doc_content,
                "indexing_technique": "high_quality",
                "process_rule": {"mode": "automatic"},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("document", {}).get("id")
        else:
            logger.error(f"推送知识库失败: {resp.status_code} {resp.text}")
            return None


# ---- API 端点 ----

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/parse", response_model=ParseResponse)
async def parse_policy(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="保单 PDF 文件"),
):
    """
    上传保单 PDF，解析提取结构化信息，并自动入库到 Dify 知识库。
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 格式")

    # 保存临时文件
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        logger.info(f"开始解析: {file.filename}")

        # 1. Docling 提取文本
        text = extract_text_with_docling(tmp_path)
        logger.info(f"文本提取完成，长度: {len(text)} 字符")

        # 2. LLM 提取结构化字段
        policy = extract_policy_info_with_llm(text)
        logger.info(f"字段提取完成: {policy.insurance_company} / {policy.insured_person}")

        # 3. 异步推送到 Dify 知识库
        dify_doc_id = None
        if DIFY_DATASET_ID and DIFY_API_KEY:
            dify_doc_id = await push_to_dify_knowledge_base(policy, text, file.filename)

        return ParseResponse(
            success=True,
            policy=policy,
            message="解析成功",
            dify_document_id=dify_doc_id,
        )

    except Exception as e:
        logger.error(f"解析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"解析失败: {str(e)}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
