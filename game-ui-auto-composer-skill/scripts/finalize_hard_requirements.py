#!/usr/bin/env python3
"""Deterministically rebuild Composer hard requirements from business text only."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


class HardRequirementFinalizationError(ValueError):
    """Raised when deterministic hard requirements cannot be finalized safely."""


PAGE_SEMANTIC_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "recharge_page",
        re.compile(r"(?:新的)?(?:游戏)?充值(?:页面|界面)"),
    ),
    (
        "guild_shop",
        re.compile(r"公会(?:商城|商店)(?:页面|界面)?", re.IGNORECASE),
    ),
    (
        "shop",
        re.compile(r"(?:游戏)?(?:商城|商店)(?:页面|界面)?|\bshop(?: page)?\b", re.IGNORECASE),
    ),
)

NUMBER_VALUES = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
CHINESE_NUMBER_CHARS = "一两二三四五六七八九十"
NUMBER_PATTERN = (
    rf"(?:\d+|(?<![{CHINESE_NUMBER_CHARS}])"
    rf"[{CHINESE_NUMBER_CHARS}](?![{CHINESE_NUMBER_CHARS}]))"
)

ELEMENT_RULES: tuple[dict[str, str], ...] = (
    {
        "noun": "刷新按钮",
        "count_fact_id": "count_refresh",
        "count_target": "refresh_button",
        "element_fact_id": "refresh_required",
        "element_target": "refresh_button",
        "semantic": "refresh_button",
    },
    {
        "noun": "购买按钮",
        "count_fact_id": "count_purchase_buttons",
        "count_target": "purchase_button",
        "element_fact_id": "purchase_button_required",
        "element_target": "purchase_button",
        "semantic": "purchase_button",
    },
    {
        "noun": "角色头像",
        "count_fact_id": "count_character_avatars",
        "count_target": "character_avatar",
        "element_fact_id": "character_avatar_required",
        "element_target": "character_avatar",
        "semantic": "character_avatar",
    },
    {
        "noun": "奖励列表",
        "count_fact_id": "count_reward_lists",
        "count_target": "reward_list",
        "element_fact_id": "reward_list_required",
        "element_target": "reward_list",
        "semantic": "reward_list",
    },
    {
        "noun": "倒计时",
        "count_fact_id": "count_countdowns",
        "count_target": "countdown",
        "element_fact_id": "countdown_required",
        "element_target": "countdown",
        "semantic": "countdown",
    },
    {
        "noun": "分类",
        "count_fact_id": "count_categories",
        "count_target": "category_tab_template",
        "element_fact_id": "categories_required",
        "element_target": "category_navigation",
        "semantic": "category_navigation",
    },
    {
        "noun": "商品",
        "count_fact_id": "count_products",
        "count_target": "product_card_template",
        "element_fact_id": "products_required",
        "element_target": "product_grid",
        "semantic": "product_grid",
    },
    {
        "noun": "奖励",
        "count_fact_id": "count_rewards",
        "count_target": "reward_item_template",
        "element_fact_id": "rewards_required",
        "element_target": "reward_list",
        "semantic": "reward_list",
    },
    {
        "noun": "按钮",
        "count_fact_id": "count_buttons",
        "count_target": "button_template",
        "element_fact_id": "buttons_required",
        "element_target": "button_group",
        "semantic": "button_group",
    },
)
ELEMENT_BY_NOUN = {item["noun"]: item for item in ELEMENT_RULES}
NOUN_PATTERN = "|".join(re.escape(item["noun"]) for item in ELEMENT_RULES)

COUNT_RE = re.compile(
    rf"(?P<number>{NUMBER_PATTERN})\s*(?:个|项|条|张)?\s*(?P<noun>{NOUN_PATTERN})"
)
GRID_X_RE = re.compile(
    rf"(?:(?P<noun>{NOUN_PATTERN})\s*(?:按|为|做成)?\s*)?"
    rf"(?P<columns>{NUMBER_PATTERN})\s*[xX×]\s*(?P<rows>{NUMBER_PATTERN})"
)
GRID_COLUMNS_ROWS_RE = re.compile(
    rf"(?:(?P<noun>{NOUN_PATTERN})\s*(?:按|为|做成)?\s*)?"
    rf"(?P<columns>{NUMBER_PATTERN})\s*列\s*(?P<rows>{NUMBER_PATTERN})\s*行"
)
GRID_ROWS_COLUMNS_RE = re.compile(
    rf"(?:(?P<noun>{NOUN_PATTERN})\s*(?:按|为|做成)?\s*)?"
    rf"(?P<rows>{NUMBER_PATTERN})\s*行\s*(?P<columns>{NUMBER_PATTERN})\s*列"
)
PER_ROW_RE = re.compile(
    rf"(?P<noun>{NOUN_PATTERN})\s*(?:按)?\s*每行\s*(?P<columns>{NUMBER_PATTERN})\s*个?"
)
EXPLICIT_ELEMENT_RE = re.compile(
    rf"(?P<prefix>必须有|必须包含|必须包括|需要|要有|包含|显示)\s*"
    rf"(?:(?:一|1)\s*个\s*)?(?P<noun>{NOUN_PATTERN})"
)
INCLUDE_RE = re.compile(
    r"(?:必须包含|必须包括|需要包含|要包含)\s*(?P<value>[^，,。；;！？!\n]+)"
)
EXCLUDE_RE = re.compile(
    r"(?:不得包含|不能包含|禁止包含|不要包含|必须不包含|不得出现|不要出现|禁止出现)\s*"
    r"(?P<value>[^，,。；;！？!\n]+)"
)
CURRENCY_PAIR_RE = re.compile(r"显示\s*金币和公会币")
SENTENCE_BOUNDARY_RE = re.compile(r"[。！？!；;\n]")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    try:
        return NUMBER_VALUES[value]
    except KeyError as exc:  # pragma: no cover - regex and table stay synchronized
        raise HardRequirementFinalizationError(
            f"Unsupported deterministic number: {value!r}"
        ) from exc


def sentence_containing(text: str, start: int, end: int) -> str:
    left = max((match.end() for match in SENTENCE_BOUNDARY_RE.finditer(text, 0, start)), default=0)
    right_match = SENTENCE_BOUNDARY_RE.search(text, end)
    right = right_match.start() if right_match else len(text)
    return text[left:right]


def explicit_locked_position(sentence: str, evidence: str) -> str | None:
    if not re.search(r"必须|固定|不能移动|不可移动|严格保持", sentence):
        return None
    evidence_start = sentence.find(evidence)
    evidence_center = evidence_start + len(evidence) / 2 if evidence_start >= 0 else 0
    candidates: list[tuple[float, str]] = []
    for position, pattern in (
        ("top", r"顶部|上方"),
        ("left", r"左侧|左边"),
        ("right", r"右侧|右边"),
        ("bottom", r"底部|下方"),
        ("center", r"中央|中心"),
    ):
        for match in re.finditer(pattern, sentence):
            candidates.append((abs(match.start() - evidence_center), position))
    return min(candidates)[1] if candidates else None


def page_semantic(requirement: str) -> dict[str, str] | None:
    for value, pattern in PAGE_SEMANTIC_RULES:
        matches = list(pattern.finditer(requirement))
        if matches:
            match = max(matches, key=lambda item: (len(item.group(0)), item.start() * -1))
            return {
                "fact_id": f"page_{value}",
                "value": value,
                "evidence": match.group(0),
            }
    return None


def add_required_element(
    result: list[dict[str, Any]],
    seen: set[str],
    rule: dict[str, str],
    evidence: str,
    position: str | None,
) -> None:
    target = rule["element_target"]
    if target in seen:
        return
    seen.add(target)
    result.append(
        {
            "fact_id": rule["element_fact_id"],
            "target_component_id": target,
            "semantic": rule["semantic"],
            "position": position,
            "evidence": evidence,
        }
    )


def explicit_counts_and_elements(
    requirement: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    counts: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    seen_counts: set[str] = set()
    seen_elements: set[str] = set()
    for match in COUNT_RE.finditer(requirement):
        rule = ELEMENT_BY_NOUN[match.group("noun")]
        target = rule["count_target"]
        if target not in seen_counts:
            seen_counts.add(target)
            counts.append(
                {
                    "fact_id": rule["count_fact_id"],
                    "target_component_id": target,
                    "count": parse_number(match.group("number")),
                    "evidence": match.group(0),
                }
            )
        sentence = sentence_containing(requirement, match.start(), match.end())
        add_required_element(
            elements,
            seen_elements,
            rule,
            match.group(0),
            explicit_locked_position(sentence, match.group(0)),
        )

    if CURRENCY_PAIR_RE.search(requirement):
        evidence = CURRENCY_PAIR_RE.search(requirement).group(0)  # type: ignore[union-attr]
        for fact_id, target, semantic in (
            ("gold_required", "gold_status", "gold_currency"),
            ("guild_currency_required", "guild_currency_status", "guild_currency"),
        ):
            if target not in seen_elements:
                seen_elements.add(target)
                elements.append(
                    {
                        "fact_id": fact_id,
                        "target_component_id": target,
                        "semantic": semantic,
                        "position": None,
                        "evidence": evidence,
                    }
                )

    for match in EXPLICIT_ELEMENT_RE.finditer(requirement):
        rule = ELEMENT_BY_NOUN[match.group("noun")]
        sentence = sentence_containing(requirement, match.start(), match.end())
        add_required_element(
            elements,
            seen_elements,
            rule,
            match.group(0),
            explicit_locked_position(sentence, match.group(0)),
        )
    return counts, elements


def infer_grid_target(
    noun: str | None, counts: list[dict[str, Any]]
) -> tuple[str, str] | None:
    if noun:
        rule = ELEMENT_BY_NOUN[noun]
        return rule["count_target"], rule["count_fact_id"].replace("count_", "grid_", 1)
    grid_candidates = [
        item for item in counts if item["target_component_id"].endswith("_template")
    ]
    if len(grid_candidates) == 1:
        target = grid_candidates[0]["target_component_id"]
        return target, grid_candidates[0]["fact_id"].replace("count_", "grid_", 1)
    return None


def grid_requirements(
    requirement: str, counts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    patterns = (GRID_X_RE, GRID_COLUMNS_ROWS_RE, GRID_ROWS_COLUMNS_RE)
    for pattern in patterns:
        for match in pattern.finditer(requirement):
            resolved = infer_grid_target(match.groupdict().get("noun"), counts)
            if resolved is None:
                continue
            target, fact_id = resolved
            if target in seen:
                continue
            seen.add(target)
            columns = parse_number(match.group("columns"))
            rows = parse_number(match.group("rows"))
            dimension_start = min(match.start("columns"), match.start("rows"))
            dimension_end = max(match.end("columns"), match.end("rows"))
            result.append(
                {
                    "fact_id": fact_id,
                    "target_component_id": target,
                    "columns": columns,
                    "rows": rows,
                    "evidence": requirement[dimension_start:dimension_end],
                }
            )

    count_by_target = {item["target_component_id"]: item["count"] for item in counts}
    for match in PER_ROW_RE.finditer(requirement):
        resolved = infer_grid_target(match.group("noun"), counts)
        if resolved is None:
            continue
        target, fact_id = resolved
        if target in seen or target not in count_by_target:
            continue
        columns = parse_number(match.group("columns"))
        count = count_by_target[target]
        if columns < 1 or count % columns:
            continue
        seen.add(target)
        result.append(
            {
                "fact_id": fact_id,
                "target_component_id": target,
                "columns": columns,
                "rows": count // columns,
                "evidence": match.group(0),
            }
        )
    return result


def explicit_clause_values(pattern: re.Pattern[str], requirement: str) -> list[str]:
    values: list[str] = []
    for match in pattern.finditer(requirement):
        value = match.group("value").strip()
        if value and value not in values:
            values.append(value)
    return values


def derive_hard_requirements(user_requirement: str) -> dict[str, Any]:
    """Return the complete deterministic hard-requirement object."""

    if not isinstance(user_requirement, str) or not user_requirement.strip():
        raise HardRequirementFinalizationError(
            "original user_requirement must contain non-whitespace business text"
        )
    counts, elements = explicit_counts_and_elements(user_requirement)
    return {
        "page_semantic": page_semantic(user_requirement),
        "explicit_counts": counts,
        "grid_requirements": grid_requirements(user_requirement, counts),
        "required_elements": elements,
        "must_include": explicit_clause_values(INCLUDE_RE, user_requirement),
        "must_not_include": explicit_clause_values(EXCLUDE_RE, user_requirement),
    }


def extract_business_requirement(request_document: Any) -> str:
    if not isinstance(request_document, dict):
        raise HardRequirementFinalizationError("request.json must contain an object")
    requirement = request_document.get("user_requirement")
    if not isinstance(requirement, str) or not requirement.strip():
        raise HardRequirementFinalizationError(
            "request.json user_requirement must contain business text"
        )
    return requirement


def finalize_document(plan: Any, user_requirement: str) -> dict[str, Any]:
    """Copy a plan and replace the untrusted LLM hard-requirement object."""

    if not isinstance(plan, dict):
        raise HardRequirementFinalizationError("ui-compose-plan must contain an object")
    result = copy.deepcopy(plan)
    context = result.get("project_context")
    if not isinstance(context, dict):
        raise HardRequirementFinalizationError(
            "ui-compose-plan project_context must contain an object"
        )
    if context.get("user_requirement") != user_requirement:
        raise HardRequirementFinalizationError(
            "plan project_context.user_requirement differs from original business requirement"
        )
    context["hard_requirements"] = derive_hard_requirements(user_requirement)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="LLM-generated ui-compose-plan.json")
    parser.add_argument(
        "--request", required=True, type=Path, help="Authoritative Runner request.json"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Final plan path; defaults to replacing the candidate plan atomically",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output or args.plan
    try:
        requirement = extract_business_requirement(load_json(args.request))
        finalized = finalize_document(load_json(args.plan), requirement)
        write_json_atomic(output_path, finalized)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        HardRequirementFinalizationError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    hard = finalized["project_context"]["hard_requirements"]
    print(
        json.dumps(
            {
                "status": "finalized",
                "output": str(output_path),
                "owner": "deterministic_code",
                "page_semantic": hard["page_semantic"]["value"]
                if hard["page_semantic"]
                else None,
                "explicit_count_count": len(hard["explicit_counts"]),
                "grid_requirement_count": len(hard["grid_requirements"]),
                "required_element_count": len(hard["required_elements"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
