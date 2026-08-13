# ADR 0004: Use persistent generation tasks and a task center

- Status: Accepted
- Date: 2026-08-13

## Context

Generation currently belongs to `ProcessingPage`: leaving or hiding the page cancels the active request, while `PersistentGenerationRepository` only wraps the process-local `WorkStore`. This conflicts with the expected mobile interaction that a user can leave the progress page and inspect the result later.

## Decision

Generation becomes an application-level, persistent task instead of a page-owned request.

The first release guarantees:

- navigation away from the progress screen does not cancel generation;
- one application-level runner executes at most one task at a time;
- queued, active and historical tasks are visible in a task center;
- task input and progress survive process termination;
- an interrupted task is marked recoverable and may be resumed or retried after the next launch;
- cancellation is always an explicit user action.

It does **not** initially guarantee uninterrupted network execution after HarmonyOS terminates the application process. That stronger guarantee requires a separately validated system background-task integration, including notification, permission and duration constraints.

## Task model

`GenerationTask` persists the following data:

| Field | Meaning |
| --- | --- |
| `id` | Stable task identifier |
| `status` | `queued`, `preparing`, `generating`, `saving`, `succeeded`, `failed`, `cancelled`, or `interrupted` |
| `stage` / `progress` / `message` | User-visible progress snapshot |
| `previewSnapshot` | Video, page and subtitle input required to retry deterministically |
| `profileId` | LLM profile reference; API keys remain in HUKS and are never copied into the task row |
| `configSnapshot` | Non-secret model, endpoint, compatibility mode, reasoning and style settings used by this run |
| `outputNoteId` | Idempotent link to the generated note once saved |
| `attempt` | Retry count |
| `errorCode` / `errorMessage` / `retryable` | Structured failure information and available action |
| `createdAt` / `updatedAt` / `startedAt` / `finishedAt` | Ordering and recovery timestamps |

## Lifecycle rules

1. Subtitle confirmation creates a `queued` task and navigates to its detail screen.
2. The application runner claims the oldest queued task and advances it through preparing, generating and saving.
3. Pages observe repository state only; page lifecycle events never cancel a task.
4. Saving a note is idempotent through `outputNoteId`, preventing duplicate notes after recovery.
5. On application startup, stale active tasks become `interrupted`. The runner may resume safe stages or expose a retry action.
6. Retry creates a new attempt on the same task identity and preserves the previous error for diagnostics.
7. Removing task history never removes a completed note. Deleting a note never silently removes task diagnostics.

## Task-center interaction

- Running cards show stage, progress and an explicit cancel action.
- Queued cards allow cancellation and show their queue position.
- Failed cards map structured errors to a primary action: re-login, change LLM profile, retry, or return to the source video.
- Completed cards open the generated note; task history can be cleared independently.
- The generation detail screen and task-center card render the same persisted task state.

## Consequences

- `GenerationRepository` must be replaced or extended with task CRUD, observation and claim/recovery operations backed by RDB.
- `GenerateNoteUseCase` must accept a task snapshot instead of reading mutable page workspace state.
- `ProcessingPage` becomes a task-detail observer and removes cancellation from `aboutToDisappear` / `onHidden`.
- `AppContainer` owns the runner lifecycle.
- True execution while the process is suspended or terminated remains a separate capability and acceptance test.

