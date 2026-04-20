import { Router } from "express";
import fs from "fs";
import path from "path";
import { SaveBotConfigBody } from "@workspace/api-zod";

const router = Router();

const CONFIG_FILE = path.resolve("/home/runner/workspace/.bot_config.json");
const LOG_FILE = path.resolve("/home/runner/workspace/.bot_activity.log");
const MAX_LOG_LINES = 200;

interface BotConfigStore {
  telegramToken?: string;
  mongoUri?: string;
}

function readConfig(): BotConfigStore {
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf-8"));
    }
  } catch {
    // ignore
  }
  return {};
}

function writeConfig(config: BotConfigStore) {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2), "utf-8");
}

function appendLog(line: string) {
  const timestamp = new Date().toISOString();
  const entry = `[${timestamp}] ${line}\n`;
  try {
    fs.appendFileSync(LOG_FILE, entry, "utf-8");
    // Trim log file if too large
    const content = fs.readFileSync(LOG_FILE, "utf-8");
    const lines = content.split("\n").filter(Boolean);
    if (lines.length > MAX_LOG_LINES) {
      fs.writeFileSync(LOG_FILE, lines.slice(-MAX_LOG_LINES).join("\n") + "\n", "utf-8");
    }
  } catch {
    // ignore
  }
}

// GET /api/bot/config
router.get("/bot/config", (req, res) => {
  const config = readConfig();
  const githubToken = process.env.GITHUB_PAT;
  const githubRepoUrl = process.env.GITHUB_REPO_URL;

  res.json({
    hasTelegramToken: !!config.telegramToken,
    hasMongoUri: !!config.mongoUri,
    hasGitHubToken: !!githubToken,
    repoUrl: githubRepoUrl || null,
  });
});

// POST /api/bot/config
router.post("/bot/config", (req, res) => {
  const parsed = SaveBotConfigBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }

  const current = readConfig();
  const updated: BotConfigStore = { ...current };

  if (parsed.data.telegramToken !== null && parsed.data.telegramToken !== undefined) {
    if (parsed.data.telegramToken.trim()) {
      updated.telegramToken = parsed.data.telegramToken.trim();
      process.env.TELEGRAM_BOT_TOKEN = updated.telegramToken;
    }
  }

  if (parsed.data.mongoUri !== null && parsed.data.mongoUri !== undefined) {
    if (parsed.data.mongoUri.trim()) {
      updated.mongoUri = parsed.data.mongoUri.trim();
      process.env.MONGO_URI = updated.mongoUri;
    }
  }

  writeConfig(updated);
  appendLog("Bot configuration updated");

  const githubToken = process.env.GITHUB_PAT;
  const githubRepoUrl = process.env.GITHUB_REPO_URL;

  res.json({
    hasTelegramToken: !!updated.telegramToken,
    hasMongoUri: !!updated.mongoUri,
    hasGitHubToken: !!githubToken,
    repoUrl: githubRepoUrl || null,
  });
});

// GET /api/bot/logs
router.get("/bot/logs", (req, res) => {
  try {
    if (!fs.existsSync(LOG_FILE)) {
      res.json({ lines: ["No log entries yet."], timestamp: new Date().toISOString() });
      return;
    }
    const content = fs.readFileSync(LOG_FILE, "utf-8");
    const lines = content.split("\n").filter(Boolean).slice(-100);
    res.json({ lines, timestamp: new Date().toISOString() });
  } catch {
    res.json({ lines: ["Error reading log file."], timestamp: new Date().toISOString() });
  }
});

export default router;
