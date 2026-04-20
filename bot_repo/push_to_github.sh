#!/bin/bash
# GitHub Push Script — Unity Group Manager
# Run this from the Replit Shell tab:  bash push_to_github.sh

set -e

echo "=== GitHub Push Script ==="
echo ""

# Check secrets
if [ -z "$GH_USERNAME" ] || [ -z "$GH_TOKEN" ]; then
  echo "ERROR: GH_USERNAME or GH_TOKEN is not set."
  echo "Please add them in Replit Secrets."
  exit 1
fi

REPO="desi_mlh_bot"
REMOTE_URL="https://$GH_USERNAME:$GH_TOKEN@github.com/$GH_USERNAME/$REPO.git"

echo "1. Configuring git..."
git config --global user.email "replit@example.com"
git config --global user.name "$GH_USERNAME"

echo "2. Setting remote URL..."
git remote set-url origin "$REMOTE_URL" 2>/dev/null || git remote add origin "$REMOTE_URL"

echo "3. Staging all changes..."
git add .

echo "4. Committing..."
git diff --cached --quiet && echo "Nothing to commit — already up to date." || \
  git commit -m "feat: multilingual (BN/EN/AR) system + /lang command

- strings.py: full BN/EN/AR dictionaries for all user messages
- helpers.py: auto-detect lang from language_code on first save
- strings.py: in-memory _user_lang_cache + get_user_lang/set_user_lang
- start.py: force_join, welcome, privacy, referral, status, help, daily
  all use get_string() with detected user language
- start.py: /lang command + setlang callback (EN/BN/AR buttons)
- start.py: daily_already_claimed & daily_success translated
- video.py: ban, no-videos, all-watched messages translated
- groups.py: bot-added welcome message language-aware (group lang)
- parse_mode=HTML added where missing"

echo "5. Pushing to main..."
git push origin main

echo ""
echo "=== Push complete! ==="
echo "Check: https://github.com/$GH_USERNAME/$REPO"
