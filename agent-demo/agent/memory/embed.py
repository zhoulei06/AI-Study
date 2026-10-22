"""长期记忆的语义向量。

Store 直接收 LangChain Embeddings。对话仍走 DeepSeek，DeepSeek 没有 embeddings 接口，
所以向量用单独的嵌入模型。默认是本机中文模型，不另申请 key。
"""

from __future__ import annotations

import os

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings

DEFAULT_MODEL = "huggingface:BAAI/bge-small-zh-v1.5"


def build_embeddings() -> tuple[Embeddings, int, str]:
    spec = os.getenv("EMBEDDING_MODEL", "").strip() or DEFAULT_MODEL
    kwargs: dict[str, object] = {}
    if spec.startswith("huggingface:"):
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        kwargs = {
            "model_kwargs": {"device": "cpu"},
            "encode_kwargs": {"normalize_embeddings": True},
        }
    embeddings = init_embeddings(spec, **kwargs)
    dims = len(embeddings.embed_query("维度"))
    return embeddings, dims, spec
