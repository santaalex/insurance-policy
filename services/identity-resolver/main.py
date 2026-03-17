"""
同人识别微服务
- 输入：两个人名（可能是简体/繁体/英文不同写法）
- 输出：是否是同一个人的判断 + 置信度
- 同时维护一个家庭成员档案（存在 Dify 知识库中）
"""

import os
import json
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="同人识别服务",
    description="判断不同写法的人名是否为同一人，维护家庭成员档案",
    version="1.0.0"
)

LLM_CLIENT = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ.get("LLM_API_BASE", "https://api.openai.com/v1"),
)
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
DIFY_API_BASE = os.environ.get("DIFY_API_BASE", "http://dify-api:5001")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")


# ---- 数据模型 ----

class IdentityCheckRequest(BaseModel):
    name_a: str             # 第一个名字
    name_b: str             # 第二个名字
    context_a: Optional[str] = None   # 额外上下文（如出生日期、地区）
    context_b: Optional[str] = None

class IdentityCheckResponse(BaseModel):
    is_same_person: bool
    confidence: float       # 0.0 ~ 1.0
    canonical_name: str     # 推荐使用的标准名字
    reasoning: str          # 判断理由

class FamilyMember(BaseModel):
    canonical_name: str             # 标准名
    aliases: list[str] = []         # 所有别名（简体/繁体/英文等）
    relationship: Optional[str] = None  # 与主用户的关系（妻子/儿子等）
    birth_date: Optional[str] = None
    notes: Optional[str] = None

class MergeRequest(BaseModel):
    """将保单中的人名归并到家庭成员档案"""
    policy_holder: Optional[str] = None
    insured_person: Optional[str] = None
    beneficiaries: list[str] = []
    existing_members: list[FamilyMember] = []


# ---- 核心逻辑 ----

SAME_PERSON_PROMPT = """你是一个精通中英文姓名识别的专家。
请判断以下两个名字是否可能是同一个人的不同写法。

名字A: {name_a}
{context_a_text}

名字B: {name_b}
{context_b_text}

判断规则（满足任一即为同一人）：
- 中文简体与繁体转换（如：刘 vs 劉、陈 vs 陳）
- 中文姓名与英文拼音/译名（如：王大明 vs Wong Tai Ming vs Daiming Wang）
- 粤语/普通话拼音差异（如：李美华 vs Lee Mei Wah）
- 英文名缩写（如：David Lee vs D. Lee）
- 双名与单名（如：John David Smith vs John Smith）
- 常见昵称（如：Bob vs Robert）

请以 JSON 格式返回：
{{
  "is_same_person": true/false,
  "confidence": 0.0到1.0之间的数字,
  "canonical_name": "推荐使用的标准名字（优先使用中文全名，无中文则用英文全名）",
  "reasoning": "简明判断理由（中文，1-2句）"
}}

只返回 JSON，不要其他说明。"""


FAMILY_MERGE_PROMPT = """你是家庭成员档案管理员。
以下是从一份保单中提取的人员名单，以及现有的家庭成员档案。
请判断保单中的每个人应该归并到哪个现有成员，或者是新增成员。

保单人员：
{policy_persons}

现有家庭成员档案：
{existing_members}

请以 JSON 格式返回归并结果：
{{
  "merges": [
    {{
      "policy_name": "保单中的名字",
      "action": "merge" 或 "new",
      "matched_canonical_name": "归并到的现有成员标准名（action为merge时填写）",
      "suggested_canonical_name": "建议的标准名（action为new时填写）",
      "confidence": 0.0到1.0
    }}
  ]
}}

只返回 JSON。"""


def check_same_person(name_a: str, name_b: str,
                      context_a: Optional[str] = None,
                      context_b: Optional[str] = None) -> IdentityCheckResponse:
    context_a_text = f"上下文: {context_a}" if context_a else ""
    context_b_text = f"上下文: {context_b}" if context_b else ""

    response = LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=[{
            "role": "user",
            "content": SAME_PERSON_PROMPT.format(
                name_a=name_a,
                name_b=name_b,
                context_a_text=context_a_text,
                context_b_text=context_b_text,
            )
        }],
        temperature=0,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)
    return IdentityCheckResponse(**data)


def merge_policy_persons(policy_persons: list[str],
                         existing_members: list[FamilyMember]) -> dict:
    members_text = json.dumps(
        [m.model_dump() for m in existing_members],
        ensure_ascii=False, indent=2
    ) if existing_members else "（暂无）"

    response = LLM_CLIENT.chat.completions.create(
        model=LLM_MODEL,
        messages=[{
            "role": "user",
            "content": FAMILY_MERGE_PROMPT.format(
                policy_persons="\n".join(f"- {p}" for p in policy_persons),
                existing_members=members_text,
            )
        }],
        temperature=0,
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


# ---- API 端点 ----

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/check", response_model=IdentityCheckResponse)
def check_identity(req: IdentityCheckRequest):
    """判断两个名字是否是同一个人"""
    try:
        return check_same_person(req.name_a, req.name_b, req.context_a, req.context_b)
    except Exception as e:
        logger.error(f"同人判断失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/merge")
def merge_persons(req: MergeRequest):
    """将保单人员归并到家庭成员档案"""
    # 收集所有保单中出现的人名
    policy_persons = []
    if req.policy_holder:
        policy_persons.append(f"{req.policy_holder}（投保人）")
    if req.insured_person:
        policy_persons.append(f"{req.insured_person}（被保人）")
    for b in req.beneficiaries:
        policy_persons.append(f"{b}（受益人）")

    if not policy_persons:
        raise HTTPException(status_code=400, detail="至少需要一个人名")

    try:
        result = merge_policy_persons(policy_persons, req.existing_members)
        return result
    except Exception as e:
        logger.error(f"人员归并失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
