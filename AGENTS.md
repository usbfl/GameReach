# GameReach
- Read docs/PROJECT_SPEC.md for full requirements. Preserve unrelated changes.
- Backend: `cd backend && uv sync --frozen && uv run pytest`.
- Frontend: `cd frontend && npm ci && npm run build && npm run test:e2e`.
- Offline demo: `bash scripts/run_demo.sh` (127.0.0.1:8000, one worker).
- Never copy credentials into source, fixtures, reports or client bundles.
- Fixtures are synthetic; live mode never falls back to fixtures/mock.
- Ranking is deterministic. Unknown is not false, zero or pass.
- Persist state at safe nodes; human actions require state_version/action_id.
- No public deployment, purchase, posting, arbitrary URL fetching or shell tools.
