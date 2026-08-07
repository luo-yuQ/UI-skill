# Cross-Reference Conflicts

Record conflict when reliable B1 analyses provide incompatible evidence for the same visual dimension and normalization cannot responsibly merge it.

## Conflict types

- Rendering: semi-realistic versus flat cartoon or stylized cel shading.
- Color: cool low-saturation blue-gray versus warm high-saturation red.
- Material: rough stone or black iron versus glossy plastic or bright glass.
- Shape: sharp slender forms versus rounded heavy forms.
- Decoration: restrained ornament versus dense elaborate ornament.
- World/theme: medieval fantasy versus modern science fiction.

## Procedure

1. Keep each alternative trait and its supporting B1 `asset_id` values.
2. Record the union in the conflict trait's provenance fields.
3. Record relevant counterevidence in `contradicting_references` when applicable.
4. Use user group context only to explain or transparently weight evidence; never erase the lower-weight evidence.
5. Add an `unresolved_conflicts` entry when the available evidence cannot settle the conflict.
6. Reduce affected trait or profile confidence when conflict remains material.

Do not hide conflict, silently select a preferred side, or fabricate a unifying trait. Conflict records are descriptive evidence, not requests for the Composer to choose a design.
