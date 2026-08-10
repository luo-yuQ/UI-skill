# Composer v2 Workflow

## Contract boundary

Input:

- one valid `ui-compose-input` v2 object;
- one ordinary-language user requirement;
- one complete authoritative A final analysis;
- one complete authoritative B2 style profile.

Output:

- one valid `ui-compose-plan` v2 object expressing a new UI design intent.

No raw-image, legacy asset-placement, or alternate successful core mode exists.

## Step 1: Validate v2 input

Resolve and validate the Composer, A, and B schemas. Reject `pics`, top-level `assets`, empty user requirements, and unexpected fields. Stop on any error; never partially compose or repair upstream evidence.

## Step 2: Interpret the user target

Establish what is being designed: target business goal, page scope, content semantics, exact counts, requested interactions, requested changes, orientation, resolution, constraints, and explicit visual preferences.

Create the target model before selecting reference material.

## Step 3: Extract applicable A principles

Review A regions, relationships, groups, hierarchy, rules, excluded content, and uncertainty. Select portable organization such as grouping, adjacency, containment, alignment, proportions, repetition, focal order, and action placement.

Do not copy reference text, theme, item identity, character identity, event meaning, exact pixel coordinates, or irrelevant components.

## Step 4: Select applicable B traits

Index B traits by dimension and classification. Prefer relevant `stable`; condition relevant `secondary`; scope `local` narrowly; do not silently choose `conflicting`; do not turn `uncertain` into fact.

## Step 5: Resolve authority and scope

Apply the priority:

```text
explicit user target > A for target content/count/change
explicit user visual request > B
A > B for formal layout
reliable unoverridden B > unsupported style invention
```

Prevent A/B reference semantics from leaking into target content.

## Step 6: Design new pages and components

Create only user-justified pages. Build a new target component hierarchy that satisfies target content and interactions. Add low-risk structural components only when required for completeness and record the assumption.

## Step 7: Adapt layout

Map useful A relations onto the new tree. Recalculate for target counts, semantics, orientation, resolution, and safe area. Prefer normalized and relational intent over copied coordinates.

## Step 8: Derive target visual direction

Convert selected B evidence and explicit user visual changes into page-specific directives. Preserve trait traceability. Do not copy the B profile wholesale or invent unsupported treatment.

## Step 9: Define behavior

Add only necessary interactions, state changes, feedback, and page navigation. Allow empty navigation for a single-page requirement. Keep behavior engine-neutral.

## Step 10: Define generation constraints

Record exact counts, required and forbidden content, content zones, focal order, separability, overlap, readability, clean boundaries, cutout suitability, and reference-fidelity limits. Do not compile a prompt.

## Step 11: Record decisions and risk

Complete `reference_application` for adopted, adapted, ignored, and rejected A principles and for material B trait decisions. Record low-risk gaps as assumptions and material conflicts or uncertainty as warnings.

## Step 12: Validate and return

Validate the completed object against `schemas/ui-compose-plan.schema.json`. Return exactly one JSON object with no surrounding prose or downstream adapter output.

## Review gates

- User counts and explicit requirements win over reference counts.
- Component IDs and semantics describe the target, not renamed A objects.
- Every adopted style directive is traceable to B or an explicit user override.
- Local traits are not globalized.
- Conflicting and uncertain traits are not hard requirements.
- Reference-specific narrative and business content do not appear without user authorization.
- Generation constraints express structure, not a full prompt.
- No v1 `asset_usages`, `missing_assets`, or engine-specific fields remain.
