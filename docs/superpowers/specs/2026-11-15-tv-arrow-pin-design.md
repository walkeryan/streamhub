# TV arrow-pattern PIN on "Who's watching?"

Status: approved 2026-11-15. Scope: the Android TV picker, the hub's PIN calls, and the web screens
where a PIN is chosen.

## What and why

On a shared TV, picking a person who has a PIN opens a number keypad that the D-pad walks one key at
a time. That is slow with a remote, and a child (or a grandparent) who knows "up, up, down, down" has
an easier time with arrows than with digits.

A person can now choose an **arrow pattern** instead of a typed PIN. On the TV, picking them shows
N empty dots. Each D-pad press (Up, Down, Left or Right) fills one dot, and the last one sends the
pattern at once: there is no OK step. A wrong pattern empties the dots and says so; Back cancels; OK
starts the dots again and never sends. People with an ordinary PIN keep the keypad.

## The pattern is the PIN

Each arrow stands for the digit it sits on on a phone keypad, around 5:

| Arrow | Digit |
|-------|-------|
| Up    | 2     |
| Left  | 4     |
| Right | 6     |
| Down  | 8     |

So Up, Up, Down, Down, Left, Right is the PIN `228846`. The hub stores and checks it exactly like any
other PIN: the same scrypt hash, the same length rule (4 to 8), the same switch and adult-unlock
checks, and the same budget of five wrong guesses in fifteen minutes per person. Anywhere a PIN is
typed (the web picker, the adult unlock, "current PIN" boxes) the person can type those digits.

## Data model

One nullable integer on `profile`, next to `pin_hash` (migration `0015`):

- `pin_arrows`: the pattern's length when the PIN was chosen as an arrow pattern; null for a typed PIN
  or no PIN.
- Setting a PIN with `arrows: true` records its length; setting one without it, or removing the PIN,
  clears it. Every PIN that existed before the migration stays a typed one.
- Clients only see `pin_arrows` while there is a PIN for it to describe.

## API

- `PUT /users/{id}/pin`, `PUT /profiles/{id}/pin` and `POST /users` take `arrows: boolean` (default
  false) beside `pin`. With `arrows: true` the PIN must hold only 2, 4, 6 and 8, or the answer is
  400 `invalid_pin`; the length rules (422 outside 4 to 8 digits) are unchanged. Everything else about
  these calls (who may set whose PIN, proving the current one) is as before.
- `pin_arrows` (integer or null) is published wherever `has_pin` is: `User` (and so `/auth/me`, the
  user list and detail, and the user returned by a switch), `Profile`, and `SwitchableUser`, the
  `GET /auth/switchable` list a TV's picker reads.

## TV

- `SwitchableUser.pinArrows` is read from `pin_arrows` (absent on older hubs, which keeps the keypad).
- `ArrowPinDialog` replaces `PinPadDialog` when the picked person has `pinArrows`. It shows that many
  dots and the hint "Enter your arrow pattern to continue as <name>." It holds no buttons: its one
  focusable node takes focus when it opens, and every arrow key press (down and up) is consumed inside
  it, so the D-pad never moves focus anywhere. A held key's repeats are ignored, so one long press is
  one arrow. OK clears the dots; Back reaches the dialog's dismissal.
- The arrows become digits (`ArrowPinState`, unit-tested without Android) and go through the existing
  `submitPin`, so errors, the "Checking…" state, the lockout countdown and `pinAttempt` (which empties
  the dots after each answer) behave as with the keypad. Errors say "pattern" instead of "PIN".
- Which arrows were pressed is never shown, only how many.
- App version 1.2.5 (code 12).

## Web

- Person page (Users) and Settings, Profile: beside "Set PIN", **Use an arrow pattern** (or **Change
  arrow pattern**) opens a dialog with on-screen ↑ ← → ↓ buttons laid out like a D-pad. The arrow keys
  work too and are captured while it is open, so they never move focus; Backspace takes the last arrow
  back, Enter saves, Escape cancels. The arrows entered are shown (this is where the pattern is
  chosen), 4 to 8 of them, with Clear and Save. The dialog says that six arrows are much harder to
  guess than four, and how the pattern types as digits.
- The person page and Settings say when someone's PIN is an arrow pattern, and of how many arrows.
- The web "Who's watching?" picker is unchanged: it keeps the keypad, and the digits work there.
- Adding a person (with an optional PIN) and the adult-unlock setup still take a typed PIN; an arrow
  pattern can be chosen on the person page afterwards.

## Security

An arrow pattern has four choices per press, against ten for a digit:

- 4 arrows: 4^4 = 256 patterns. At five guesses per fifteen minutes, trying them all takes
  256 / 5 x 15 min, about 13 hours.
- 6 arrows: 4^6 = 4,096 patterns, about 8.5 days.
- 8 arrows: 4^8 = 65,536 patterns, about 136 days.

A four-digit PIN has 10,000 combinations (about 21 days). Patterns are therefore weaker for the same
length, which is why the web dialog recommends six or more. The rate limit is the same one every PIN
already has, applied per person across every device.

`pin_arrows` also tells the household how long that person's PIN is, which a typed PIN does not.
The TV needs it to send the pattern on the last press without an OK; the cost is that a guesser need
not try other lengths, which the numbers above already assume. On-screen, the TV shows only filled
dots, never the arrows.
