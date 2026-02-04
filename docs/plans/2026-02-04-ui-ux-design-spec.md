# ekaiX UI/UX Design Specification

Reference: v0 prototype at `https://v0.app/chat/d77VGgtQcEn`

Build with: **Next.js + Material UI (MUI)** using `createTheme` with the ekai brand palette from `CLAUDE.md`. Do NOT use shadcn, Tailwind, or Radix — the v0 prototype used those but the production app uses MUI exclusively.

---

## 1. Global Layout

```
+------------------+------------------------------------------+
|                  |                                          |
|  Sidebar (240px) |  Main Content Area                       |
|  fixed height    |  scrollable                              |
|                  |                                          |
|  - Logo          |  (varies by active page)                 |
|  - Nav items     |                                          |
|  - User footer   |                                          |
|                  |                                          |
+------------------+------------------------------------------+
```

### Sidebar
- **Width**: 240px, fixed, full viewport height
- **Background**: `#131316` (darker than main bg)
- **Border**: right border `#3A3A3E`
- **Logo**: "ekai" text + gold hexagon icon, top-left, compact
- **Nav items** (vertical stack, icon + label):
  1. Data Products (database icon)
  2. User Management (people icon)
  3. LLM Configuration (settings/gear icon)
- **Active nav item**: gold text `#D4A843`, gold left border bar (3px)
- **Inactive nav item**: `#9E9E9E` text, no border
- **Hover**: subtle background lighten
- **User footer** (bottom of sidebar):
  - Gold avatar circle with initials (e.g., "SC")
  - Email text truncated with ellipsis
  - Overflow menu button (three dots) → dropdown: Profile, Settings, Sign out

### Main Content Area
- **Background**: `#1A1A1E`
- **Padding**: 24px
- Scrollable independently from sidebar

---

## 2. Dashboard (Data Products List)

**Route**: `/` or `/data-products`

### Header Row
- **Title**: "Manage Data Products" — `h1`, white, bold
- **Search bar**: MUI TextField with search icon, placeholder "Search data products...", ~400px wide
- **Create button**: "Create Data Product" — gold filled button, right-aligned

### Data Table
- MUI Table with columns:

| Column | Content | Width |
|--------|---------|-------|
| Name | Product name, bold white text | ~25% |
| Database | Snowflake DB name, monospace-style | ~18% |
| Status | Colored pill badge | ~12% |
| Last Updated | Relative date | ~12% |
| Owner | Full name | ~15% |
| Collaborators | Overlapping avatar circles with initials | ~15% |
| Actions | Three-dot menu button | 40px |

### Status Badges (pill shape, outlined)
- **Published**: green outline + green text
- **Draft**: gold outline + gold text
- **Discovery**: gold outline + gold text
- **In Progress**: gold outline + gold text

### Collaborator Avatars
- Gold-bordered circles, 28px diameter
- Show initials (e.g., "JL", "SC", "AT")
- Overlap by ~8px when multiple
- Max 3 visible + "+N" overflow

### Pagination Footer
- "Rows per page" dropdown (5, 10, 25)
- "1-5 of 5" text
- Previous/Next arrow buttons (disabled when at bounds)

### Row Click Behavior
- Entire row is clickable (cursor: pointer)
- Clicking a row navigates to the **Chat Workspace** for that data product

---

## 3. Create Data Product Modal

Triggered by "Create Data Product" button. MUI Dialog, centered, ~500px wide.

### Step Indicator
- Two dots at top of modal, gold = active step, gray = inactive
- Dot 1 active on Step 1, Dot 2 active on Step 2

### Step 1: Name & Description
- **Title**: "Create Data Product"
- **Subtitle**: "Start building your semantic model"
- **Fields**:
  - Name (required, marked with *): MUI TextField, placeholder "e.g., Customer Analytics Model"
  - Description (optional): MUI TextField multiline/textarea, placeholder "Describe what you want to analyze..."
- **Buttons**:
  - Cancel (outlined, left)
  - Next (gold filled, right) — **disabled** until Name has content

