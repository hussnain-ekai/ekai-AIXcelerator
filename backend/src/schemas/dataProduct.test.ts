import { describe, expect, it } from 'vitest';
import { createDataProductSchema } from './dataProduct.js';

describe('createDataProductSchema', () => {
  it('accepts structured product with all fields', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Sales Analytics',
      database_reference: 'ANALYTICS_DB',
      schemas: ['PUBLIC'],
      tables: ['ORDERS', 'CUSTOMERS'],
    });
    expect(result.success).toBe(true);
  });

  it('accepts structured product with explicit type', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Sales Analytics',
      product_type: 'structured',
      database_reference: 'ANALYTICS_DB',
      schemas: ['PUBLIC'],
      tables: ['ORDERS'],
    });
    expect(result.success).toBe(true);
  });

  it('rejects structured product without database_reference', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Sales Analytics',
      product_type: 'structured',
    });
    expect(result.success).toBe(false);
  });

  it('accepts document product without database_reference', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Policy Documents',
      product_type: 'document',
    });
    expect(result.success).toBe(true);
  });

  it('accepts hybrid product without database_reference', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Hybrid Analytics',
      product_type: 'hybrid',
    });
    expect(result.success).toBe(true);
  });

  it('accepts hybrid product with database_reference', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Hybrid Analytics',
      product_type: 'hybrid',
      database_reference: 'ANALYTICS_DB',
      schemas: ['PUBLIC'],
      tables: ['ORDERS'],
    });
    expect(result.success).toBe(true);
  });

  it('defaults product_type to structured', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Sales Analytics',
      database_reference: 'ANALYTICS_DB',
      schemas: ['PUBLIC'],
      tables: ['ORDERS'],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.product_type).toBe('structured');
    }
  });

  it('rejects empty name', () => {
    const result = createDataProductSchema.safeParse({
      name: '',
      product_type: 'document',
    });
    expect(result.success).toBe(false);
  });

  it('rejects invalid product_type', () => {
    const result = createDataProductSchema.safeParse({
      name: 'Test',
      product_type: 'invalid',
    });
    expect(result.success).toBe(false);
  });
});
