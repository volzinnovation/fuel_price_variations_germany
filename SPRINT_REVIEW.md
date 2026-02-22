# Sprint Review: Tankzeit iPhone App

Sprint status: Completed and submitted
Date: 2026-02-22

## Goal

Build a new native iPhone app for Tankzeit using SwiftUI + MapKit + MVVM, adapted from the existing iOS playbook and aligned to the current web product.

## Scope Delivered

- New iPhone project scaffolded in `iphone/` using XcodeGen.
- App architecture implemented with clear layers:
  - `App/`, `Models/`, `Services/`, `ViewModels/`, `Views/`, `Resources/`
- Product tabs delivered (matching requested scope):
  - Diesel
  - E10
  - Favoriten
  - Statistik
- Info tab intentionally omitted (content moved to store metadata).
- Remote-first data strategy implemented (no offline bundle flow):
  - `https://tankzeit.de/data/stations.json`
  - `https://tankzeit.de/data2/...`
  - Tankerkönig live APIs for nearby stations and prices
- Station detail experience implemented:
  - Mini map
  - Favorite toggle
  - Navigation handoff (Apple/Google Maps)
  - Hourly variation chart and best-hour visualization
- Favorites persistence via local `UserDefaults` store.
- Statistics screen implemented using management boxplot dataset with date and fuel controls.

## Design & Branding Outcome

- App icon/artwork pipeline created via script:
  - `iphone/scripts/generate_artwork.py`
- Final logo based on user-provided proposal image (`Gemini_Generated_App_logo_tankzeit.png`) with script-driven rescaling to all required icon sizes.
- SVG wrapper source produced for reproducibility:
  - `iphone/Tankzeit/Resources/logo/proposal_logo.svg`

## Compliance & Policy Work

- App Store metadata text prepared in German.
- Dedicated privacy policy document created:
  - `DATENSCHUTZ.md`
- Apple icon validation issue fixed:
  - Large app icon now exported without alpha channel (RGB), matching App Store requirement.

## Build & Validation

- Xcode project generation succeeded:
  - `cd iphone && xcodegen generate`
- Swift compile sanity/typecheck passed with simulator target.
- App icon alpha-channel issue resolved and verified after regeneration.

## Key Decisions

- Chose remote-first data access due to requirement that offline strategy is not possible.
- Preserved MVVM + MapKit patterns for consistency with prior app architecture.
- Moved informational project text from in-app tab to App Store/policy artifacts.

## Risks / Follow-up Checks

- Confirm final App Store Connect privacy labels match runtime behavior of latest build.
- Keep third-party API assumptions (rate limits/availability) under monitoring.
- Verify production signing/provisioning and bundle ID consistency across release pipeline.

## Artifacts Added/Updated

- `iphone/` (new iOS app project and sources)
- `DATENSCHUTZ.md`
- `SPRINT_REVIEW.md`
- `app-playbook.md`

## Retrospective Notes

What worked well:
- Reusing the known SwiftUI/MapKit/MVVM baseline accelerated delivery.
- Scripted artwork generation enabled rapid iteration with deterministic outputs.

What to improve:
- Freeze icon direction earlier to reduce design loop churn.
- Run App Store asset validation checklist earlier in the sprint.