### Step 2: Select Data Source
- **Title**: "Select Data Source"
- **Database dropdown**: MUI Select, placeholder "Choose a database..."
  - Options loaded from Snowflake INFORMATION_SCHEMA (mock: PROD_ANALYTICS_DB, FINANCE_PROD_DB, MARKETING_DW, SALES_WAREHOUSE, OPERATIONS_DB)
- **Schema checkboxes** (appear after database selection):
  - Label: "Select Schemas"
  - MUI Checkbox list, gold checkmarks when checked
  - Pre-selects non-PUBLIC schemas, PUBLIC unchecked by default
  - Example for MARKETING_DW: ANALYTICS (checked), CAMPAIGNS (checked), PUBLIC (unchecked)
- **Buttons**:
  - Back (outlined, left) — returns to Step 1
  - Create (gold filled, right) — **disabled** until database selected and at least one schema checked

### Modal Close
- X button top-right, closes modal without saving

---

## 4. Chat Workspace

**Route**: `/data-products/:id` (navigated to after clicking a row or completing Create)

### Layout
```
+------------------+------------------------------------------+
|  Sidebar         |  Breadcrumb       [Artifacts 5] button   |
|  (same)          +------------------------------------------+
|                  |  Phase Stepper (horizontal)               |
|                  +------------------------------------------+
|                  |                                          |
|                  |  Chat Messages (scrollable)              |
|                  |                                          |
|                  +------------------------------------------+
|                  |  Chat Input Bar                           |
+------------------+------------------------------------------+
```

### Breadcrumb
- "Data Products" (gold link, clickable → back to dashboard) > "Marketing Attribution Model" (white, current)
- Right side: **Artifacts button** (document icon + "Artifacts" label + gold badge count)

### Phase Stepper (horizontal, centered)
- 5 steps connected by lines:
  1. Discovery
  2. Requirements
  3. Generation
  4. Validation
  5. Publishing
- **Completed step**: gold circle with white checkmark, gold text
- **Current step**: gold circle with gold fill, gold text
- **Future step**: gray circle, gray text
- **Connector lines**: gold between completed steps, gray between future steps

### Chat Messages Area
- Scrollable container, full remaining height
- Two message types:

#### Agent Messages (left-aligned)
- **Label**: "ekaiX" in gold text above the bubble
- **Bubble**: dark card background (`#252528`), rounded corners, ~70% max width
- **Content**: white text, supports bold, bullet lists, checkmarks/emojis
- **Artifact cards** (inline, below message text):
  - Gold left border (3px)
  - Icon (type-specific) + Title (bold) + Description (gray)
  - Clickable → opens artifact detail panel
  - Examples:
    - "ERD Diagram — 24 tables, 31 relationships"
    - "Data Quality Report — 78% overall score"
    - "BRD Document — 6 metrics, 5 dimensions, 2 filters"
    - "Semantic Model YAML — 7 tables, 6 measures, 5 dimensions"
    - "Data Preview — Sample results from your model"

#### User Messages (right-aligned)
- Dark card background, rounded corners, right-aligned
- White text, no label

### Chat Input Bar (fixed bottom)
- **Attachment button** (paperclip icon, left)
- **Text input**: MUI TextField, placeholder "Ask ekaiX anything...", full width
- **Send button** (gold arrow/paper-plane icon, right)

---

## 5. Artifacts Panel (Right Slide-over)

Triggered by clicking the "Artifacts N" button in the chat workspace header.

### Panel Structure
- **Width**: ~380px, slides in from right, overlays chat area
- **Background**: card color (`#252528`)
- **Header**: "Artifacts" title + "N generated" subtitle + Close (X) button
- **Content**: Artifacts grouped by phase

### Phase Groups
Each group has a phase heading (uppercase, small, muted text) followed by artifact cards:

- **DISCOVERY**: ERD Diagram, Data Quality Report
- **REQUIREMENTS**: BRD Document
- **GENERATION**: Semantic Model YAML
- **VALIDATION**: Data Preview

### Artifact Cards in Panel
- Gold left border (3px)
- Type icon + Name (bold) + Description (gray) + Timestamp (gray, small)
- Clickable → opens same detail dialog as chat artifact cards
- Hover: subtle background lighten

---

## 6. ERD Diagram Panel (Right Slide-over)

