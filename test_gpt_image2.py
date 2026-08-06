from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


def require_dict(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(message)
    return value


def upload_image(
    session: requests.Session,
    base_url: str,
    image_path: Path,
) -> str:
    if not image_path.is_file():
        raise FileNotFoundError(f"图片不存在：{image_path}")

    with image_path.open("rb") as file:
        response = session.post(
            f"{base_url}/uploads/images",
            files={"file": (image_path.name, file)},
            data={"purpose": "generation"},
            timeout=120,
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"上传接口没有返回 JSON：HTTP {response.status_code}\n"
            f"{response.text[:500]}"
        ) from exc

    if not response.ok or not body.get("success"):
        raise RuntimeError(
            f"上传失败：HTTP {response.status_code}\n"
            f"{json.dumps(body, ensure_ascii=False, indent=2)}"
        )

    data = require_dict(body.get("data"), "上传结果缺少 data")
    image_url = data.get("url")

    if not isinstance(image_url, str) or not image_url:
        raise RuntimeError("上传结果缺少 data.url")

    return image_url


def extract_task_id(body: dict[str, Any]) -> str:
    candidates = [
        body.get("task_id"),
        body.get("id"),
        body.get("data", {}).get("id")
        if isinstance(body.get("data"), dict)
        else None,
    ]

    for value in candidates:
        if isinstance(value, str) and value:
            return value

    raise RuntimeError(
        "生成响应中没有找到 task_id：\n"
        + json.dumps(body, ensure_ascii=False, indent=2)
    )


def extract_status(body: dict[str, Any]) -> str:
    candidates = [
        body.get("task_status"),
        body.get("status"),
        body.get("data", {}).get("status")
        if isinstance(body.get("data"), dict)
        else None,
    ]

    for value in candidates:
        if isinstance(value, str) and value:
            return value.lower()

    return "unknown"


def extract_image_url(body: dict[str, Any]) -> str | None:
    direct_url = body.get("url")
    if isinstance(direct_url, str) and direct_url:
        return direct_url

    data = body.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            url = first.get("url")
            if isinstance(url, str) and url:
                return url

    result = body.get("result")
    if isinstance(result, dict):
        result_url = result.get("url")
        if isinstance(result_url, str) and result_url:
            return result_url

        result_data = result.get("data")
        if isinstance(result_data, list) and result_data:
            first = result_data[0]
            if isinstance(first, dict):
                url = first.get("url")
                if isinstance(url, str) and url:
                    return url

    output = body.get("output")
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, dict):
            url = first.get("url")
            if isinstance(url, str) and url:
                return url

    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用 preview-request 和参考图片测试 gpt-image-2"
    )
    parser.add_argument(
        "--request",
        required=True,
        type=Path,
        help="preview-request.json 路径",
    )
    parser.add_argument(
        "--image",
        action="append",
        required=True,
        type=Path,
        help="参考图片路径，按参考图顺序重复填写",
    )
    parser.add_argument(
        "--output",
        default=Path("gpt-image2-preview.png"),
        type=Path,
        help="生成图片保存路径",
    )
    parser.add_argument(
        "--size",
        default="9:16",
        help="输出比例，默认 9:16",
    )
    parser.add_argument(
        "--resolution",
        default="1k",
        choices=["1k", "2k", "4k"],
        help="分辨率档位，首次测试建议 1k",
    )
    args = parser.parse_args()

    api_key = os.getenv("TOAPIS_API_KEY")
    base_url = os.getenv(
        "TOAPIS_BASE_URL",
        "https://ai-api.youchu.work/v1",
    ).rstrip("/")

    if not api_key:
        raise RuntimeError("缺少环境变量 TOAPIS_API_KEY")

    request_data = json.loads(
        args.request.read_text(encoding="utf-8-sig")
    )

    prompt = request_data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError("preview-request.json 缺少有效 prompt")

    reference_assets = request_data.get("reference_assets", [])
    if isinstance(reference_assets, list):
        expected_count = len(reference_assets)
        if expected_count != len(args.image):
            raise RuntimeError(
                f"JSON 要求 {expected_count} 张参考图，"
                f"命令实际传入 {len(args.image)} 张"
            )

    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {api_key}"}
    )

    uploaded_urls: list[str] = []

    for index, image_path in enumerate(args.image, start=1):
        print(f"[1/4] 上传参考图 {index}：{image_path}")
        image_url = upload_image(session, base_url, image_path)
        uploaded_urls.append(image_url)
        print(f"      上传成功：{image_url}")

    payload = {
        "model": "gpt-image-2",
        "prompt": prompt,
        "reference_images": uploaded_urls,
        "size": args.size,
        "resolution": args.resolution,
        "n": 1,
        "response_format": "url",
    }

    print("[2/4] 提交 gpt-image-2 生成任务")

    response = session.post(
        f"{base_url}/images/generations",
        json=payload,
        timeout=120,
    )

    try:
        task_body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"生成接口没有返回 JSON：HTTP {response.status_code}\n"
            f"{response.text[:500]}"
        ) from exc

    if not response.ok or task_body.get("success") is False:
        raise RuntimeError(
            f"生成请求失败：HTTP {response.status_code}\n"
            f"{json.dumps(task_body, ensure_ascii=False, indent=2)}"
        )

    task_id = extract_task_id(task_body)
    print(f"      task_id：{task_id}")
    print("[3/4] 等待生成结果")

    deadline = time.time() + 300
    final_body: dict[str, Any] | None = None

    while time.time() < deadline:
        status_response = session.get(
            f"{base_url}/images/generations/{task_id}",
            timeout=60,
        )
        status_body = status_response.json()

        if not status_response.ok:
            raise RuntimeError(
                f"查询任务失败：HTTP {status_response.status_code}\n"
                f"{json.dumps(status_body, ensure_ascii=False, indent=2)}"
            )

        status = extract_status(status_body)
        progress = status_body.get(
            "progress",
            status_body.get("data", {}).get("progress", 0)
            if isinstance(status_body.get("data"), dict)
            else 0,
        )

        print(f"      状态：{status}，进度：{progress}%")

        if status in {"completed", "succeeded", "success", "finished"}:
            final_body = status_body
            break

        if status in {"failed", "error", "cancelled", "canceled"}:
            raise RuntimeError(
                "图片生成失败：\n"
                + json.dumps(status_body, ensure_ascii=False, indent=2)
            )

        time.sleep(3)

    if final_body is None:
        raise TimeoutError("等待图片生成超时，任务可能仍在服务端运行")

    image_url = extract_image_url(final_body)
    if not image_url:
        raise RuntimeError(
            "任务已完成，但结果中没有找到图片 URL：\n"
            + json.dumps(final_body, ensure_ascii=False, indent=2)
        )

    print("[4/4] 下载图片")
    image_response = requests.get(image_url, timeout=180)
    image_response.raise_for_status()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image_response.content)

    result_json = args.output.with_suffix(".result.json")
    result_json.write_text(
        json.dumps(final_body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"      图片：{args.output.resolve()}")
    print(f"      结果：{result_json.resolve()}")

    if sys.platform == "win32":
        os.startfile(args.output.resolve())  # type: ignore[attr-defined]

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n测试失败：{exc}", file=sys.stderr)
        raise SystemExit(2)