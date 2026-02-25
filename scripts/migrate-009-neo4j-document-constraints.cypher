// Neo4j document graph constraints
// Run against Neo4j after database initialization
// These constraints ensure uniqueness for document graph nodes

CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.document_id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:DocumentChunk) REQUIRE c.chunk_id IS UNIQUE;
CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (f:DocumentFact) REQUIRE f.fact_id IS UNIQUE;
CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE;
CREATE CONSTRAINT dp_id IF NOT EXISTS FOR (dp:DataProduct) REQUIRE dp.data_product_id IS UNIQUE;
