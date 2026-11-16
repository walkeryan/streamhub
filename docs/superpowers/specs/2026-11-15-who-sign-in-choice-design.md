# "Who's watching?": each person signs in with a PIN, a password or an arrow pattern

Status: approved 2026-11-15. Scope: the hub's PIN calls and data, the web "Who's watching?" picker
(which is also what the TV app shows, inside its WebView), the web screens where the secret is chosen
or asked for, and the native TV picker used right after pairing. Follows the arrow-pattern PIN
(`2026-11-15-tv-arrow-pin-design.md`).

## What and why

The owner asked for two things:

1. **Restore the highlight on the focused keypad key.** The PIN keypad hid which key had focus (no
   ring, lift or colour) so people watching the screen could not follow a PIN entered with the remote.
   In practice it made the keypad hard to use: the person entering the PIN could not see where they
   were either.
2. **Each person picks exactly one way to sign in:** a number PIN, a password or an arrow pattern.

The arrow pattern from the previous change was only ever asked for by the native TV picker. On the
TV, the picker people actually see is the web one, which always opened the keypad, so a person with
an arrow pattern was still shown digits. That bug is fixed here as part of (2).

## The keypad

The rule in `base.css` that cancelled the focus style on `.pin-pad .pin-key` is gone, so a focused
key shows the same focus box as every other button, in every keypad dialog. The rest of the
remote-friendly behaviour stays: arrows are clamped to the keypad (no wrapping, never out of it), focus
returns to 5 after every key press, error and confirm step, and Enter after typed digits submits. The
"your place on the keypad is hidden" note is removed. People who would rather not have their walk on
the keypad watched can choose an arrow pattern or a password.

## One secret, three kinds

There is still one secret per person, in `profile.pin_hash`, hashed and checked the same way whatever
its kind, against the same budget of five wrong guesses in fifteen minutes. The new nullable
`profile.pin_kind` column says how it is asked for:

| `pin_kind` | Rules | Asked for with |
|------------|-------|----------------|
| `pin`      | 4 to 8 ASCII digits | the keypad |
| `arrows`   | 4 to 8 arrows, sent as keypad digits (Up 2, Left 4, Right 6, Down 8) | dots that fill with arrow presses |
| `password` | 6 to 64 printable characters, kept exactly as typed (never trimmed) | a password field |
| null       | no secret | |

`pin_arrows` stays (the length of an arrow pattern, so a client knows how many presses to wait for)
and is null for the other kinds.

Migration `0016` adds the column and backfills it: a row with a `pin_hash` and a `pin_arrows` becomes
`arrows`, a row with only a `pin_hash` becomes `pin`. A row the hub reads without a kind (none should
remain) is shown as an arrow pattern when it has a length, else a number PIN.

### API

- `PUT /users/{id}/pin`, `PUT /profiles/{id}/pin` and `POST /users` take `kind`. The older
  `arrows: true` still means an arrow pattern, so older clients keep working; `kind` wins when both are
  sent; neither means a number PIN. A secret that breaks its kind's rules answers 400 `invalid_pin`;
  the schema only bounds it (4 to 64 characters, 422 outside that).
- Everywhere the person's own secret is checked rather than chosen, it may now be a password:
  `POST /auth/switch` `pin`, `POST /auth/adult-unlock` `pin`, and every `current_pin`. Those take up to
  64 characters of anything, and the hash check alone decides. A wrong one costs a guess as before.
- `pin_kind` is published wherever `has_pin` is: `User` (and so `/auth/me`, the user list and page,
  and `Switched`), `Profile`, and `SwitchableUser` (`GET /auth/switchable`).
- `PUT /profiles/{id}/pin` no longer signs the person out everywhere (it bumped the token version);
  like `PUT /users/{id}/pin` it only ends the adult unlock. This is the same change as the separate
  "PIN keeps the session" fix, so the two branches agree.

The owner PIN is a separate credential and is unchanged.

## Web

**Choosing.** The person page (Users) and Settings, Profile show one row: how the person signs in
("Signs in with a password", "Signs in with an arrow pattern of 6 arrows", "No PIN"), a "Choose
sign-in" / "Change sign-in" button and "Remove". The button opens "How should Name sign in?" with three
choices: Number PIN, Password, Arrow pattern. Each opens its entry: the keypad entered twice, a password
entered twice, or the arrow-pattern chooser. Choosing one replaces whatever was there. When the hub
asks for the current secret first (403 `current_pin_required`, one's own secret unless signed in a few
minutes ago), it is asked for the way the current one was chosen, and the change is sent again with
it. "Add a managed user" has the same three choices.

**Asking.** "Who's watching?" and the adult-titles unlock ask with the matching entry:

- `pin`: the keypad, as before.
- `arrows`: a dialog of N hollow dots. Each arrow key (what the TV remote's D-pad sends to the web app)
  fills one, and the last one sends the pattern as its digits, with no OK step. The arrow keys are
  captured ahead of the spatial navigation, so they never move focus to the cards behind. Key repeats
  are ignored. Enter (the remote's OK) empties the dots and never sends; Escape or Back closes. A wrong
  pattern empties the dots and says so; too many wrong ones lock the entry for the server's
  Retry-After. Which arrows were pressed is never shown. On-screen arrows serve a mouse or touch screen.
- `password`: a password field that has focus when the dialog opens (on the TV this brings up the
  system keyboard); Enter submits.

Someone without a secret who unlocks adult titles first chooses how they will sign in, then unlocks.
The older household profile sign-in labels the field "Password" for a password kind.

## TV (native picker)

`SwitchableUser` gains `pinKind` (from `pin_kind`) and a derived `kind`. Against an older hub, or a
kind the app does not know, it falls back to the earlier rule: an arrow pattern when `pin_arrows` is
given, else the keypad. A password person gets `PasswordPinDialog`: one masked field typed on the
system keyboard, sent with Done or Continue. Errors, lockout and Back reuse the existing flow, worded
for the kind ("That password isn't right"). Version 1.2.8 (15).

## Testing

- Hub: setting, showing and clearing each kind; each kind's rules; `kind` over `arrows`; adding a
  person with each kind; switching to a password person and its rate limit; a password for adult
  unlock and as `current_pin`; the household sign-in with a password; the `0016` backfill and
  downgrade.
- Web (Playwright, mock server): Who's watching with an arrow-pattern person (arrow keys fill the dots,
  focus stays put, Enter clears, a wrong pattern clears and says so, the last arrow switches), on-screen
  arrows and Escape, a password person, the keypad's visible focus box, choosing a password on the
  person page, and the existing PIN, arrow-pattern and adult-unlock flows through the chooser.
- TV: `SwitchableUser.kind` from new and older hubs, and the picker's password wording.
