# QA and repair reference

## Repair order

1. Fix missing/corrupt file or save failure.
2. Fix wrong slide count, invalid sequence, or empty slides.
3. Restore missing titles, charts, images, tables, and citations.
4. Fix clipped/out-of-bounds objects and broken images.
5. Fix text overflow and harmful overlaps.
6. Normalize typography, spacing, alignment, palette, footer, and numbering.
7. Render affected slides and recheck them.

## Repair policy

- Preserve correct content and working objects.
- Prefer moving/resizing/shortening over deleting and recreating a slide.
- Shorten copy before shrinking body text below readable size.
- Simplify a crowded visual before adding more explanatory text.
- Retry transient COM busy/rejected-call failures at most twice with brief backoff.
- Use one bounded repair pass. If fatal issues remain, switch to the Python backend and record the MCP failure.

## Fatal issues

- Presentation missing, empty, unreadable, or unsaved
- Required slide or title missing
- Expected analytical visual absent
- Broken image or invalid object required by the story
- Object materially outside the slide
- Unresolved overflow or overlap that hides content

## Warnings

- Possible minor overlap or conservative overflow estimate
- Too many font families
- Repeated plain background/layout
- Weak source placement or non-critical inconsistency

Warnings must be logged; promote them to fatal only when rendered evidence shows impaired readability or meaning.
