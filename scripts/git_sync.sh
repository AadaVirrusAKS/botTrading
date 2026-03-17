#!/bin/bash
# Git Sync Script - Push/Pull using credentials from .env
# Usage:
#   ./scripts/git_sync.sh push          # Push to remote
#   ./scripts/git_sync.sh pull          # Pull from remote
#   ./scripts/git_sync.sh push "msg"    # Commit all changes with message, then push

set -e
cd "$(dirname "$0")/.."

# Load credentials from .env
if [ ! -f .env ]; then
    echo "❌ .env file not found in project root"
    echo "   Create it with GITHUB_USERNAME, GITHUB_TOKEN, GITHUB_REPO"
    exit 1
fi

source .env

if [ -z "$GITHUB_USERNAME" ] || [ -z "$GITHUB_TOKEN" ] || [ -z "$GITHUB_REPO" ]; then
    echo "❌ Missing required .env variables"
    echo "   Need: GITHUB_USERNAME, GITHUB_TOKEN, GITHUB_REPO"
    exit 1
fi

REMOTE_URL="https://${GITHUB_USERNAME}:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
ACTION="${1:-push}"
COMMIT_MSG="${2:-}"

case "$ACTION" in
    push)
        if [ -n "$COMMIT_MSG" ]; then
            echo "📝 Committing changes: $COMMIT_MSG"
            git add -A
            git commit -m "$COMMIT_MSG" || echo "   (nothing to commit)"
        fi
        echo "🚀 Pushing to ${GITHUB_REPO}..."
        git push "$REMOTE_URL" main
        echo "✅ Push complete"
        ;;
    pull)
        echo "📥 Pulling from ${GITHUB_REPO}..."
        git pull "$REMOTE_URL" main --no-rebase
        echo "✅ Pull complete"
        ;;
    *)
        echo "Usage: $0 {push|pull} [commit-message]"
        echo ""
        echo "Examples:"
        echo "  $0 push                    # Push existing commits"
        echo "  $0 push \"Added new feature\" # Commit all + push"
        echo "  $0 pull                    # Pull latest changes"
        exit 1
        ;;
esac
