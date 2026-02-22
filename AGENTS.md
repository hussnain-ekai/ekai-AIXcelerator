# Repository Guidelines

## Project Structure & Module Organization
This repository is a three-service workspace:

- `frontend/`: Next.js 15 + React UI (`src/app`, `src/components`, `src/hooks`, `src/stores`).
- `backend/`: Fastify + TypeScript API (`src/routes`, `src/services`, `src/schemas`, `src/middleware`).
- `ai-service/`: FastAPI + Python orchestration (`agents`, `routers`, `services`, `tools`, `backends`).
- `docs/`: planning/research docs and architecture notes.
- `scripts/`: local environment/bootstrap SQL and helper scripts.
- `docker-compose.yml`: local data stack (PostgreSQL, Neo4j, Redis, MinIO).

## Build, Test, and Development Commands
Run commands from each service directory (no root-level build pipeline):

- `./scripts/init-dev.sh`: starts Docker dependencies and initializes local data services.
- `cd frontend && npm run dev`: run Next.js app on port 3000.
- `cd backend && npm run dev`: run Fastify API with live reload.
- `cd ai-service && source venv/bin/activate && uvicorn main:app --reload --port 8001`: run AI service locally.
- `cd frontend && npm run build` / `cd backend && npm run build`: production builds.
- `cd frontend && npm run lint`; `cd backend && npm run lint && npm run typecheck`: static checks.
- `pm2 start ecosystem.config.js`: run all services together in dev mode.

## Coding Style & Naming Conventions
- TypeScript is strict in both `frontend` and `backend`; keep types explicit and avoid `any`.
- Use 2-space indentation in TS/TSX; prefer `camelCase` for variables/functions and `PascalCase` for React components.
- Python follows Black/Isort defaults from `ai-service/pyproject.toml` (line length 100) and strict mypy settings.
- Keep secrets in `.env`; do not hardcode credentials, ports, or endpoints.

## Testing Guidelines
- Frontend/Backend use Vitest: `cd frontend && npm test`, `cd backend && npm test`.
- AI service uses Pytest: `cd ai-service && pytest`.
- Place Python tests in `ai-service/tests` as `test_*.py`.
- For TS, use `*.test.ts` / `*.test.tsx` near the feature or under a local `__tests__` folder.
- No enforced coverage threshold currently; add tests for all new behavior and bug fixes.

## Commit & Pull Request Guidelines
- Follow the existing history style: short, imperative, sentence-case subjects (example: `Add BRD viewer panel and stabilize agent pipeline`).
- Keep commits focused by service/feature area.
- PRs should include a clear problem/solution summary.
- Link the related issue or planning doc (`docs/plans/...`) when applicable.
- Include test evidence (commands run) in the PR description.
- Add screenshots or short recordings for UI changes.
