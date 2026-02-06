import * as Minio from 'minio';

import { config } from '../config.js';

const client = new Minio.Client({
  endPoint: config.MINIO_ENDPOINT,
  port: config.MINIO_PORT,
  useSSL: config.MINIO_USE_SSL,
  accessKey: config.MINIO_ACCESS_KEY,
  secretKey: config.MINIO_SECRET_KEY,
});

async function uploadFile(
  bucket: string,
  path: string,
  buffer: Buffer,
  contentType: string,
): Promise<void> {
  await client.putObject(bucket, path, buffer, buffer.length, {
    'Content-Type': contentType,
  });
}

async function getFile(bucket: string, path: string): Promise<Buffer> {
  const stream = await client.getObject(bucket, path);
  const chunks: Buffer[] = [];
  for await (const chunk of stream) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks);
}

async function healthCheck(): Promise<boolean> {
  try {
    await client.bucketExists('artifacts');
    return true;
  } catch {
    return false;
  }
}

export const minioService = { client, uploadFile, getFile, healthCheck };
