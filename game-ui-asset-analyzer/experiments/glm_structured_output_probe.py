import json
import base64
import os
import sys
from dataclasses import replace
from pathlib import Path

import requests
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from vlm_client import (  # noqa: E402
    VLMClientConfig,
    VLMError,
    create_configured_vlm_client,
)


MODEL = "glm-5.3-flash"

# Control-0: rerun the same minimal structured-output probe through the exact
# production vlm_client.py call chain used by Stage2-A2, to check whether the
# production client reaches HTTP 200 / a provider response where the
# hand-written requests.post probe got HTTP 204.
CONTROL_0_IMAGE = Path(
    r"runs\20260902_direct-asset-discovery-005-production-client\analysis-image.png"
)
CONTROL_0_SYSTEM_PROMPT = "Return the requested structured response."
CONTROL_0_USER_PROMPT = "Produce one response now."
CONTROL_0_MAX_TOKENS = 12000  # identical to A2 ADMISSION_MAX_TOKENS

# Case A/B/C (hand-written requests.post payloads) are temporarily suspended
# while the HTTP 204 gap against the production call chain is investigated.
RUN_STRUCTURED_OUTPUT_CASES = False

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

def encode_image_as_data_url(image_path: Path) -> str:
    raw = image_path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"

def endpoint_from_base(base_url: str) -> str:
    base_url = base_url.rstrip("/")

    if base_url.endswith("/v1"):
        return base_url + "/chat/completions"

    return base_url + "/v1/chat/completions"


