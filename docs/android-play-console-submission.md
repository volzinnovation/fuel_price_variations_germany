# Android Play Console Submission Checklist

Last reviewed: 2026-03-28

This memo is specific to the current Android app in this repository and the current Google Play draft for Tankzeit.

## Current app behavior

Observed in the current codebase:

- Package name: `de.tankzeit.android`
- Sensitive permissions requested:
  - `ACCESS_FINE_LOCATION`
  - `ACCESS_COARSE_LOCATION`
  - `POST_NOTIFICATIONS`
- The app currently requests location permission on startup and then uses location to:
  - load nearby Diesel and E10 stations
  - center the map
  - sort/filter nearby stations
- A privacy-policy page exists at `https://tankzeit.de/privacy.html`.
- The app links to privacy policy and imprint from the Info tab.
- The app stores favorites locally in `SharedPreferences`.
- The app loads station/statistics JSON from `https://tankzeit.de/...`.
- The app loads live fuel data from `https://creativecommons.tankerkoenig.de/...`.
- The app loads map tiles from `https://tile.openstreetmap.org/...`.
- The app opens external links for:
  - Google Maps / geo navigation
  - project and legal links
- The app does not contain evidence of:
  - ads SDKs
  - analytics SDKs
  - crash-reporting SDKs
  - user accounts or login
  - in-app purchases
  - background location
  - broad file access permissions

Code references:

- `android/app/src/main/AndroidManifest.xml`
- `android/app/src/main/java/de/tankzeit/android/MainActivity.kt`
- `android/app/src/main/java/de/tankzeit/android/location/LocationClient.kt`
- `android/app/src/main/java/de/tankzeit/android/data/FavoritesStore.kt`
- `android/app/src/main/java/de/tankzeit/android/data/TankzeitRepository.kt`
- `android/app/src/main/java/de/tankzeit/android/ui/TankzeitApp.kt`
- `android/app/src/main/java/de/tankzeit/android/ui/components/MapLibreMap.kt`
- `android/app/src/main/java/de/tankzeit/android/ui/screens/InfoScreen.kt`
- `android/app/src/main/java/de/tankzeit/android/ui/screens/StationDetailSheet.kt`

## Remaining pre-submit decisions

1. Publish the privacy policy URL in Play Console.

- Use `https://tankzeit.de/privacy.html`.

2. Decide whether to keep startup-triggered location permission.

- Current review risk is higher because the app requests precise location immediately.
- A user-initiated permission flow would be easier to defend in review.

3. Decide whether to keep `ACCESS_FINE_LOCATION`.

- If only nearby sorting and coarse map centering are needed, `ACCESS_COARSE_LOCATION` may be sufficient.

## Recommended Data safety answers

Use the conservative answer set below for the current build:

### Data collection and security

- Does your app collect or share any of the required user data types? `Yes`
- Is all user data collected by your app encrypted in transit? `Yes`
- Do you provide a way for users to request that their data is deleted? `No`

Rationale:

- Location-related data can be inferred by third-party map tile requests once the app centers near the user.
- Tile and API requests are HTTPS.
- Favorites remain local to the device and do not create a server-side account data set.

### Data types

- `Precise location`: `Collected`, `Shared`
- `Approximate location`: if Play requires both, answer consistently with the final location declaration
- `Personal info`: `No`
- `Financial info`: `No`
- `Health and fitness`: `No`
- `Messages`: `No`
- `Photos and videos`: `No`
- `Audio files`: `No`
- `Files and docs`: `No`
- `Calendar`: `No`
- `Contacts`: `No`
- `App activity`: `No`
- `Web browsing`: `No`
- `Crash logs`: `No`
- `Diagnostics`: `No`
- `Other app performance data`: `No`
- `Device or other IDs`: `No`

### Usage and handling for location

If you declare location for the current build, use:

- Collected: `Yes`
- Shared: `Yes`
- Processed ephemerally: `No`
- Required or optional: `Optional`
- Purpose: `App functionality`

Do not mark:

- `Analytics`
- `Advertising or marketing`
- `Developer communications`
- `Account management`
- `Fraud prevention, security, and compliance`

## Recommended App content answers

### Privacy policy

- `Required`
- Add `https://tankzeit.de/privacy.html`

### Ads

- `No`

### App access

- `No, all functionality is available without special access`

### Target audience and content

- `18 and over`

### Content ratings

Expected questionnaire answers:

- Violence: `No`
- Fear/horror: `No`
- Sexual content: `No`
- Gambling: `No`
- Drugs/alcohol/tobacco encouragement: `No`
- User-generated content: `No`
- User-to-user sharing/public posting: `No`
- Purchases/real-money transactions inside app: `No`

### News and Magazine apps

- `No`

### COVID-19 contact tracing / status

- `No`

## Store listing checklist

Before sending the app for review:

- Upload the signed `.aab`
- Add app title
- Add short description
- Add full description
- Add support email
- Add website URL
- Add privacy policy URL
- Add app icon
- Add feature graphic
- Add screenshots
- Choose category
- Choose tags
- Complete Data safety
- Complete App content declarations
- Complete content rating questionnaire
- Confirm no ads label
- Check release notes

## Local prep in this repo

- `scripts/generate_android_play_store_assets.py`: creates Play Store icon, feature graphic and metadata text files under `output/play-store/android/`
- `android/keystore.properties`: local release-signing file with keystore path and passwords
- `android/app/build.gradle.kts`: reads signing data from `android/keystore.properties` or environment variables
