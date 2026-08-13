# Game UI Asset Taxonomy v0.1 — Stage2-A v0.2 Boundaries

`semantic_type` answers only: “What is this visual element?” It does not decide whether the element should be extracted. A `text` candidate may be preserved as special artwork, while a `button` may require advanced handling because text is baked into it.

Use exactly these ten categories:

| Type | Definition and boundary |
| --- | --- |
| `background` | A scene-wide or page-wide visual surface behind the primary UI content. Do not use for a bounded container. |
| `panel` | A bounded visual surface that contains, groups, or organizes multiple visual elements. A card shell may be a panel even when the whole card is clickable. Do not use for a full-page background or a thin ornamental border. |
| `button` | A visually independent control surface or the button's own complete visible treatment, not every clickable region and not all content in a business module. Classify by visual identity even when baked text prevents direct extraction. |
| `icon` | A compact symbolic graphic representing an action, status, resource, or category. Do not use for large narrative artwork. Containment inside a panel, button, or other container does not remove its independent semantic identity. |
| `illustration` | A substantial pictorial subject or scene used as featured artwork. Do not use for small symbols or purely ornamental flourishes. Containment inside a panel, button, or other container does not remove its independent semantic identity. |
| `frame` | A border, bezel, slot outline, portrait surround, or other visual enclosure whose main identity is framing content. Containment inside a panel, button, or other container does not remove its independent semantic identity. |
| `progress_bar` | A track, fill, or complete visual indicator whose primary identity is measurable progress or quantity. Containment inside a panel, button, or other container does not remove its independent semantic identity. |
| `decoration` | A non-semantic visual embellishment such as a flourish, divider ornament, sparkle, badge treatment, or corner accent. Do not use for functional icons. Containment inside a panel, button, or other container does not remove its independent semantic identity. |
| `text` | Visible lettering, numerals, labels, or artistic typography whose primary identity is textual. It may still be image-worthy special lettering. Containment inside a panel, button, or other container does not remove its independent semantic identity. |
| `unknown` | A visible candidate whose semantic identity cannot be determined reliably from the image. Use only when none of the other nine types is defensible. |

Interactivity is not the sole test for `panel` versus `button`. A clickable card may contain a panel, illustration, icon, decoration, text, and a visually independent button; each child keeps its semantic identity. Parent and child candidates may both be present with overlapping bboxes.