Triggered by clicking ERD Diagram artifact (from chat or artifacts panel).

- **Width**: ~500px slide-over from right
- **Header**: "ERD Diagram" title + Close (X) button
- **Legend**: two color swatches — "Fact Table" (gold border), "Dimension Table" (green border)
- **Fact table cards**: gold/amber left border, table name (bold), row count
  - Full width, stacked vertically
- **Dimension table cards**: green left border, table name (bold), row count
  - 2-column grid layout
- **Footer text**: "N relationships detected"

### Table cards shown in mockup:
**Fact**: CAMPAIGN_EVENTS (2.1M), CONVERSIONS (847K), TOUCHPOINTS (5.9M)
**Dimension**: CUSTOMERS (124K), CHANNELS (42), CAMPAIGNS (1,203), PRODUCTS (8,432)

---

## 7. Data Quality Report Panel (Right Slide-over)

Triggered by clicking Data Quality Report artifact.

- **Width**: ~500px slide-over from right
- **Header**: "Data Quality Report" title + Close (X) button
- **Donut chart**: gold ring, large percentage in center (e.g., "78%"), "Overall" label
- **Summary text**: "4 of 6 tables meet quality threshold (>60%)"
- **Table**: MUI Table with columns:

| Column | Example |
|--------|---------|
| Table Name | CONVERSIONS |
| Rows | 847K |
| Null % | 2.1% |
| Score | 95% (color-coded: green >=80%, gold 60-79%, red <60%) |

### Score color coding:
- 95%, 97%, 100%, 89% → green text
- 68%, 72% → gold text

---

## 8. User Management Page

**Route**: `/user-management`

### Profile Card (MUI Card)
- **Header**: "Profile" / "Your account information"
- **Avatar**: gold circle with initials, ~56px
- **Name**: bold, white
- **Email**: gray
- **Badges**: pill-shaped outlined badges for Snowflake role and account
  - e.g., "ANALYST_ROLE", "ACME_PROD.us-east-1"
- **Info text**: "User identity is managed by your Snowflake account. Contact your administrator to update roles or permissions."

### Preferences Card (MUI Card)
- **Header**: "Preferences" / "Customize your experience"
- **Appearance toggle**: "Appearance" label + "Switch between light and dark mode" description + MUI Switch (right-aligned) + "Dark"/"Light" label
- **Email Notifications**: toggle switch
- **Default Rows Per Page**: MUI Select dropdown (5, 10, 25)
- **Save Preferences**: gold filled button

---

## 9. LLM Configuration Page

**Route**: `/llm-configuration`

### Page Header
- **Title**: "LLM Configuration"
- **Subtitle**: "Configure the AI model provider for semantic model generation and conversational analysis."

### Provider Radio Cards (MUI RadioGroup, vertical stack)
Three selectable cards, gold border on selected:

1. **Snowflake Cortex** (default selected)
   - "RECOMMENDED" gold badge
   - "Native Snowflake AI models - no additional configuration required"
   - Green status: checkmark + "Cortex AI is enabled and ready to use"

2. **Enterprise Cloud**
   - "Use your own cloud AI services (GCP Vertex AI, Azure OpenAI)"

3. **Public APIs**
   - "Connect to public AI services like OpenAI, Anthropic (requires API keys)"

---

## 10. Complete Chat Conversation Flow

This documents the full 5-phase conversation as shown in the mockup. Use this as the template for demo/mock data.

### Phase 1: Discovery
1. **Agent**: "Welcome! I'll help you create a semantic model for **{name}**. I've connected to **{database}** and will now discover your selected schemas: {schemas}. Let me start profiling your tables..."
2. **Agent**: "Discovery complete! Here's what I found: **3 schemas** · **24 tables** · **312 columns**. I've classified your tables into **8 fact tables** and **16 dimension tables**, and detected **31 relationships** between them."
   - Artifact: ERD Diagram (24 tables, 31 relationships)
   - Artifact: Data Quality Report (78% overall score)
