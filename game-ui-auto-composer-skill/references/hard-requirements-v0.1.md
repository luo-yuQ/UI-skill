# Deterministic Hard Requirements v0.1

`scripts/finalize_hard_requirements.py` is the sole owner of the final
`project_context.hard_requirements`. Its only fact source is the original
business `request.json.user_requirement`.

It does not inspect A, B, `reference_application`, `generation_constraints`, or
the LLM's proposed hard-requirement values.

## Supported page semantics

The parser uses a fixed keyword mapping and preserves the matched source phrase
as evidence:

- `充值页面`、`充值界面`、`游戏充值页面` → `recharge_page`;
- `公会商店`、`公会商城` → `guild_shop`;
- `商城`、`商店`、`商城页面`、`商店页面` and `shop page` → `shop`.

`shop` reuses the stable ID in
`game-ui-layout-reference-analyzer/references/game-ui-page-taxonomy.md`.
`recharge_page` and `guild_shop` are existing Composer business semantics that
are more specific than the A1 visual page taxonomy. When no supported explicit
page phrase exists, `page_semantic` is `null`.

## Supported explicit counts

Counts require a number adjacent to a recognized noun, such as:

```text
4个奖励
6个商品
3个按钮
一个刷新按钮
```

Supported numbers are Arabic non-negative integers and the simple Chinese forms
`一`、`两`/`二` through `十`. Recognized targets are classification tabs,
products, rewards, generic buttons, refresh/purchase buttons, reward lists,
character avatars, and countdowns.

An explicit count also establishes that its named element is required. Counts
are never read from A visible-item evidence.

## Supported explicit grids

The parser supports a recognized target noun followed by:

```text
商品按5x2排列
商品按5×2排列
商品按5列2行排列
商品做成两行五列
```

It also supports `商品每行5个` only when the same business requirement contains
an explicit total for that target and the total is evenly divisible by the
column count. A bare grid may be associated only when exactly one unambiguous
repeat target exists in the parsed user counts.

A-derived grids remain valid in layout reuse and generation constraints but do
not enter this ledger.

## Supported required elements

The conservative explicit prefixes are:

```text
必须有
必须包含
必须包括
需要
要有
包含
显示
```

They apply only to the recognized element nouns. `显示金币和公会币` is also a
fixed rule producing the two explicit currency elements. A hard position is
recorded only when the same clause uses an explicit lock such as `必须` or
`固定` together with a supported direction.

## Supported include and exclude clauses

`must_include` accepts only values following `必须包含`、`必须包括`、`需要包含`
or `要包含`. `must_not_include` accepts only values following explicit forms
such as `不得包含`、`不要包含`、`禁止出现` or `不要出现`.

Stored values must be exact substrings of the original business requirement.

## Intentionally unsupported

v0.1 deliberately does not infer:

- elements, counts, grids, panels, navigation, or status areas from “参考这个布局”;
- counts or grids from A/B evidence;
- vague quantities such as “多个”“若干”“丰富一些”; 
- compound Chinese numerals above the simple supported forms, such as `十二` or
  `二十五`;
- synonyms or page semantics outside the fixed mappings;
- a grid target when multiple parsed repeat targets make a bare `5x2` ambiguous;
- rows for `每行5个` without an explicit compatible total;
- arbitrary free-form semantic interpretation through an LLM.

Unsupported or ambiguous language produces no hard fact. It must not be filled
from references or model judgment.
