"""GLM documented response_format probe（仅测 Case D）。

背景（由 Control-0 / B-C probe 实跑结论得出）：
- Control-0（glm_structured_output_probe.py）已证明 production
  vlm_client.py 调用链可以真实到达 GLM（HTTP 200 + 完整 provider envelope）。
- glm_split_response_format_probe.py 已实现 experiment-only 的
  production-aligned transport（Case B / Case C：拆分字段形式）。
- 中转站文档定义的 response_format 原生格式为：
      {
        "response_format": {
          "type": "json_schema",
          "json_schema": {"name": "<string>", "schema": {}}
        }
      }
  文档没有定义 strict / response_format_schema /
  response_format="json_schema" / response_format="none"。

本实验（experiment-only，不修改任何现有文件）：
- 只测试一个新 Case D（case-d-documented-json-schema）：
  production transport + 中转站文档原生 nested json_schema + 不发送 strict。
- 不运行 Case A / B / C。
- response_format 完全按文档格式构造；"strict" 与 "response_format_schema"
  字段必须完全不存在，发送前用 deterministic assertion 自检，不满足则
  实验直接失败、绝不发送请求（避免把旧 Case A 的 strict=True 带进去）。
- prompt 中不出现任何 schema 字段名（schema_version / foo / number /
  mandatory_unused_field），也没有 JSON example —— schema 约束只能来自
  API 的 response_format 参数本身。
- 最重要探针字段：mandatory_unused_field（prompt 中绝不出现；只有 schema
  真正通过 API schema 参数约束了模型输出时才会出现该字段）。
- 不自动输出 supported / unsupported 结论；只记录 transport / HTTP /
  provider raw response / parse / schema validation /
  mandatory_unused_field_present，结论后续人工判断。

Exit code 约定：0 = Case D 完成了一次真实 HTTP 交互（schema 是否有效是被
测量的实验结论，不是失败条件）；1 = 配置 / 图片 / payload 断言 / transport
层面的失败（payload 断言失败时不发送任何请求）。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from threading import local

import requests
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# 直接 import 复用 production vlm_client.py 的公共 API、transport 常量与
# 模块级 helper（含带下划线前缀的私有 helper，均为 production 原实现）。
from vlm_client import (  # noqa: E402
    RECOVERABLE_HTTP_STATUS_CODES,
    RECOVERABLE_TRANSPORT_EXCEPTIONS,
    TRANSPORT_MAX_ATTEMPTS,
    TRANSPORT_RETRY_WAIT_SECONDS,
    VLMClientConfig,
    VLMConfigurationError,
    VLMError,
    VLMResponseParseError,
    VLMResponseTruncatedError,
    VLMTransportError,
    _safe_provider_body,
    _transport_failure_detail,
    build_chat_completions_endpoint,
    encode_image_as_data_url,
    parse_json_object,
)

MODEL = "glm-5.3-flash"
MAX_TOKENS = 12000  # 与 A2 ADMISSION_MAX_TOKENS / Control-0 CONTROL_0_MAX_TOKENS 一致

# 与 Control-0 / B-C probe 完全相同的固定图片与固定 prompt。
# prompt 中不含任何 schema 字段名，也没有 JSON example。
IMAGE_PATH = Path(
    r"runs\20260902_direct-asset-discovery-005-production-client\analysis-image.png"
)
SYSTEM_PROMPT = "Return the requested structured response."
USER_PROMPT = "Produce one response now."

OUTPUT_ROOT = Path("runs/20260903_glm_documented_response_format_probe")
SUMMARY_SCHEMA_VERSION = "glm-documented-response-format-probe-v0.1"

# 与 glm_structured_output_probe.py / Control-0 / B-C probe 完全相同的固定 SCHEMA。
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "foo",
        "number",
        "mandatory_unused_field",
    ],
    "properties": {
        "schema_version": {
            "const": "probe-v1",
        },
        "foo": {
            "type": "string",
        },
        "number": {
            "type": "integer",
        },
        "mandatory_unused_field": {
            "type": "string",
        },
    },
}

REQUIRED_FIELDS = ["schema_version", "foo", "number", "mandatory_unused_field"]
PROBE_FIELD = "mandatory_unused_field"

CASE_D_NAME = "case-d-documented-json-schema"
CASE_D_RESPONSE_FORMAT_DESC = "documented nested json_schema without strict"


class ExperimentChatCompletionsTransport:
    """Experiment-only 复刻 production ChatCompletions transport。

    与 scripts/vlm_client.py ChatCompletionsVLMClient 的逐项对应关系：
    - endpoint：直接复用 production build_chat_completions_endpoint(...)。
    - session：与 production 相同，per-thread requests.Session
      （threading.local 模式），不使用裸 requests.post。
    - headers：与 production infer_json 中的 headers 字典逐字一致，
      含 Accept / User-Agent / Accept-Encoding。
    - retry：逐行复制 production _post_with_transport_retry 的
      Transport Retry v0.1 语义；常量与 helper 直接 import 自 production。
    - 204 / 空 body：与 production infer_json 的 2xx 后特判一致。
    唯一增量：额外把每次真实收到的 HTTP response 存入线程本地
    last_http_response，用于在非 200 / transport 失败时仍保存
    raw-http-response.txt 证据（不改变 retry 行为本身）。
    """

    def __init__(self, config: VLMClientConfig, *, max_tokens: int) -> None:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise VLMConfigurationError("max_tokens must be a positive integer")
        # 与 production ChatCompletionsVLMClient.__init__ 相同的 per-thread
        # session 工厂模式（production 在未注入 session 时即用 requests.Session）。
        self._session_factory = requests.Session
        self._session_local = local()
        self.config = config
        self.endpoint = build_chat_completions_endpoint(config.base_url)
        self.max_tokens = max_tokens
        self._response_local = local()

    def get_last_provider_response(self):
        """与 production get_last_provider_response 相同的线程本地语义。"""

        return getattr(self._response_local, "provider_response", None)

    def get_last_http_response(self):
        """experiment-only：返回最后一次真实收到的 HTTP response（或 None）。"""

        return getattr(self._response_local, "last_http_response", None)

    def redact(self, text: str) -> str:
        if not self.config.api_key:
            return text
        return text.replace(self.config.api_key, "[REDACTED]")

    def _get_session(self):
        """与 production _get_session 相同：per-thread session。"""

        session = getattr(self._session_local, "session", None)
        if session is None:
            session = self._session_factory()
            self._session_local.session = session
        return session

    def build_headers(self) -> dict[str, str]:
        """与 scripts/vlm_client.py ChatCompletionsVLMClient.infer_json 的
        headers 完全一致（勿删 Accept / User-Agent / Accept-Encoding）。"""

        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Stage2A-VLMClient/0.1",
            "Accept-Encoding": "identity",
        }

    def build_base_payload(
        self,
        *,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        """与 production infer_json 的基础 payload 字段逐项一致；
        thinking_policy == "omit" 时不发送任何 thinking 字段。"""

        payload: dict = {
            "model": self.config.model,
            "temperature": 0,
            "top_p": 1,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": encode_image_as_data_url(image_path)
                            },
                        },
                    ],
                },
            ],
        }
        if self.config.thinking_policy == "disabled":
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _post_with_transport_retry(
        self,
        *,
        payload: dict,
        headers: dict[str, str],
    ):
        """逐行复制 production ChatCompletionsVLMClient._post_with_transport_retry
        （Transport Retry v0.1）；常量与 helper 直接 import 自 production。
        唯一增量：每次收到 response 时记录到线程本地 last_http_response。"""

        for attempt in range(1, TRANSPORT_MAX_ATTEMPTS + 1):
            status_code: int | None = None
            try:
                response = self._get_session().post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout,
                )
            except RECOVERABLE_TRANSPORT_EXCEPTIONS as exc:
                detail = type(exc).__name__
            except Exception as exc:
                detail = type(exc).__name__
                raise VLMTransportError(
                    _transport_failure_detail(detail, attempt), retryable=False
                ) from None
            else:
                self._response_local.last_http_response = response
                status_code = getattr(response, "status_code", None)
                if type(status_code) is not int:
                    detail = "Provider response has no HTTP status code"
                    raise VLMTransportError(
                        _transport_failure_detail(detail, attempt), retryable=False
                    )
                if 200 <= status_code < 300:
                    return response
                body = _safe_provider_body(
                    getattr(response, "text", ""), self.config.api_key
                )
                detail = f"HTTP {status_code}"
                if body:
                    detail += f": {body}"
                if status_code not in RECOVERABLE_HTTP_STATUS_CODES:
                    raise VLMTransportError(
                        _transport_failure_detail(detail, attempt),
                        retryable=False,
                        status_code=status_code,
                    )

            if attempt == TRANSPORT_MAX_ATTEMPTS:
                raise VLMTransportError(
                    _transport_failure_detail(detail, attempt),
                    retryable=True,
                    status_code=status_code,
                )
            time.sleep(TRANSPORT_RETRY_WAIT_SECONDS)

        raise AssertionError("transport retry loop exhausted without a result")

    def send(self, *, payload: dict):
        """发送 payload，返回原始 requests response。

        2xx 后处理与 production infer_json 一致：HTTP 204 / 空 body 特判、
        JSON 解码、并把解码后的 provider envelope 存入线程本地
        provider_response（供 get_last_provider_response 使用）。
        """

        self._response_local.provider_response = None
        self._response_local.last_http_response = None
        response = self._post_with_transport_retry(
            payload=payload, headers=self.build_headers()
        )
        status_code = getattr(response, "status_code", None)
        response_text = getattr(response, "text", "")
        if status_code == 204:
            raise VLMTransportError(
                "Provider returned HTTP 204 with no response body",
                retryable=True,
                status_code=204,
            )
        if not isinstance(response_text, str) or not response_text.strip():
            raise VLMTransportError(
                "Provider returned an empty response body",
                retryable=True,
                status_code=status_code if type(status_code) is int else None,
            )
        # 防御性脱敏：解码前移除 API key（正常情况下 provider body 不会包含它）。
        response_text = self.redact(response_text)
        try:
            provider_response = json.loads(response_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise VLMResponseParseError(
                "Chat Completions response body is not valid JSON"
            ) from exc
        self._response_local.provider_response = provider_response
        return response


def assert_documented_response_format(payload: dict) -> None:
    """发送前的 deterministic payload 自检（Case D：文档原生 nested json_schema）。

    任何一条不满足都直接让实验失败，不发送请求 —— 这样避免不小心把旧
    Case A 的 strict=True 或 B/C 的 response_format_schema 带进去。
    """

    assert isinstance(payload["response_format"], dict), (
        "response_format must be the documented nested object form"
    )
    assert payload["response_format"]["type"] == "json_schema", (
        "response_format.type must be 'json_schema'"
    )
    assert set(payload["response_format"].keys()) == {"type", "json_schema"}, (
        "response_format must contain exactly type + json_schema"
    )
    assert "response_format_schema" not in payload, (
        "response_format_schema must not be sent (not defined by the docs)"
    )
    assert "strict" not in payload, (
        "no top-level strict field may be sent"
    )
    json_schema = payload["response_format"]["json_schema"]
    assert set(json_schema.keys()) == {"name", "schema"}, (
        "json_schema must contain exactly name + schema (no strict)"
    )
    assert "strict" not in json_schema, (
        "strict must be completely absent (neither True nor False)"
    )
    assert json_schema["name"] == "structured_output_probe", (
        "json_schema.name must be 'structured_output_probe'"
    )
    assert json_schema["schema"] == SCHEMA, (
        "json_schema.schema must equal the fixed probe SCHEMA"
    )


def build_case_payload(transport: ExperimentChatCompletionsTransport) -> dict:
    """构造最终真正发出去的 payload（基础字段 + 文档原生 nested json_schema）。"""

    payload = transport.build_base_payload(
        image_path=IMAGE_PATH,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=USER_PROMPT,
    )
    payload["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_output_probe",
            "schema": SCHEMA,
        },
    }
    assert_documented_response_format(payload)
    return payload


def extract_assistant_content(provider_response) -> str:
    """与 production ChatCompletionsVLMClient.infer_json 的 content 提取路径
    一致（choices[0].message.content / finish_reason=="length" 视为截断）。"""

    try:
        choice = provider_response["choices"][0]
        finish_reason = choice.get("finish_reason")
        message = choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise VLMResponseParseError(
            "Chat Completions response has no assistant content"
        ) from exc
    if finish_reason == "length":
        raise VLMResponseTruncatedError(
            "model response reached token limit before producing final content"
        )
    if not isinstance(content, str) or not content.strip():
        raise VLMResponseParseError(
            "Chat Completions response contains no final message content"
        )
    return content


def validate_parsed(parsed) -> dict:
    """jsonschema Draft 2020-12 严格校验固定 SCHEMA。"""

    errors = sorted(
        Draft202012Validator(SCHEMA).iter_errors(parsed),
        key=lambda error: [str(part) for part in error.path],
    )
    return {
        "schema_valid": not errors,
        "validation_errors": [
            {
                "path": "$"
                + "".join(
                    f"[{part}]" if isinstance(part, int) else f".{part}"
                    for part in error.path
                ),
                "message": error.message,
            }
            for error in errors
        ],
    }


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_http_evidence(
    transport: ExperimentChatCompletionsTransport,
    case_dir: Path,
) -> bool:
    """保存 raw-http-response.txt（HTTP status + response headers + raw body）。

    无论 HTTP 200 还是失败路径，只要真实收到过 response 就保存证据。
    """

    response = transport.get_last_http_response()
    if response is None:
        return False
    raw_body = getattr(response, "text", "")
    if not isinstance(raw_body, str):
        raw_body = ""
    (case_dir / "raw-http-response.txt").write_text(
        "HTTP_STATUS = {}\n"
        "\n"
        "RESPONSE_HEADERS =\n"
        "{}\n"
        "\n"
        "RESPONSE_BODY =\n"
        "{}\n".format(
            getattr(response, "status_code", None),
            json.dumps(dict(response.headers), ensure_ascii=False, indent=2),
            transport.redact(raw_body),
        ),
        encoding="utf-8",
    )
    return True


def run_case(
    transport: ExperimentChatCompletionsTransport,
    case_dir: Path,
) -> tuple[dict, dict | None]:
    """运行唯一 Case D；返回 (result, payload)。payload 为 None 表示
    payload 构造 / 自检失败（未发送任何请求）。"""

    case_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "case": CASE_D_NAME,
        "response_format": CASE_D_RESPONSE_FORMAT_DESC,
        "request_ok": False,
        "http_status": None,
        "parse_ok": False,
        "schema_valid": False,
        "validation_errors": [],
        "required_fields_present": {},
        "mandatory_unused_field_present": None,
        "parsed": None,
        "raw_provider_response_saved": False,
        "http_evidence_saved": False,
        "error": None,
        "error_type": None,
    }

    print()
    print("=" * 70)
    print(CASE_D_NAME)
    print("=" * 70)
    print("RESPONSE_FORMAT =", CASE_D_RESPONSE_FORMAT_DESC)

    # 1) 构造最终 payload（文档原生 nested json_schema；断言无 strict /
    #    无 response_format_schema；不满足则直接失败、不发送请求）
    try:
        payload = build_case_payload(transport)
    except (AssertionError, KeyError, TypeError) as exc:
        result["error"] = f"payload self-check failed: {exc}"
        result["error_type"] = type(exc).__name__
        print("ABORTED_BEFORE_SEND =", result["error"])
        return result, None

    # 2) request-payload.json：最终真正发出去的 payload；API key 不得写入。
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if transport.config.api_key and transport.config.api_key in payload_text:
        result["error"] = "payload unexpectedly contains the API key; aborted"
        result["error_type"] = "ApiKeyLeakGuard"
        print("ABORTED_BEFORE_SEND =", result["error"])
        return result, None
    (case_dir / "request-payload.json").write_text(payload_text, encoding="utf-8")

    # 3) 发送（production transport 语义：per-thread Session + Transport Retry）
    try:
        response = transport.send(payload=payload)
    except VLMError as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        result["http_evidence_saved"] = write_http_evidence(transport, case_dir)
        raw = transport.get_last_provider_response()
        if raw is not None:
            write_json(case_dir / "raw-provider-response.json", raw)
            result["raw_provider_response_saved"] = True
        print("TRANSPORT_ERROR =", result["error"])
        return result, payload

    result["request_ok"] = True
    result["http_status"] = response.status_code

    # 4) raw-http-response.txt：HTTP_STATUS + response headers + raw body
    result["http_evidence_saved"] = write_http_evidence(transport, case_dir)

    # 5) raw-provider-response.json（解码后的 envelope；send() 内已脱敏）
    provider_response = transport.get_last_provider_response()
    if provider_response is not None:
        write_json(case_dir / "raw-provider-response.json", provider_response)
        result["raw_provider_response_saved"] = True

    # 6) 非 200：如实记录 HTTP 层结果，不伪造任何 structured output 结论
    if response.status_code != 200:
        result["error"] = f"non-200 HTTP status: {response.status_code}"
        result["error_type"] = "Non200HttpStatus"
        return result, payload

    # 7) 提取 assistant content（与 production infer_json 提取路径一致）
    try:
        content = extract_assistant_content(provider_response)
    except VLMError as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        return result, payload

    # 8) 解析 content：直接复用 production parse_json_object
    #    （剥 <think> 块与 markdown fence，要求 JSON object；允许剥 fence）
    parsed = None
    try:
        parsed = parse_json_object(content)
        result["parse_ok"] = True
    except VLMError as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__

    write_json(
        case_dir / "parsed-content.json",
        {
            "content_raw": content,
            "parse_ok": result["parse_ok"],
            "parse_error": None if result["parse_ok"] else result["error"],
            "parsed": parsed,
        },
    )

    if not result["parse_ok"]:
        return result, payload

    # 9) jsonschema 严格校验（Draft 2020-12，固定 SCHEMA）+
    #    单独记录最重要探针字段 mandatory_unused_field 是否出现
    validation = validate_parsed(parsed)
    result["schema_valid"] = validation["schema_valid"]
    result["validation_errors"] = validation["validation_errors"]
    result["parsed"] = parsed
    result["required_fields_present"] = {
        field: field in parsed for field in REQUIRED_FIELDS
    }
    result["mandatory_unused_field_present"] = PROBE_FIELD in parsed

    write_json(
        case_dir / "validation-result.json",
        {
            **validation,
            "required_fields_present": result["required_fields_present"],
            "mandatory_unused_field_present": result["mandatory_unused_field_present"],
        },
    )

    return result, payload


def print_case_results(result: dict) -> None:
    """按固定模板打印 Case D 结果块（不输出 supported / unsupported 结论）。"""

    print("REQUEST_OK =", result["request_ok"])
    print("HTTP_STATUS =", result["http_status"])
    print("PARSE_OK =", result["parse_ok"])
    print("SCHEMA_VALID =", result["schema_valid"])
    print(
        "MANDATORY_UNUSED_FIELD_PRESENT =",
        result["mandatory_unused_field_present"],
    )
    if result["parsed"] is not None:
        print("PARSED =", json.dumps(result["parsed"], ensure_ascii=False))
    else:
        print("PARSED = None")
    print("ERROR =", result["error"])


def write_summary(
    output_root: Path,
    config: VLMClientConfig,
    transport: ExperimentChatCompletionsTransport,
    payload: dict | None,
    result: dict,
) -> Path:
    """写 summary.json；protocol 部分从最终实际 payload 测量得出（非硬编码）。"""

    if payload is not None:
        response_format = payload["response_format"]
        json_schema = (
            response_format.get("json_schema")
            if isinstance(response_format, dict)
            else None
        )
        protocol = {
            "response_format": (
                "object" if isinstance(response_format, dict) else "non-object"
            ),
            "type": response_format.get("type"),
            "strict_present": isinstance(json_schema, dict) and "strict" in json_schema,
            "response_format_schema_present": "response_format_schema" in payload,
            "response_format_sent": response_format,
        }
    else:
        protocol = {
            "response_format": None,
            "type": None,
            "strict_present": None,
            "response_format_schema_present": None,
            "response_format_sent": None,
        }

    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "model": config.model,
        "protocol": protocol,
        "case": {
            "name": result["case"],
            "request_ok": result["request_ok"],
            "http_status": result["http_status"],
            "parse_ok": result["parse_ok"],
            "schema_valid": result["schema_valid"],
            "mandatory_unused_field_present": result["mandatory_unused_field_present"],
            "parsed": result["parsed"],
            "error": result["error"],
            "error_type": result["error_type"],
            "validation_errors": result["validation_errors"],
            "required_fields_present": result["required_fields_present"],
            "response_format": result["response_format"],
        },
        "api_mode": config.api_mode,
        "thinking_policy": config.thinking_policy,
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "top_p": 1,
        "endpoint": transport.endpoint,
        "image": str(IMAGE_PATH),
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": USER_PROMPT,
        "schema": SCHEMA,
    }
    summary_path = output_root / "summary.json"
    write_json(summary_path, summary)
    return summary_path


def print_final(result: dict, summary_path: Path) -> None:
    print()
    print("=" * 70)
    print("FINAL")
    print("=" * 70)
    print("CASE_D_REQUEST_OK =", result["request_ok"])
    print("CASE_D_HTTP_STATUS =", result["http_status"])
    print("CASE_D_PARSE_OK =", result["parse_ok"])
    print("CASE_D_SCHEMA_VALID =", result["schema_valid"])
    print(
        "CASE_D_MANDATORY_UNUSED_FIELD_PRESENT =",
        result["mandatory_unused_field_present"],
    )
    print("SUMMARY =", summary_path)


def main() -> int:
    print("=" * 70)
    print("GLM documented response_format probe (Case D only)")
    print("=" * 70)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    if not IMAGE_PATH.exists():
        print("IMAGE_NOT_FOUND =", IMAGE_PATH)
        return 1

    try:
        # 与 asset_admission_probe.py（A2）/ Control-0 / B-C probe 完全一致的
        # 配置链：base URL / API key 来自 STAGE2A_VLM_* env vars，model 固定为
        # glm-5.3-flash，api_mode=chat_completions，thinking_policy=omit。
        config = replace(
            VLMClientConfig.from_env(model_override=MODEL),
            api_mode="chat_completions",
            thinking_policy="omit",
        )
        transport = ExperimentChatCompletionsTransport(config, max_tokens=MAX_TOKENS)
    except (VLMError, ValueError) as exc:
        print("CONFIG_ERROR =", str(exc))
        return 1

    print("BASE_URL =", config.base_url)
    print("MODEL =", config.model)
    print("API_MODE =", config.api_mode)
    print("THINKING_POLICY =", config.thinking_policy)
    print("MAX_TOKENS =", MAX_TOKENS)
    print("ENDPOINT =", transport.endpoint)
    print("IMAGE =", IMAGE_PATH)
    print("OUTPUT =", OUTPUT_ROOT)

    result, payload = run_case(transport, OUTPUT_ROOT / CASE_D_NAME)
    print_case_results(result)

    summary_path = write_summary(OUTPUT_ROOT, config, transport, payload, result)
    print_final(result, summary_path)

    return 0 if result["request_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
