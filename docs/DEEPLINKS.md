# Hand-off service registry (seed data)

Verified status as of Oct 2026 from community reports; **test on real hardware**. Registry is stored in the `service_registry` table and editable in Settings → Sources → Streaming services → Advanced. Launch order: title intent (ACTION_VIEW + setPackage) → leanback launch intent → web URL / QR.

| key | name | tmdb_provider_ids | android_package | android_title_uri | web_title_url | supports_title_link | notes |
|---|---|---|---|---|---|---|---|
| netflix | Netflix | 8 | com.netflix.ninja | https://www.netflix.com/title/{netflix_id}?source=30 | https://www.netflix.com/title/{netflix_id} | true | source=30 required; netflix_id from Wikidata P1874 by TMDB id |
| youtube | YouTube | 192 | com.google.android.youtube.tv | https://www.youtube.com/watch?v={id} | https://www.youtube.com/watch?v={id} | true | used for trailers |
| hulu | Hulu | 15 | com.hulu.livingroomplus | https://www.hulu.com/watch/{hulu_id} (component .WKFactivity) | https://www.hulu.com/watch/{hulu_id} | partial | movies/episodes play; series → series page |
| prime | Prime Video | 9,119 | com.amazon.amazonvideo.livingroom | https://app.primevideo.com | https://www.primevideo.com/detail/{asin} | false | app launch only on TV |
| disney | Disney+ | 337 | com.disney.disneyplus | https://www.disneyplus.com/browse/entity-{id} | https://www.disneyplus.com/browse/entity-{id} | unverified | Wikidata P13902 |
| max | HBO Max | 1899 | com.wbd.stream | https://play.max.com/video/watch/{id} | https://play.max.com/video/watch/{id} | unverified | unreliable |
| peacock | Peacock | 386,387 | com.peacocktv.peacockandroid | — | https://www.peacocktv.com/watch/playback/vod/{id} | false | no TV deep link |
| paramount | Paramount+ | 531 | com.cbs.ott | — | https://www.paramountplus.com | false | |
| appletv | Apple TV+ | 350 | com.apple.atve.androidtv.appletv | — | https://tv.apple.com | false | |
| tubi | Tubi | 73 | com.tubitv | — | https://tubitv.com | false | |
| pluto | Pluto TV | 300 | tv.pluto.android | — | https://pluto.tv | false | |

Android manifest needs a `<queries>` block listing these packages. Each registry row has a "Test link" action in the UI.
