# Workspace

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Telegram Bot — DESI MLH

A full-featured Telegram bot with modular handler structure.

- **Runtime**: Python 3.11
- **Framework**: Pyrogram (async Telegram client)
- **Database**: MongoDB via Motor (async driver)
- **Entrypoint**: `main.py`
- **Config** (via Replit Secrets):
  - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` — from my.telegram.org
  - `TELEGRAM_BOT_TOKEN` — from @BotFather
  - `MONGO_URI` — MongoDB connection string
  - `ADMIN_ID` — numeric Telegram user ID for admin commands
- **GitHub**: https://github.com/imranhossenmaruf/desi_mlh_bot

### Ban Prevention Fixes Applied

নিম্নলিখিত পরিবর্তনগুলো করা হয়েছে Telegram ToS মেনে চলার জন্য:

1. **`handlers/tagger.py`** — Invisible ZWNJ mention সরানো হয়েছে। এখন visible mention ব্যবহার করা হয়। Batch size 8→5, delay 1.5s→3s, cooldown 5s→300s (5 মিনিট), member limit 5000→200।

2. **`handlers/autotag.py`** — Tagall delay 1.2s→3s বাড়ানো হয়েছে। Member limit 200 এ সীমিত করা হয়েছে।

3. **`handlers/moderation.py`** — Service message (join/leave/pin) auto-delete বন্ধ করা হয়েছে (ToS violation)। Warning message থেকে promotional channel button সরানো হয়েছে।

4. **`helpers.py`** — Broadcast এ group-এও message পাঠানো বন্ধ করা হয়েছে (unsolicited group broadcast = ToS violation)। Broadcast delay 0.05s→0.1s বাড়ানো হয়েছে।

5. **`handlers/monitor.py`** — Monitor group forward এ rate limit যোগ করা হয়েছে (প্রতি গ্রুপ থেকে min 2 সেকেন্ড interval)। Reaction পাঠানো বন্ধ করা হয়েছে।

### Modular Structure

```text
config.py          — DB collections, Pyrogram Client, constants, shared state
helpers.py         — Utility functions, broadcast helpers, moderation helpers, bot_api
main.py            — Imports all handlers, runs app.run(main()) with background loops
handlers/
  __init__.py
  start.py         — /start, /help, /daily, status callback
  admin.py         — /blockuser, /unblockuser, package overrides
  broadcast.py     — /broadcast, /sbc, all bc_* callbacks, /cancel
  forcejoin.py     — force-join system and all fj_* callbacks
  moderation.py    — /mute, /unmute, /ban, /unban, /warn, /clearwarn, /kick, /del
  shadowban.py     — /shadowban, /unshadowban, shadowban enforcer
  filters.py       — /addfilter, /delfilter, /filters, filter enforcer
  nightmode.py     — /nightmode, nightmode_loop (BST-aware group lock/unlock)
  antiflood.py     — /antiflood, antiflood enforcer
  tagger.py        — /tagall, /utag, /stoptag (visible mentions, rate limited)
  autotag.py       — /tagall, /tag (rate limited, member capped at 200)
  monitor.py       — monitor group relay (rate limited)
  protection.py    — anti-forward, link protection, anti-spam
  clone.py         — clone bot management
  ai_reply.py      — AI auto-reply via Gemini
```

---

## Structure

```text
artifacts-monorepo/
├── artifacts/              # Deployable applications
│   └── api-server/         # Express API server
├── lib/                    # Shared libraries
│   ├── api-spec/           # OpenAPI spec + Orval codegen config
│   ├── api-client-react/   # Generated React Query hooks
│   ├── api-zod/            # Generated Zod schemas from OpenAPI
│   └── db/                 # Drizzle ORM schema + DB connection
├── scripts/                # Utility scripts (single workspace package)
├── pnpm-workspace.yaml     # pnpm workspace
├── tsconfig.base.json      # Shared TS options
├── tsconfig.json           # Root TS project references
└── package.json            # Root package with hoisted devDeps
```

## TypeScript & Composite Projects

Every package extends `tsconfig.base.json` which sets `composite: true`. The root `tsconfig.json` lists all packages as project references. This means:

- **Always typecheck from the root** — run `pnpm run typecheck` (which runs `tsc --build --emitDeclarationOnly`).
- **`emitDeclarationOnly`** — we only emit `.d.ts` files during typecheck.
- **Project references** — when package A depends on package B, A's `tsconfig.json` must list B in its `references` array.

## Root Scripts

- `pnpm run build` — runs `typecheck` first, then recursively runs `build` in all packages that define it
- `pnpm run typecheck` — runs `tsc --build --emitDeclarationOnly` using project references

## Packages

### `artifacts/api-server` (`@workspace/api-server`)

Express 5 API server. Routes live in `src/routes/` and use `@workspace/api-zod` for request and response validation and `@workspace/db` for persistence.

### `lib/db` (`@workspace/db`)

Database layer using Drizzle ORM with PostgreSQL.

### `lib/api-spec` (`@workspace/api-spec`)

Owns the OpenAPI 3.1 spec (`openapi.yaml`) and the Orval config (`orval.config.ts`).

### `lib/api-zod` (`@workspace/api-zod`)

Generated Zod schemas from the OpenAPI spec.

### `lib/api-client-react` (`@workspace/api-client-react`)

Generated React Query hooks and fetch client from the OpenAPI spec.

### `scripts` (`@workspace/scripts`)

Utility scripts package.
