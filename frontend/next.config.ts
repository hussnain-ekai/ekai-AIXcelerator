/**
 * Next.js configuration.
 *
 * IMPORTANT: Environment variables are loaded from the ROOT .env file (../.env).
 * Do NOT create a separate .env.local file in this directory.
 */
import type { NextConfig } from 'next';
import path from 'path';
import { config as dotenvConfig } from 'dotenv';

// Load environment variables from root .env file BEFORE Next.js processes config
dotenvConfig({ path: path.resolve(__dirname, '..', '.env') });

const nextConfig: NextConfig = {
  output: 'standalone',
  // Expose NEXT_PUBLIC_* variables to the client
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL,
  },
  // @dagrejs/dagre uses require('@dagrejs/graphlib') internally which webpack
  // replaces with webpackEmptyContext. Excluding from SSR bundling lets
  // Node.js resolve it natively; the webpack alias resolves it for the client.
  serverExternalPackages: ['@dagrejs/dagre', '@dagrejs/graphlib'],
  webpack: (config) => {
    // dagre's ESM bundle wraps require() in a dynamic proxy that webpack
    // replaces with webpackEmptyContext. Force both packages to resolve to
    // their CJS bundles where require() is a plain static call that webpack
    // can analyze and bundle correctly.
    config.resolve = config.resolve ?? {};
    config.resolve.alias = {
      ...config.resolve.alias,
      '@dagrejs/dagre': path.resolve(
        __dirname,
        'node_modules/@dagrejs/dagre/dist/dagre.cjs.js',
      ),
      '@dagrejs/graphlib': path.resolve(
        __dirname,
        'node_modules/@dagrejs/graphlib/dist/graphlib.cjs.js',
      ),
    };
    return config;
  },
};

export default nextConfig;
