# Telegram Bot Dashboard

## Overview

A full-stack web dashboard for managing a Python Telegram bot connected to a GitHub repository. Provides GitHub sync/push, bot configuration, file browsing, and activity logs — all in a dark professional UI.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM (available, not used yet)
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)
- **Frontend**: React + Vite + Tailwind CSS + shadcn/ui
- **State management**: TanStack Query

## Artifacts

### bot-dashboard (React + Vite, previewPath: `/`)
Frontend dashboard with 4 pages:
- `/` Overview: GitHub connection form (masked PAT input), Sync/Push controls
- `/files`: Python file browser with code viewer
- `/config`: Telegram Bot Token + MongoDB URI configuration
- `/logs`: Live activity log viewer (polls every 5s)

### api-server (Express, previewPath: `/api`)
REST API routes:
- `POST /api/github/connect` — clone/connect GitHub repo with PAT
- `GET /api/github/status` — repo connection status
- `POST /api/github/sync` — pull latest from GitHub
- `POST /api/github/push` — commit + push changes to GitHub
- `GET /api/github/files` — list Python/config files in cloned repo
- `GET /api/github/file?path=...` — read file contents
- `GET /api/bot/config` — check which credentials are configured
- `POST /api/bot/config` — save Telegram token / MongoDB URI
- `GET /api/bot/logs` — read activity log lines

## Local State Files

- `/home/runner/workspace/.bot_github_state.json` — GitHub connection state
- `/home/runner/workspace/.bot_config.json` — Telegram/MongoDB config
- `/home/runner/workspace/.bot_activity.log` — activity log
- `/home/runner/workspace/bot_repo/` — cloned bot repository

## Key Commands

- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/api-server run dev` — run API server locally

## Next Steps (Bot Features to Implement)

Once GitHub is connected and code synced, the following features need to be implemented in the bot's Python files:

1. **strings.py** — multilingual translations for BN/EN/AR with `/lang` command
2. **Control Group toggle** — `/video` ON/OFF per group stored in MongoDB
3. **Admin moderation** — auto-delete links/forwards from non-admins
4. **Smart inline random** — 10 random videos with 7-day seen filter
5. **Performance cache** — local dict cache for language/group settings
