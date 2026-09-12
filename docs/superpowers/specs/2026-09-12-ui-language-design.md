# EN/VI UI Language Selection Design

**Date:** 2026-09-12  
**Status:** Approved in chat

## Goal

Add an English/Vietnamese application-language selector to the launcher and
use the selected language throughout CapCap's user-facing desktop UI.

## Requirements

- English remains the default and the fallback for untranslated strings.
- The launcher exposes `English` and `Tiếng Việt` in a language selector.
- The selected language is saved in `QSettings` under `ui_language` and is
  restored on the next launch.
- Changing the selector updates the visible launcher immediately.
- The editor is constructed with the selected language, including labels,
  buttons, tooltips, placeholders, menus, dialogs, progress states, and
  CapCap-generated success/error messages.
- Existing project files, settings keys, pipeline identifiers, resource IDs,
  and backend/API payloads remain unchanged.
- Technical/product names that are already common UI vocabulary may remain in
  English, including CPU, GPU, OCR, TTS, API, SRT, FFmpeg, Whisper, Piper,
  CapCut, VieNeu, and Timeline where the English term is clearer.
- Raw third-party diagnostics from FFmpeg, model runtimes, and remote APIs
  remain unchanged; the surrounding CapCap notification is localized.
- No new dependency or Qt `.qm` build pipeline is introduced.

## Recommended architecture

Create `ui/i18n.py` as the single localization boundary. It owns:

- supported language codes and normalization (`en`, `vi`);
- the current process language;
- reading/writing `ui_language` through the existing CapCap QSettings
  namespace; and
- `t(source_text, **values)`, which returns the Vietnamese catalog entry when
  Vietnamese is active and otherwise returns the original English source.

The catalog uses the existing English UI text as keys. This keeps the default
English copy in one readable place, avoids a second message-ID layer, and
allows missing translations to fall back safely. Templates with runtime
values are translated before formatting so placeholders and raw diagnostic
details are preserved.

Static UI construction in the existing view, controller, and widget modules
will call `t(...)`. Runtime calls that set user-visible text will use the same
helper. Launcher has a small retranslation method for its known controls and
rebuilds recent-project cards after a language change so the current launcher
is immediately consistent. The editor does not need a live language toggle:
the launcher selection is applied before `VideoTranslatorGUI` builds its
widgets; returning to the launcher creates a fresh window using the persisted
language.

## Combo-box and persistence rule

Display labels are translated, but every behavior-affecting combo keeps a
stable `itemData` value. Reads used by workflow, export, styling, voice, and
settings code use that stable value (or a canonical helper) rather than a
translated `currentText()`. Existing persisted English values remain readable
for backward compatibility.

This prevents a Vietnamese label such as `Lấp đầy` from becoming a pipeline
mode or project-state value while allowing the user to see localized choices.

## Coverage

Localization changes cover the user-facing text in:

- launcher and resource manager;
- start, advanced, preview, timeline, and inspector views;
- subtitle, rewrite, export, progress, voice-clone, and editor dialogs;
- controller-generated prompts, status messages, warnings, and confirmations;
- UI utility helpers that show file dialogs or message boxes; and
- dynamic labels updated during pipeline, preview, transcription, translation,
  voice generation, and export.

Backend algorithm messages, serialized data, prompt files, model names, and
third-party raw errors are outside the UI localization boundary.

## Data flow

```text
QApplication
  -> LauncherWindow reads ui_language
  -> selector change: normalize -> save QSettings -> retranslate launcher
  -> accept project
  -> VideoTranslatorGUI and child UI builders call t(...)
  -> runtime UI updates call t(...) with the active language
```

## Verification

- Add stdlib `unittest` coverage for language normalization, English fallback,
  Vietnamese translation, template formatting, and persistence behavior.
- Verify that translated combo labels still return their original stable data
  values.
- Run the localization tests, compile/import checks for touched Python files,
  and a headless Qt smoke check when the local Qt runtime is available.
- Manually inspect launcher English/Vietnamese switching and at least one
  complete editor flow covering a dialog, progress state, and error message.
