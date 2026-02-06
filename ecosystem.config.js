/**
 * PM2 Ecosystem Configuration.
 *
 * IMPORTANT: All configuration is loaded from the ROOT .env file.
 * Do NOT hardcode ports, credentials, or paths here.
 */
require('dotenv').config({ path: './.env' });

const FRONTEND_PORT = process.env.FRONTEND_PORT || 3000;
const BACKEND_PORT = process.env.BACKEND_PORT || 8000;
const AI_SERVICE_PORT = process.env.AI_SERVICE_PORT || 8001;

module.exports = {
  apps: [
    {
      name: 'frontend',
      cwd: './frontend',
      script: 'npm',
      args: 'run dev',
      env: {
        PORT: FRONTEND_PORT,
        NODE_ENV: process.env.NODE_ENV || 'development',
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: 'backend',
      cwd: './backend',
      script: 'npm',
      args: 'run dev',
      env: {
        PORT: BACKEND_PORT,
        NODE_ENV: process.env.NODE_ENV || 'development',
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: 'ai-service',
      cwd: './ai-service',
      script: 'bash',
      args: `-c "source venv/bin/activate && uvicorn main:app --reload --host 0.0.0.0 --port ${AI_SERVICE_PORT}"`,
      env: {
        NODE_ENV: process.env.NODE_ENV || 'development',
      },
      watch: false,
      autorestart: true,
      max_restarts: 10,
    },
  ],
};
