# Insurance Policy Manager

个人保单管理系统 — 基于开源项目拼接

## 技术栈

| 组件 | 项目 | 用途 |
|------|------|------|
| 核心平台 | [Dify](https://github.com/langgenius/dify) | RAG + LLM 问答 |
| PDF 解析 | [Docling](https://github.com/docling-project/docling) | 保单文本提取 |
| 同人识别 | LLM 辅助 | 多语言名字合并 |
| 数据库 | PostgreSQL + Weaviate | 结构化 + 向量存储 |

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/santaalex/insurance-policy.git
cd insurance-policy

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填写 API Key

# 3. 启动服务
docker-compose up -d

# 4. 访问 Dify UI（完成一次性初始化配置）
open http://localhost:3000
```

## 项目结构

```
├── docker-compose.yml          # 全栈编排
├── .env.example                # 环境变量模板
├── nginx/                      # Nginx 配置
├── services/
│   ├── pdf-parser/             # PDF 解析微服务（Docling）
│   └── identity-resolver/      # 同人识别微服务
└── scripts/
    ├── upload_policy.py        # 上传保单脚本
    └── init_dify.py            # Dify 初始化脚本
```

## 部署到服务器

```bash
# 服务器端
git clone https://github.com/santaalex/insurance-policy.git /opt/insurance-policy
cd /opt/insurance-policy
cp .env.example .env
# 编辑 .env
docker-compose up -d
```