def strip_json_fence(text: str) -> str:
    text = text.strip()

    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```"):
        text = text[3:].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    return text


def validate_parsed(parsed):
    errors = sorted(
        Draft202012Validator(SCHEMA).iter_errors(parsed),
        key=lambda e: list(e.path),
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


def validate_content(content: str):
    try:
        parsed = json.loads(strip_json_fence(content))
    except Exception as exc:
        return {
            "json_parse_ok": False,
            "schema_valid": False,
            "parse_error": str(exc),
            "parsed": None,
        }

    return {
        "json_parse_ok": True,
        "parsed": parsed,
        **validate_parsed(parsed),
    }


def raw_provider_response(client):
    getter = getattr(client, "get_last_provider_response", None)
    return getter() if callable(getter) else None


def write_control_0_result(output_root: Path, result: dict) -> None:
    (output_root / "control-0-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_control_0_summary(result: dict) -> None:
    print("CONTROL_0_REQUEST_OK =", result["request_ok"])
    print("CONTROL_0_PARSE_OK =", result["parse_ok"])
    print("CONTROL_0_SCHEMA_VALID =", result["schema_valid"])
    print("CONTROL_0_PARSED =", json.dumps(result["parsed"], ensure_ascii=False))


def run_control_0(output_root: Path) -> int:
    image_path = CONTROL_0_IMAGE

    print()
    print("=" * 70)
    print("control-0-production-client")
    print("=" * 70)

    result = {
        "case": "control-0-production-client",
        "model": MODEL,
        "api_mode": "chat_completions",
        "thinking_policy": "omit",
        "max_tokens": CONTROL_0_MAX_TOKENS,
        "image": str(image_path),
        "request_ok": False,
        "parse_ok": False,
        "schema_valid": False,
        "validation_errors": [],
        "parsed": None,
        "raw_provider_response_saved": False,
        "error": None,
        "error_type": None,
    }

    if not image_path.exists():
        result["error"] = f"Analysis Image not found: {image_path}"
        result["error_type"] = "FileNotFoundError"
        print("CONTROL_0_ERROR =", result["error"])
        write_control_0_result(output_root, result)
        print_control_0_summary(result)
        return 1

    try:
        # Exactly the Stage2-A2 configuration chain from asset_admission_probe.py:
        # base URL / API key come from STAGE2A_VLM_* env vars, the model is pinned
        # to glm-5.3-flash, and thinking_policy matches A2 ("omit"). The production
        # client itself hardcodes temperature=0 / top_p=1 inside infer_json.
        config = replace(
            VLMClientConfig.from_env(model_override=MODEL),
            api_mode="chat_completions",
            thinking_policy="omit",
        )
        client = create_configured_vlm_client(
            config,
            max_tokens=CONTROL_0_MAX_TOKENS,
        )
    except (VLMError, ValueError) as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        print("CONTROL_0_ERROR =", str(exc))
        write_control_0_result(output_root, result)
        print_control_0_summary(result)
        return 1

    result["base_url"] = config.base_url
    result["endpoint"] = client.endpoint

    print("BASE_URL =", config.base_url)
    print("MODEL =", config.model)
    print("API_MODE =", config.api_mode)
    print("THINKING_POLICY =", config.thinking_policy)
    print("MAX_TOKENS =", CONTROL_0_MAX_TOKENS)
    print("ENDPOINT =", client.endpoint)
    print("IMAGE =", image_path)

    try:
        parsed = client.infer_json(
            image_path=image_path,
            system_prompt=CONTROL_0_SYSTEM_PROMPT,
            user_prompt=CONTROL_0_USER_PROMPT,
            response_schema=SCHEMA,
        )
    except VLMError as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        print("CONTROL_0_ERROR =", str(exc))
    else:
        result["request_ok"] = True
        result["parse_ok"] = True
        result["parsed"] = parsed
        validation = validate_parsed(parsed)
        result["schema_valid"] = validation["schema_valid"]
        result["validation_errors"] = validation["validation_errors"]

    raw = raw_provider_response(client)
    if raw is not None:
        (output_root / "control-0-raw-provider-response.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["raw_provider_response_saved"] = True

    write_control_0_result(output_root, result)
    print_control_0_summary(result)

    if result["request_ok"] and not result["schema_valid"]:
        for error in result["validation_errors"]:
            print(" -", error["path"], error["message"])
        return 1

    return 0 if result["request_ok"] else 1


def run_structured_output_cases(
    output_root: Path,
    base_url: str,
    api_key: str,
) -> None:
    endpoint = endpoint_from_base(base_url)
    print()
    print("BASE_URL =", base_url)
    print("ENDPOINT =", endpoint)

    image_url = encode_image_as_data_url(CONTROL_0_IMAGE)
    common = {
        "model": MODEL,
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 1000,
        "messages": [
            {
                "role": "system",
                "content": "Return the requested structured response.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Produce one response now. Inspect the attached image if needed.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url,
                        },
                    },
                ],
            },
        ],
    }

    cases = {
        # Case A: 当前工程正在使用的 OpenAI-style nested json_schema
        "case-a-openai-nested": {
            **common,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output_probe",
                    "strict": True,
                    "schema": SCHEMA,
                },
            },
        },

        # Case B: 你发现的拆分字段形式
        "case-b-split-schema": {
            **common,
            "response_format": "json_schema",
            "response_format_schema": SCHEMA,
        },

        # Case C: 你提到的 none + response_format_schema
        "case-c-none-plus-schema": {
            **common,
            "response_format": "none",
            "response_format_schema": SCHEMA,
        },
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    final_summary = {}

    for case_name, payload in cases.items():
        print()
        print("=" * 70)
        print(case_name)
        print("=" * 70)

        case_dir = output_root / case_name
        case_dir.mkdir(parents=True, exist_ok=True)

        (case_dir / "request-payload.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=120,
            )

            raw_text = response.text

            (case_dir / "raw-http-response.txt").write_text(
                raw_text,
                encoding="utf-8",
            )

            print("HTTP =", response.status_code)

            if response.status_code != 200:
                print("REQUEST FAILED")
                print("RESPONSE_HEADERS =", dict(response.headers))
                print("RESPONSE_BODY =", repr(response.text))

                final_summary[case_name] = {
                    "http_status": response.status_code,
                    "request_ok": False,
                    "response_headers": dict(response.headers),
                    "response_body": response.text,
                }
                continue

            provider_json = response.json()

            (case_dir / "raw-provider-response.json").write_text(
                json.dumps(
                    provider_json,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            content = provider_json["choices"][0]["message"]["content"]

            print("CONTENT =", content)

            result = validate_content(content)

            (case_dir / "validation-result.json").write_text(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            print("JSON_PARSE_OK =", result["json_parse_ok"])
            print("SCHEMA_VALID =", result["schema_valid"])

            if not result["schema_valid"]:
                for error in result.get("validation_errors", []):
                    print(
                        " -",
                        error["path"],
                        error["message"],
                    )

            final_summary[case_name] = {
                "http_status": response.status_code,
                "request_ok": True,
                "json_parse_ok": result["json_parse_ok"],
                "schema_valid": result["schema_valid"],
                "parsed": result["parsed"],
            }

        except Exception as exc:
            print("ERROR =", repr(exc))
            final_summary[case_name] = {
                "request_ok": False,
                "error": repr(exc),
            }

    summary_path = output_root / "summary.json"
    summary_path.write_text(
        json.dumps(
            final_summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 70)
    print("FINAL")
    print("=" * 70)

    for name, result in final_summary.items():
        print(
            name,
            "HTTP=",
            result.get("http_status"),
            "SCHEMA_VALID=",
            result.get("schema_valid"),
        )

    print("SUMMARY =", summary_path)


def main() -> int:
    output_root = Path(
        "runs/20260903_glm_structured_output_probe"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    exit_code = run_control_0(output_root)

    if RUN_STRUCTURED_OUTPUT_CASES:
        run_structured_output_cases(
            output_root,
            os.environ["STAGE2A_VLM_BASE_URL"],
            os.environ["STAGE2A_VLM_API_KEY"],
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
