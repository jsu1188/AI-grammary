# Writing Assistant Browser Extension

This is the first browser-side bridge for Writing Assistant.

## Install for local testing

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable Developer mode.
3. Click "Load unpacked".
4. Select this `browser_extension` folder.
5. Start Writing Assistant with realtime recognition enabled.

The extension sends focused browser editor text to `http://127.0.0.1:8766/capture`.

## Current scope

- `input` and `textarea`: reads text and selection offsets, replaces selected text.
- `contenteditable`: reads selected text, HTML fragment, and basic computed CSS segments.
- Rich replacement is still plain text in this first pass. The captured style segments are already sent to the desktop app for the next implementation step.

## Why this exists

Desktop UI Automation can often read browser text, but it cannot reliably see DOM/CSS styles. The content script runs inside the page, so it can use `Selection`, `Range`, and `getComputedStyle()`.
