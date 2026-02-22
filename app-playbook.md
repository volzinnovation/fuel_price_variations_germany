# iPhone App Playbook (Tankzeit Learnings)

Last updated: 2026-02-22
Scope: Practical implementation guide for future iPhone apps in this workspace.

## 1) Default Technical Stack

- Swift 5 + SwiftUI
- MapKit (iOS 17 APIs)
- Lightweight MVVM
- XcodeGen project management (`project.yml`)
- Local simple persistence (`UserDefaults`) for user state

## 2) Recommended Project Layout

Use this structure from day 1:

- `iphone/<AppName>/App`
- `iphone/<AppName>/Models`
- `iphone/<AppName>/Services`
- `iphone/<AppName>/ViewModels`
- `iphone/<AppName>/Views`
- `iphone/<AppName>/Resources/Assets.xcassets`
- `iphone/scripts`

Keep domain types in `Models`, network/file fetchers in `Services`, and view state/orchestration in `ViewModels`.

## 3) Data Strategy Decision First

Before coding UI, explicitly choose one:

- Offline-first (bundled baseline + optional import)
- Remote-first (live APIs / hosted JSON)

For Tankzeit, remote-first was required. Future apps should not copy offline bundle flows unless explicitly needed.

## 4) Runtime Patterns That Scaled

- Single shared `AppViewModel` for cross-tab state.
- Location service as dedicated observable service with clear authorization lifecycle.
- Separate repositories for domain data loading (`TankzeitRepository` pattern).
- Map/list/detail sharing one selected-item model to avoid duplicated state.

## 5) Product Mapping Pattern (Web -> Native)

When adapting from web app:

1. Extract exact tabs, endpoints, and interaction flows from HTML/JS.
2. Recreate parity screens first.
3. Apply native improvements second (sheet UX, map handoff, chart readability).
4. Remove in-app screens explicitly deemed non-essential (e.g., info tab) and move content to store/legal docs.

## 6) Asset Pipeline Playbook

- Keep one script as source of truth for icon generation:
  - `iphone/scripts/generate_artwork.py`
- If a user provides a proposal image, build pipeline from that source and regenerate all icon sizes automatically.
- Always flatten App Store icon outputs to RGB (no alpha) to avoid rejection.

## 7) Build & Verification Commands

From repo root:

```bash
cd iphone
xcodegen generate
```

Compile sanity (sim target):

```bash
xcrun --sdk iphonesimulator swiftc \
  -module-cache-path /tmp/tankzeit-swift-module-cache \
  -target arm64-apple-ios18.0-simulator \
  -typecheck \
  iphone/Tankzeit/App/*.swift \
  iphone/Tankzeit/Models/*.swift \
  iphone/Tankzeit/Services/*.swift \
  iphone/Tankzeit/ViewModels/*.swift \
  iphone/Tankzeit/Views/*.swift
```

Notes:
- In restricted/sandboxed environments, `-module-cache-path` avoids permission failures on default cache directories.

## 8) App Store Readiness Checklist

- App icon set complete and valid.
- Large icon (1024) has no alpha channel.
- Usage descriptions in `Info.plist` match actual runtime behavior.
- Privacy disclosures aligned with actual data flow and third-party endpoints.
- Metadata complete: subtitle, promo text, description, keywords, support URL.

## 9) EU/DE Privacy & Legal Checklist

- Provide a clear privacy policy markdown/file in repo and publication channel.
- Include lawful basis references (DSGVO Art. 6), rights, controller contact, and complaint rights.
- Document third-party data sources/processors and transfer considerations.
- Ensure store labels and policy text are kept in sync with each release.

## 10) Release Hygiene

- Avoid committing Xcode user state files.
- Keep generated assets deterministic via scripts.
- Track major decisions in sprint review docs.
- Capture release-specific known risks and post-release checks.

## 11) Suggested Start Template for Next App

Use this prompt skeleton:

```text
Build a new iPhone app in /iphone using SwiftUI + MapKit + MVVM.
Use XcodeGen and keep App/Models/Services/ViewModels/Views separation.
Match the existing web product flows first, then apply native UX polish.
Choose and document data strategy (offline-first or remote-first) before implementation.
Create script-based app icon generation and validate App Store icon requirements.
```
