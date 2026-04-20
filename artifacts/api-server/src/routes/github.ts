import { Router } from "express";
import { execSync, exec } from "child_process";
import fs from "fs";
import path from "path";
import { promisify } from "util";
import {
  ConnectGitHubBody,
  SyncFromGitHubResponse,
  PushToGitHubBody,
} from "@workspace/api-zod";

const execAsync = promisify(exec);
const router = Router();

const BOT_REPO_DIR = path.resolve("/home/runner/workspace/bot_repo");
const STATE_FILE = path.resolve("/home/runner/workspace/.bot_github_state.json");

interface GitHubState {
  repoUrl: string;
  repoName: string;
  branch: string;
  lastSync: string | null;
  lastPush: string | null;
}

function readState(): GitHubState | null {
  try {
    if (fs.existsSync(STATE_FILE)) {
      return JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
    }
  } catch {
    // ignore
  }
  return null;
}

function writeState(state: GitHubState) {
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), "utf-8");
}

function buildAuthenticatedUrl(repoUrl: string, token: string): string {
  const url = new URL(repoUrl.replace(/\.git$/, "") + ".git");
  url.username = "oauth2";
  url.password = token;
  return url.toString();
}

function getRepoName(repoUrl: string): string {
  const parts = repoUrl.replace(/\.git$/, "").split("/");
  return parts[parts.length - 1] || "repo";
}

// POST /api/github/connect
router.post("/github/connect", async (req, res) => {
  const parsed = ConnectGitHubBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "Invalid request: " + parsed.error.message });
    return;
  }

  const { repoUrl, token, branch = "main" } = parsed.data;
  const repoName = getRepoName(repoUrl);

  try {
    const authedUrl = buildAuthenticatedUrl(repoUrl, token);

    // Store token in env-accessible location (in process memory for current run)
    process.env.GITHUB_PAT = token;
    process.env.GITHUB_REPO_URL = repoUrl;

    // Clone or fetch
    if (!fs.existsSync(BOT_REPO_DIR)) {
      fs.mkdirSync(BOT_REPO_DIR, { recursive: true });
      execSync(`git clone --branch ${branch} --depth 1 "${authedUrl}" "${BOT_REPO_DIR}"`, {
        stdio: "pipe",
        timeout: 60000,
      });
    } else {
      // Check if already a git repo
      const isGitRepo = fs.existsSync(path.join(BOT_REPO_DIR, ".git"));
      if (!isGitRepo) {
        execSync(`git init "${BOT_REPO_DIR}"`, { stdio: "pipe" });
        execSync(`git -C "${BOT_REPO_DIR}" remote add origin "${authedUrl}"`, { stdio: "pipe" });
        execSync(`git -C "${BOT_REPO_DIR}" fetch origin ${branch} --depth 1`, { stdio: "pipe", timeout: 60000 });
        execSync(`git -C "${BOT_REPO_DIR}" checkout ${branch}`, { stdio: "pipe" });
      } else {
        // Update remote URL with new token
        execSync(`git -C "${BOT_REPO_DIR}" remote set-url origin "${authedUrl}"`, { stdio: "pipe" });
        execSync(`git -C "${BOT_REPO_DIR}" fetch origin ${branch} --depth 1`, { stdio: "pipe", timeout: 60000 });
        execSync(`git -C "${BOT_REPO_DIR}" checkout ${branch}`, { stdio: "pipe" });
        execSync(`git -C "${BOT_REPO_DIR}" reset --hard origin/${branch}`, { stdio: "pipe" });
      }
    }

    const now = new Date().toISOString();
    writeState({
      repoUrl,
      repoName,
      branch,
      lastSync: now,
      lastPush: null,
    });

    res.json({
      success: true,
      message: `Connected to ${repoName} on branch ${branch}`,
      repoName,
      branch,
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    req.log.error({ err }, "GitHub connect failed");
    res.status(400).json({ error: "Failed to connect: " + message.substring(0, 300) });
  }
});

// GET /api/github/status
router.get("/github/status", (req, res) => {
  const state = readState();
  if (!state) {
    res.json({ connected: false });
    return;
  }
  res.json({
    connected: true,
    repoUrl: state.repoUrl,
    repoName: state.repoName,
    branch: state.branch,
    lastSync: state.lastSync,
    lastPush: state.lastPush,
    localPath: BOT_REPO_DIR,
  });
});

