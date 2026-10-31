# Attribution logos

`tmdb.svg` and `justwatch.svg` are **placeholder text wordmarks** drawn for this project. They are not
the official TMDB or JustWatch logos.

The About screen (`src/screens/Settings.tsx`) loads them from `/attribution/`. TMDB's API terms require its
logo next to the attribution notice, and `docs/LEGAL.md` asks for the JustWatch logo too.

**Before any public distribution**, replace both files with the official assets, keeping the same file names:

- TMDB: download from TMDB's logos and attribution page (themoviedb.org → About → Logos & Attribution) and
  follow its usage rules (approved colours, no alterations, less prominent than the app's own branding).
- JustWatch: download from JustWatch's brand / press assets page and follow its attribution guidelines.

Keep the files SVG (or update the paths in `Settings.tsx`). The About screen shows them at 28 px high.
