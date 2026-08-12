# Game UI Asset Taxonomy v0.1

`semantic_type` answers only: “What is this visual element?” It does not decide whether the element should be extracted. A `text` candidate may be preserved as special artwork, while a `button` may require advanced handling because text is baked into it.

Use exactly these ten categories:

| Type | Definition and boundary |
| --- | --- |
| `background` | A scene-wide or page-wide visual surface behind the primary UI content. Do not use for a bounded container. |
| `panel` | A bounded surface that groups or supports UI content. Do not use for a full-page background or a thin ornamental border. |
| `button` | A visually identifiable interactive control or its complete visible treatment. Classify by identity even when baked text prevents direct extraction. |
| `icon` | A compact symbolic graphic representing an action, status, resource, or category. Do not use for large narrative artwork. |
| `illustration` | A substantial pictorial subject or scene used as featured artwork. Do not use for small symbols or purely ornamental flourishes. |
| `frame` | A border, bezel, slot outline, portrait surround, or other visual enclosure whose main identity is framing content. |
| `progress_bar` | A track, fill, or complete visual indicator whose primary identity is measurable progress or quantity. |
| `decoration` | A non-semantic visual embellishment such as a flourish, divider ornament, sparkle, or corner accent. Do not use for functional icons. |
| `text` | Visible lettering, numerals, labels, or artistic typography whose primary identity is textual. It may still be image-worthy special lettering. |
| `unknown` | A visible candidate whose semantic identity cannot be determined reliably from the image. Use only when none of the other nine types is defensible. |
