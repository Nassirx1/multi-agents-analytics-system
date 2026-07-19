# PowerPoint MCP tool playbook

## Safe sequence

1. `file(create)` and capture `session_id`.
2. `slide(create)` with a suitable layout or blank canvas.
3. Add a background shape and send it behind content when needed.
4. Add/format titles and concise text.
5. Add charts, tables, or allow-listed images; inspect returned shape names.
6. Add notes, alt text, sources, and footer/slide numbers.
7. `file(save)`.
8. Inspect slides/shapes/text and export slide images.
9. Repair fatal issues, save, inspect, and export again.
10. Close with save enabled.

## Tool boundaries

Use only file, slide, shape, text, chart, table, image, placeholder, notes, alignment, and export operations exposed by Agent 9. Never use VBA, arbitrary evaluation, slideshow control, hyperlinks to untrusted targets, or unrelated files.

## Known operational details

- PowerPoint coordinates are points; use a 960 x 540 canvas for 13.333 x 7.5 inch widescreen slides.
- Query shape names after creation; do not assume default names.
- The server may require `#RRGGBB` for text colors while shape fills accept `RRGGBB`.
- Some chart operations retain unused default data cells; blank unused rows/series and hide an unnecessary legend.
- Treat `0x800AC472`, `RPC_E_CALL_REJECTED`, and explicit server-busy messages as transient. Retry with bounded backoff.