// POST /api/github/sync
router.post("/github/sync", async (req, res) => {
  const state = readState();
  if (!state) {
    res.status(400).json({ error: "Not connected to a GitHub repository. Please connect first." });
    return;
  }

  const token = process.env.GITHUB_PAT;
  if (!token) {
    res.status(400).json({ error: "GitHub token not in memory. Please reconnect." });
    return;
  }

  try {
    const authedUrl = buildAuthenticatedUrl(state.repoUrl, token);
    execSync(`git -C "${BOT_REPO_DIR}" remote set-url origin "${authedUrl}"`, { stdio: "pipe" });

    const beforeHash = execSync(`git -C "${BOT_REPO_DIR}" rev-parse HEAD`, { encoding: "utf-8" }).trim();
    execSync(`git -C "${BOT_REPO_DIR}" fetch origin ${state.branch}`, { stdio: "pipe", timeout: 60000 });
    execSync(`git -C "${BOT_REPO_DIR}" reset --hard origin/${state.branch}`, { stdio: "pipe" });
    const afterHash = execSync(`git -C "${BOT_REPO_DIR}" rev-parse HEAD`, { encoding: "utf-8" }).trim();

    let filesChanged: string[] = [];
    if (beforeHash !== afterHash) {
      const diffOutput = execSync(
        `git -C "${BOT_REPO_DIR}" diff --name-only ${beforeHash} ${afterHash}`,
        { encoding: "utf-8" }
      ).trim();
      filesChanged = diffOutput ? diffOutput.split("\n").filter(Boolean) : [];
    }

    const now = new Date().toISOString();
    writeState({ ...state, lastSync: now });

    res.json({
      success: true,
      message: filesChanged.length > 0
        ? `Synced ${filesChanged.length} file(s) from GitHub`
        : "Already up to date",
      filesChanged,
      timestamp: now,
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    req.log.error({ err }, "GitHub sync failed");
    res.status(400).json({ error: "Sync failed: " + message.substring(0, 300) });
  }
});

// POST /api/github/push
router.post("/github/push", async (req, res) => {
  const state = readState();
  if (!state) {
    res.status(400).json({ error: "Not connected to a GitHub repository." });
    return;
  }

  const parsedBody = PushToGitHubBody.safeParse(req.body);
  if (!parsedBody.success) {
    res.status(400).json({ error: "commitMessage is required" });
    return;
  }

  const token = process.env.GITHUB_PAT;
  if (!token) {
    res.status(400).json({ error: "GitHub token not in memory. Please reconnect." });
    return;
  }

  try {
    const authedUrl = buildAuthenticatedUrl(state.repoUrl, token);
    // Add remote if it doesn't exist, otherwise update the URL
    try {
      execSync(`git -C "${BOT_REPO_DIR}" remote add origin "${authedUrl}"`, { stdio: "pipe" });
    } catch {
      execSync(`git -C "${BOT_REPO_DIR}" remote set-url origin "${authedUrl}"`, { stdio: "pipe" });
    }

    // Configure git user if not set
    try {
      execSync(`git -C "${BOT_REPO_DIR}" config user.email "bot@replit.com"`, { stdio: "pipe" });
      execSync(`git -C "${BOT_REPO_DIR}" config user.name "Replit Bot Dashboard"`, { stdio: "pipe" });
    } catch {
      // ignore config errors
    }

    execSync(`git -C "${BOT_REPO_DIR}" add -A`, { stdio: "pipe" });

    // Check if there's anything to commit
    const statusOutput = execSync(`git -C "${BOT_REPO_DIR}" status --porcelain`, { encoding: "utf-8" }).trim();
    if (!statusOutput) {
      res.json({
        success: true,
        message: "Nothing to push — working tree is clean",
        filesChanged: [],
        timestamp: new Date().toISOString(),
      });
      return;
    }

    const filesChanged = statusOutput.split("\n").filter(Boolean).map((l) => l.slice(3));
    execSync(
      `git -C "${BOT_REPO_DIR}" commit -m "${parsedBody.data.commitMessage.replace(/"/g, '\\"')}"`,
      { stdio: "pipe" }
    );
    execSync(`git -C "${BOT_REPO_DIR}" push origin ${state.branch}`, { stdio: "pipe", timeout: 60000 });

    const now = new Date().toISOString();
    writeState({ ...state, lastPush: now });

    res.json({
      success: true,
      message: `Pushed ${filesChanged.length} file(s) to GitHub`,
      filesChanged,
      timestamp: now,
    });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    req.log.error({ err }, "GitHub push failed");
    res.status(400).json({ error: "Push failed: " + message.substring(0, 300) });
  }
});

// GET /api/github/files
router.get("/github/files", (req, res) => {
  if (!fs.existsSync(BOT_REPO_DIR)) {
    res.json({ files: [] });
    return;
  }

  function walkDir(dir: string, baseDir: string): Array<{ path: string; name: string; size: number; lastModified: string }> {
    const results: Array<{ path: string; name: string; size: number; lastModified: string }> = [];
    try {
      const entries = fs.readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.name.startsWith(".")) continue;
        const fullPath = path.join(dir, entry.name);
        const relativePath = path.relative(baseDir, fullPath);
        if (entry.isDirectory()) {
          // Only recurse one level deep to keep it lightweight
          if (dir === baseDir) {
            results.push(...walkDir(fullPath, baseDir));
          }
        } else if (
          entry.name.endsWith(".py") ||
          entry.name.endsWith(".txt") ||
          entry.name.endsWith(".json") ||
          entry.name.endsWith(".md") ||
          entry.name.endsWith(".env") ||
          entry.name.endsWith(".sh")
        ) {
          const stat = fs.statSync(fullPath);
          results.push({
            path: relativePath,
            name: entry.name,
            size: stat.size,
            lastModified: stat.mtime.toISOString(),
          });
        }
      }
    } catch {
      // ignore
    }
    return results;
  }

  const files = walkDir(BOT_REPO_DIR, BOT_REPO_DIR);
  res.json({ files });
});

// GET /api/github/file?path=...
router.get("/github/file", (req, res) => {
  const filePath = req.query.path as string;
  if (!filePath) {
    res.status(400).json({ error: "path query parameter is required" });
    return;
  }

  // Security: prevent path traversal
  const fullPath = path.resolve(BOT_REPO_DIR, filePath);
  if (!fullPath.startsWith(BOT_REPO_DIR)) {
    res.status(400).json({ error: "Invalid file path" });
    return;
  }

  if (!fs.existsSync(fullPath)) {
    res.status(404).json({ error: "File not found" });
    return;
  }

  try {
    const content = fs.readFileSync(fullPath, "utf-8");
    res.json({ path: filePath, content });
  } catch (err) {
    req.log.error({ err }, "File read failed");
    res.status(500).json({ error: "Failed to read file" });
  }
});

export default router;
