# Phase 7: Requests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A viewer can ask for a title that no source has; approved requests go to a Seerr-compatible request manager, and the requester is told when the title arrives in the library.

**Architecture:** StreamHub owns the request record and its approval state; the request manager is only the downstream executor. A small client adapter talks to the manager's REST API; a `RequestService` holds the state machine; a periodic job closes requests when the title appears in a library source. Who may request, auto-approve or approve is decided in one policy module so Phase 4's permission system can replace its body without touching callers.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, httpx + respx, APScheduler; React + TanStack Query, MSW, Playwright.

## Global Constraints

- Tests first. `ruff check`, `ruff format --check`, `mypy app`, `pytest` (in `hub-server/`, via `uv run --python 3.12 --extra dev …`) and web `npm run lint`, `npm run typecheck`, `npm run build` must pass.
- `contracts/openapi.yaml` is the source of truth: add every new path and schema there, regenerate `web-app/src/api/schema.d.ts` (`npm run gen:api`), keep `tests/test_contract.py` green.
- Secrets (the manager's API key) are stored the way the TMDB key is stored today and never returned by the API.
- No hostnames, addresses, credentials or personal names in code, tests or docs; use `example.test`.
- Web UI follows existing patterns (`components/ui.tsx` Button/Modal, `nav/Focusable.tsx`, `api/hooks.ts`), is fully D-pad/keyboard operable, and adds MSW fixtures.

---

### Task 1: Request manager client
**Files:** create `hub-server/app/adapters/seerr/__init__.py`, `client.py`; test `tests/test_seerr_client.py`.

```python
class SeerrClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str) -> None
    async def test(self) -> tuple[bool, str]                       # GET /api/v1/status
    async def create_request(self, media_type: Literal["movie", "tv"], tmdb_id: int,
                             seasons: list[int] | Literal["all"] | None = None) -> int   # POST /api/v1/request -> id
    async def request_status(self, request_id: int) -> Literal["pending", "approved", "declined", "available", "failed", "unknown"]
```
Header `X-Api-Key`. Map the manager's numeric request/media statuses to the literals. Errors raise `SeerrError(message, status)`.
- [ ] respx tests for success, 401, 409 (already requested → return the existing id when the body carries it), malformed JSON.

### Task 2: Data model and state machine
**Files:** `hub-server/app/infra/models.py`, new Alembic revision `0020_media_requests` (`down_revision = "0006"`; another branch is adding `0007`, the chain will be joined at merge), create `hub-server/app/services/requests.py`, `services/request_policy.py`; tests `tests/test_requests.py`.

Table `media_request`: `id` (hex 16, pk), `profile_id` (fk profile, cascade), `media_type` (`movie|tv`), `tmdb_id` int, `title`, `year`, `poster`, `seasons` JSON, `state`, `manager_request_id` int null, `decided_by_profile_id` null, `note` text null, `created_at`, `updated_at`; unique on (`media_type`, `tmdb_id`) among open states. Table `notice`: `id`, `profile_id`, `kind`, `title`, `body`, `ref_id`, `created_at`, `read_at`.

States: `pending_approval → approved → sent → available`, plus `declined` and `failed`. `approved → sent` happens in the same call when the manager accepts it; a manager error leaves `approved` with the error in `note` and the job retries.

```python
# services/request_policy.py — the only place that decides who may do what
def can_request(p: Principal) -> bool        # True for every signed-in profile
def auto_approve(p: Principal) -> bool       # p.is_admin
def can_approve(p: Principal) -> bool        # p.is_admin
```
```python
class RequestService:
    async def create(self, p: Principal, media_type: str, tmdb_id: int, seasons: list[int] | None) -> MediaRequest
    async def list(self, p: Principal, *, scope: Literal["mine", "all"], state: str | None) -> list[MediaRequest]
    async def decide(self, p: Principal, request_id: str, approve: bool, note: str | None) -> MediaRequest
    async def reconcile(self) -> int       # job: push approved, close available; returns number changed
    async def state_for(self, media_type: str, tmdb_id: int) -> str | None
```
Rules: creating a request for a title already playable from a library source is rejected (`already_available`); a duplicate open request returns the existing one; `reconcile` marks `available` when `AvailabilityService.library_matches` finds an in-app source for the TMDB id (or the manager reports available) and writes a `notice` for the requester; `declined` also writes a notice.
- [ ] Tests for each transition, the duplicate rule, the already-available rule, permission failures, and reconcile with a fake manager.

### Task 3: API
**Files:** create `hub-server/app/api/routers/requests.py`; modify `api/schemas.py`, `main.py`/router registration, `container.py`, `services/settings_service.py`, `contracts/openapi.yaml`; tests `tests/test_api_requests.py`.

- `GET /requests?scope=mine|all&state=` → `{requests: [...]}` (`all` needs `can_approve`).
- `POST /requests` `{media_type, tmdb_id, seasons?}` → 201 request.
- `POST /requests/{id}/approve`, `POST /requests/{id}/decline` `{note?}`.
- `GET /notices` → unread first; `POST /notices/{id}/read`.
- Settings: `request_manager_url`, `request_manager_api_key` (write-only; `request_manager_configured: bool` readable), `POST /settings/request_manager/test`.
- Tiles and title detail gain `request_state: null|pending_approval|approved|sent|available|declined|failed` and `requestable: bool` (true when the title has a TMDB id, no in-app source, the manager is configured, and `can_request`).
- A scheduler job runs `reconcile` every 5 minutes and after each library source sync.
- [ ] API tests incl. contract test; mypy clean.

### Task 4: Web
**Files:** `web-app/src/api/hooks.ts`, `screens/Title.tsx`, search result tiles, new `screens/Requests.tsx`, route + rail entry, `screens/Home.tsx` (notices), `screens/Settings.tsx` (admin: request manager URL/key/test), `mocks/handlers.ts`, `mocks/fixtures.ts`, Playwright spec `tests/requests.spec.ts`.

- Title page of a requestable title shows a primary **Request** button (for series: all seasons, or pick seasons); after requesting it shows the state ("Requested · waiting for approval", "Approved · downloading", "Declined").
- Search tiles for unavailable titles carry a "Not available" badge and open the title page.
- **Requests** screen: "My requests" for everyone; users who can approve get an "All requests" tab with Approve / Decline (optional note) on pending ones.
- Home shows unread notices as a dismissible banner row ("<Title> is now available" → opens the title).
- [ ] Playwright (mock build): request a movie, see state change after mocked approval, see the notice, keyboard-only.

### Verification
- [ ] All checks in Global Constraints pass. A real end-to-end test against a live manager happens after the owner enters its URL and API key in Settings.