3. **Agent**: Lists key tables found (Fact tables with row counts, Dimension tables with row counts). Asks "Would you like to proceed with these tables, or should I include additional ones?"
4. **User**: "Yes, include those. Also add the PRODUCTS table."
5. **Agent**: Confirms addition, shows data quality flags (null percentages, PK validity, FK integrity). Asks "Ready to move on to capturing your business requirements?"

### Phase 2: Requirements
6. **User**: "Yes, let's define the requirements"
7. **Agent**: "I'll walk you through a few questions to build your Business Requirements Document. **What is the primary business question you want to answer?**" (with examples)
8. **User**: Business question text
9. **Agent**: Suggests relevant metrics with checkmarks. Asks to confirm.
10. **User**: Confirms and adds more
11. **Agent**: Asks about dimensions to slice by (with distinct value counts)
12. **User**: Confirms
13. **Agent**: Asks about filters/business rules (with suggestions)
14. **User**: Specifies filters
15. **Agent**: "Here's your complete BRD:"
    - Artifact: BRD Document (N metrics, N dimensions, N filters)
16. **Agent**: "Does this look right? I can modify anything before generating."

### Phase 3: Generation
17. **User**: "Looks good, generate it"
18. **Agent**: "I've generated your semantic model!"
    - Artifact: Semantic Model YAML (N tables, N measures, N dimensions)

### Phase 4: Validation
19. **Agent**: "Let me now validate this against your actual data..."
20. **Agent**: "Validation complete!" with checklist (SQL compiles, join cardinality, column existence, filters working, minor warnings)
    - Artifact: Data Preview (Sample results from your model)
21. **Agent**: "Everything looks healthy. Ready to publish to Snowflake Intelligence?"

### Phase 5: Publishing
22. **User**: "Yes, publish it"
23. **Agent**: "**Published successfully!** Your semantic model and Cortex Agent are now live:" with Semantic View path, Cortex Agent path, and role access info.

---

## 11. Dark / Light Theme

Both themes use the same layout and components. Only colors change.

| Element | Dark Mode | Light Mode |
|---------|-----------|------------|
| Main background | `#1A1A1E` | `#FFFFFF` |
| Sidebar background | `#131316` | `#F5F5F5` |
| Card/surface | `#252528` | `#FAFAFA` |
| Card border | `#3A3A3E` | `#E0E0E0` |
| Text primary | `#F5F5F5` | `#1A1A1E` |
| Text secondary | `#9E9E9E` | `#666666` |
| Primary accent | `#D4A843` | `#D4A843` (same) |
| Active nav text | `#D4A843` | `#D4A843` (same) |
| Input background | `#252528` | `#FFFFFF` |
| Input border | `#3A3A3E` | `#E0E0E0` |
| Input border focus | `#D4A843` | `#D4A843` (same) |

**Default theme**: Dark. Toggle in User Management > Preferences.

---

## 12. Component Inventory (MUI)

| Component | MUI Component | Usage |
|-----------|--------------|-------|
| Sidebar | Drawer (permanent) | App navigation |
| Nav items | ListItemButton | Sidebar navigation |
| Data table | Table, TableHead, TableBody, TableRow, TableCell | Dashboard |
| Status badge | Chip (outlined, size=small) | Table status column |
| Avatar | Avatar | Collaborators, user profile |
| Create modal | Dialog | 2-step create flow |
| Text input | TextField | Name, description, search, chat input |
| Dropdown | Select | Database picker, rows per page |
| Checkbox | Checkbox + FormControlLabel | Schema selection |
| Button (primary) | Button variant="contained" | Create, Next, Send |
| Button (secondary) | Button variant="outlined" | Cancel, Back |
| Toggle | Switch | Dark/light mode, notifications |
| Radio cards | Radio + Card | LLM provider selection |
| Breadcrumb | Breadcrumbs + Link | Chat workspace header |
| Stepper | Custom (not MUI Stepper — horizontal circles with lines) | Phase progress |
| Slide-over panel | Drawer (anchor=right, temporary) | Artifacts, ERD, Data Quality |
| Donut chart | Use a lightweight chart lib (recharts or custom SVG) | Data quality score |
| Chat bubbles | Custom Card-based components | Agent/user messages |
| Artifact cards | Custom Card with left border accent | Inline chat + panel |
