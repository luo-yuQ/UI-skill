# Cross-Reference Conflicts

Record conflict when reliable B1 analyses provide incompatible evidence for the same visual dimension and normalization cannot responsibly merge it.

Conflict detection operates on atomic traits. Each cited reference must support the complete alternative assigned to it; partial support does not establish that alternative.

## Conflict types

- Rendering: semi-realistic versus flat cartoon or stylized cel shading.
- Color: cool low-saturation blue-gray versus warm high-saturation red.
- Material: rough stone or black iron versus glossy plastic or bright glass.
- Shape: sharp slender forms versus rounded heavy forms.
- Decoration: restrained ornament versus dense elaborate ornament.
- World/theme: medieval fantasy versus modern science fiction.

## Procedure

1. Split compound wording until each alternative is atomic and comparable.
2. Keep each alternative trait and only the B1 `asset_id` values that support its full meaning.
3. Record the union in the conflict trait's provenance fields.
4. Record relevant counterevidence in `contradicting_references` when applicable.
5. Describe the incompatible directions precisely in the conflict description.
6. Use user group context only to explain or transparently weight evidence; never erase the lower-weight evidence.
7. Add an `unresolved_conflicts` entry when the available evidence cannot settle the conflict.
8. Reduce affected trait or profile confidence when conflict remains material.

Do not hide conflict, silently select a preferred side, or fabricate a unifying trait. In particular, do not collapse "semi-realistic" and "flat cartoon" into "stylized rendering" unless explicit B1 evidence supports that complete higher-level trait. Broad wording is not a resolution.

Before finalizing a conflict, ask whether every supporting reference supports its full alternative. Remove partial references, split the alternative, or lower classification confidence when the answer is no. Conflict records are descriptive evidence, not requests for the Composer to choose a design.
