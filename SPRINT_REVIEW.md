# Sprint Review: Tankzeit iPhone App

Sprint status: Completed, submitted, and legally required post-submission update delivered
Date: 2026-03-04

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
- Info
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
- Legal parity update completed:
  - Added in-app Info tab in iPhone app with content aligned to the web Info page.
  - Updated min/max range logic in iPhone app:
    - If current Tankerkönig price is above `stats.maxabs`, current price is used as max.
    - If current Tankerkönig price is below `stats.minabs`, current price is used as min.
  - Applied the same min/max adjustment behavior in web app (`index.html`, `e10.html`, `favoriten.html`) for consistent user-visible values.
  - Added unit tests for range-adjustment behavior and executed on simulator.

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
- Test target added (`TankzeitTests`) and integrated into scheme.
- Simulator test run passed:
  - `xcodebuild -project iphone/Tankzeit.xcodeproj -scheme Tankzeit -destination 'platform=iOS Simulator,name=iPhone 16' test`
  - Result: 4 tests, 0 failures.

## Key Decisions

- Chose remote-first data access due to requirement that offline strategy is not possible.
- Preserved MVVM + MapKit patterns for consistency with prior app architecture.
- Added legal transparency content both in App Store assets and inside the app (Info tab) to satisfy requirements.
- Standardized price-range semantics across web and iOS by combining statistical bounds with live current price.

## Risks / Follow-up Checks

- Confirm final App Store Connect privacy labels match runtime behavior of latest build.
- Keep third-party API assumptions (rate limits/availability) under monitoring.
- Verify production signing/provisioning and bundle ID consistency across release pipeline.
- Re-run full App Store release validation whenever data-display rules change (especially user-visible min/max semantics).

## Artifacts Added/Updated

- `iphone/` (new iOS app project and sources)
- `DATENSCHUTZ.md`
- `SPRINT_REVIEW.md`
- `app-playbook.md`

## Retrospective Notes

What worked well:
- Reusing the known SwiftUI/MapKit/MVVM baseline accelerated delivery.
- Scripted artwork generation enabled rapid iteration with deterministic outputs.
- Small focused tests around business rules (`adjustedAbsoluteRange`) reduced regression risk for future UI/data changes.

What to improve:
- Freeze icon direction earlier to reduce design loop churn.
- Run App Store asset validation checklist earlier in the sprint.
- Add test target at initial scaffold time to avoid late-cycle scheme/project churn.
