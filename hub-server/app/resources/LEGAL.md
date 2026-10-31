# Legal notice & disclaimer (version 1.0)

Shown as a full-screen gate on first run and before the first IPTV/M3U source is added; repeated in Settings → About and in the Add Source dialog. Acceptance is stored per profile with version + timestamp.

> **StreamHub is a media player and organizer.** It does not provide, host, sell, promote or distribute any channels, playlists, subscriptions, EPG data or streams.
>
> You may only add sources you are **legally authorized** to access. You are **solely responsible** for the sources you configure and for complying with the laws of your jurisdiction and the rights of content owners. The authors and contributors of StreamHub accept no liability for how you use this software.
>
> Streaming-service names and logos are trademarks of their respective owners. StreamHub is not affiliated with, endorsed by or certified by any of them, including Plex, Jellyfin, Netflix, Hulu, Disney, Amazon, Warner Bros. Discovery, Apple, NBCUniversal or Paramount.
>
> This product uses the TMDB API but is not endorsed or certified by TMDB. Availability ("where to watch") data is provided by JustWatch.

Buttons: **I accept** (default focus) · **Exit**. The Accept button is disabled for 3 seconds so the text is at least glanced at.

## Attribution placement
- Settings → About: TMDB logo + notice, JustWatch logo + "Availability data by JustWatch".
- Title detail "Available on" row footer: "Availability data by JustWatch".
- Search results footer: same.

## Engineering rules
- Never bundle provider logos; load from TMDB `logo_path`.
- Deep links only open apps the user has installed; never scrape or replay DRM content.
- Encrypt source credentials at rest; redact credentials from logs and from URLs shown in the UI.
