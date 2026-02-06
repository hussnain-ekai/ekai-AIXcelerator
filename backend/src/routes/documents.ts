import crypto from 'node:crypto';

import type { FastifyInstance, FastifyRequest } from 'fastify';
import multipart from '@fastify/multipart';

import { postgresService } from '../services/postgresService.js';
import { minioService } from '../services/minioService.js';
import { MAX_FILE_SIZE_BYTES } from '../schemas/document.js';

interface UploadedDocumentRow {
  id: string;
  data_product_id: string;
  filename: string;
  minio_path: string;
  file_size_bytes: number | null;
  content_type: string | null;
  extraction_status: string;
  extraction_error: string | null;
  uploaded_by: string;
  created_at: string;
  extracted_at: string | null;
}

export async function documentRoutes(app: FastifyInstance): Promise<void> {
  // Register multipart support for this plugin scope
  await app.register(multipart, {
    limits: {
      fileSize: MAX_FILE_SIZE_BYTES,
      files: 1,
    },
  });

  /**
   * POST /documents/upload
   * Multipart file upload. Stores the file in MinIO (documents/uploads/)
   * and creates a record in uploaded_documents.
   *
   * The form must include:
   *   - file: the uploaded file (max 50MB)
   *   - data_product_id: UUID of the data product
   */
  app.post(
    '/upload',
    async (request: FastifyRequest, reply) => {
      const { snowflakeUser } = request.user;

      const data = await request.file();

      if (!data) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'No file uploaded. Send a multipart form with a "file" field.',
        });
      }

      // Read data_product_id from form fields
      const fields = data.fields;
      const dataProductIdField = fields['data_product_id'];

      let dataProductId: string | undefined;

      if (
        dataProductIdField &&
        'value' in dataProductIdField &&
        typeof dataProductIdField.value === 'string'
      ) {
        dataProductId = dataProductIdField.value;
      }

      if (!dataProductId) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Missing required field: data_product_id',
        });
      }

      // Verify the data product exists (RLS enforces workspace isolation)
      const dpCheck = await postgresService.query(
        'SELECT id FROM data_products WHERE id = $1',
        [dataProductId],
        snowflakeUser,
      );

      if (dpCheck.rowCount === 0) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Data product not found',
        });
      }

      const filename = data.filename;
      const contentType = data.mimetype;

      // Consume the file stream into a buffer
      const chunks: Buffer[] = [];
      for await (const chunk of data.file) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      }
      const fileBuffer = Buffer.concat(chunks);

      // Check if the file was truncated (exceeded limit)
      if (data.file.truncated) {
        return reply.status(413).send({
          error: 'FILE_TOO_LARGE',
          message: `File exceeds maximum size of ${MAX_FILE_SIZE_BYTES / (1024 * 1024)}MB`,
        });
      }

      const fileSize = fileBuffer.length;
      const documentId = crypto.randomUUID();
      const minioPath = `${dataProductId}/uploads/${documentId}/${filename}`;

      // Upload to MinIO
      await minioService.uploadFile(
        'documents',
        minioPath,
        fileBuffer,
        contentType,
      );

      // Create database record
      const insertResult = await postgresService.query(
        `INSERT INTO uploaded_documents
           (id, data_product_id, filename, minio_path, file_size_bytes,
            content_type, uploaded_by)
         VALUES ($1, $2, $3, $4, $5, $6, $7)
         RETURNING id, filename, file_size_bytes, content_type, created_at`,
        [
          documentId,
          dataProductId,
          filename,
          minioPath,
          fileSize,
          contentType,
          snowflakeUser,
        ],
        snowflakeUser,
      );

      const doc = insertResult.rows[0] as
        | {
            id: string;
            filename: string;
            file_size_bytes: number;
            content_type: string;
            created_at: string;
          }
        | undefined;

      if (!doc) {
        return reply.status(500).send({
          error: 'INTERNAL_ERROR',
          message: 'Failed to create document record',
        });
      }

      return reply.status(201).send({
        id: doc.id,
        filename: doc.filename,
        size: doc.file_size_bytes,
        content_type: doc.content_type,
        created_at: doc.created_at,
      });
    },
  );

  /**
   * GET /documents/:dataProductId
   * List all uploaded documents for a data product.
   */
  app.get(
    '/:dataProductId',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const { dataProductId } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, data_product_id, filename, minio_path, file_size_bytes,
           content_type, extraction_status, extraction_error,
           uploaded_by, created_at, extracted_at
         FROM uploaded_documents
         WHERE data_product_id = $1
         ORDER BY created_at DESC`,
        [dataProductId],
        snowflakeUser,
      );

      return reply.send({
        data: result.rows as UploadedDocumentRow[],
      });
    },
  );

  /**
   * GET /documents/:id/content
   * Get the extracted text content of an uploaded document.
   */
  app.get(
    '/:id/content',
    async (
      request: FastifyRequest<{ Params: { id: string } }>,
      reply,
    ) => {
      const { id } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, filename, extracted_content, extraction_status
         FROM uploaded_documents
         WHERE id = $1`,
        [id],
        snowflakeUser,
      );

      const doc = result.rows[0] as
        | {
            id: string;
            filename: string;
            extracted_content: string | null;
            extraction_status: string;
          }
        | undefined;

      if (!doc) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Document not found',
        });
      }

      if (doc.extraction_status !== 'completed' || doc.extracted_content === null) {
        return reply.status(422).send({
          error: 'EXTRACTION_PENDING',
          message: `Document extraction status: ${doc.extraction_status}`,
          extraction_status: doc.extraction_status,
        });
      }

      return reply.send({
        id: doc.id,
        filename: doc.filename,
        content: doc.extracted_content,
        extraction_status: doc.extraction_status,
      });
    },
  );
}
