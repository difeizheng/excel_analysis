"""统一 LLM 客户端(OpenAI 兼容协议,零第三方依赖)。

proposer(离线 schema 提议)与 llm_parser(查询时意图解析)共用本模块,
统一一套配置:LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_TIMEOUT。

设计:
- 仅 urllib(标准库),无 openai / anthropic SDK 依赖。
- .env 在【模块 import 时加载一次】(应用层关注),LLMClient.__init__ 纯读 os.environ
  ——这样既支持 .env 配置,又不破坏测试的 monkeypatch.delenv。
- chat(system, user, json_mode):json_mode=True 时强制 response_format:json_object
  (parser 用);json_mode=False 时纯文本(proposer 用,输出 YAML)。
- 未配置 base_url/api_key 时 available=False,chat 抛 LLMUnavailable(上层自行降级)。

边界:本模块是"查询时 LLM"的统一入口,但【算数永远不走 LLM】——
parser 只产出 Intent(结构化意图),取数/运算仍在确定性 engine。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """LLM 不可用(未配置 / 网络失败 / 响应非法),上层应降级处理。"""


def _load_dotenv(override: bool = False) -> None:
    """可选:加载 .env(python-dotenv 未装时静默跳过)。

    override=True 时覆盖已有 env(供 reload_env 用)。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=override)
    except ImportError:
        pass


# 模块 import 时加载 .env 一次。不在每个 LLMClient 实例里重复加载,
# 以保证 LLMClient.__init__ 纯粹读取 os.environ 当前状态(测试可 monkeypatch)。
_load_dotenv()


class LLMClient:
    """OpenAI 兼容 LLM 客户端。

    使用方式:
        client = LLMClient()            # 自动从 env 读配置
        if client.available:
            content = client.chat(system, user, json_mode=True)
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url: str = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.api_key: str = api_key or os.environ.get("LLM_API_KEY", "")
        self.model: str = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.timeout: int = timeout or int(os.environ.get("LLM_TIMEOUT", "30"))

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def status(self) -> str:
        """一行可读的诊断信息,用于日志 / 前端状态条。"""
        if not self.available:
            return "LLM 未配置(LLM_BASE_URL / LLM_API_KEY 缺失)"
        return f"LLM 已配置: {self.model} @ {self.base_url}"

    def chat(self, system: str, user: str, json_mode: bool = False,
             timeout: int | None = None) -> str:
        """调 chat/completions,返回 message.content。

        json_mode=True 时强制 response_format:json_object。
        timeout 显式覆盖实例默认值(供 proposer 这类离线重任务放宽)。
        不可用或网络/格式失败时抛 LLMUnavailable。
        """
        if not self.available:
            raise LLMUnavailable("LLM 未配置(LLM_BASE_URL / LLM_API_KEY 缺失)")

        to = self.timeout if timeout is None else timeout
        url = self.base_url + "/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=to) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise LLMUnavailable(f"LLM API HTTP {e.code}: {err_body[:300]}") from e
        except urllib.error.URLError as e:
            raise LLMUnavailable(f"LLM API 网络错误: {e.reason}") from e
        except TimeoutError as e:
            raise LLMUnavailable(f"LLM API 超时(>{to}s)") from e

        try:
            obj = json.loads(raw)
            return obj["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMUnavailable(f"LLM 响应格式异常: {raw[:200]}") from e


# ---------------- 模块级单例(供 proposer / parser 复用)----------------
_default_client: LLMClient | None = None


def get_default() -> LLMClient:
    """惰性构造:第一次调用读 env,后续复用。"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def reload_env() -> LLMClient:
    """重新加载 .env(override=True)并构造新客户端(.env 改后调用)。"""
    global _default_client
    _load_dotenv(override=True)
    _default_client = LLMClient()
    return _default_client
