#!/usr/bin/env bash
# Dev startup: backend (terminal 1) + frontend (terminal 2). Electron optional.
set -e
cd "$(dirname "$0")/.."
echo "Backend:  npm run dev:core"
echo "Frontend: npm run dev:web   (http://localhost:5173, proxies /api to :8000)"
echo "Electron: cd apps/desktop && npm run electron:dev  (with VITE_DEV_SERVER_URL=http://localhost:5173)"
