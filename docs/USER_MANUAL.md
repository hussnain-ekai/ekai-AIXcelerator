# ekaiX User Manual (Business Analyst)

## What ekaiX is
ekaiX helps you turn business questions into a usable data product, then query it confidently.  
It combines:
- Structured data (warehouse tables), and
- Unstructured business context (documents like PDFs, specs, legacy requirement files, SQL DDLs).

You use one chat to guide the process from discovery to publish.

## What you should use it for
- Define a new analytical data product from existing enterprise data.
- Capture business logic/KPIs in plain language.
- Build semantic outputs for trusted Q/A.
- Ask hybrid questions that may require both table data and document context.

## End-to-end workflow in the app

### 1. Create a Data Product
- Create a new product with a clear business name/title.
- Select relevant source tables.
- Add a short business objective (what decisions this product should support).

### 2. Add context documents
- Open the **Documents** panel and upload useful files.
- Keep only relevant documents as `active`; keep uncertain ones as `candidate` or `reference`.
- If you remove or replace documents, review context impact warnings before continuing.

### 3. Discovery
- ekaiX profiles tables and relationships.
- Review discovery artifacts (table understanding, quality signals, ERD).
- Confirm whether the discovered structure matches your business meaning.

### 4. Requirements
- Answer focused questions from ekaiX about:
  - KPI definitions
  - Metric grain
  - Dimensions/filters
  - Business rules and exceptions
- This becomes your BRD-quality business definition in the system.

### 5. Generation and validation
- ekaiX generates semantic artifacts and runs validations.
- If something fails, use the recovery actions shown in chat (do not guess).

### 6. Publish and use
- Publish when artifacts are validated.
- Then ask business questions directly in chat (Explorer behavior).

## How to read answers
Each answer should show trust cues:
- **Source**: Structured / Document / Hybrid
- **Exactness**: Validated exact value or insufficient evidence
- **Confidence**
- **Citations/Evidence**

If ekaiX says evidence is insufficient, treat it as a safe abstain, not a failure.  
Add missing context and ask again.

## Best practices for analysts
- Be explicit about business definitions (e.g., “active customer,” “net revenue”).
- Ask for exact values only when you expect deterministic evidence.
- Use document uploads for policies, legacy definitions, and domain language.
- After major context changes, rerun affected steps instead of continuing blindly.

## Quick quality checklist before sign-off
- Are KPI definitions unambiguous?
- Do artifacts reflect the intended business logic?
- Do answers include citations for important claims?
- For exact-number questions, does ekaiX provide deterministic evidence?
