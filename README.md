# NASA Online Directives Information System (NODIS) Sitemap & Scraper Solution

Automated, hardened sitemap generator and weekly crawler for the **NASA Online Directives Information System (NODIS)** library hosted at NASA Goddard Space Flight Center (GSFC):
`https://nodis3.gsfc.nasa.gov/Rpt_current_directives.cfm`

This repository generates and maintains a comprehensive, production-ready XML sitemap and URL index optimized for ingestion by **Onyx** (formerly Danswer) and other AI search / RAG platforms.

---

## The "Redirect Hell" & How This Solves It

Standard web scrapers and crawlers frequently fail or capture empty pages when indexing NODIS due to several legacy ColdFusion CMS quirks:

1. **Client-Side JavaScript Redirects**:
   - Links like `displayAll.cfm?Internal_ID=...&page_name=all` or `displayDir.cfm?...&format=PDF` return HTTP 200 with tiny 140-byte HTML stubs containing client-side JavaScript redirects:
     ```html
     <script Language="javascript">
       location.href='npg_img/N_PR_7120_005F_/N_PR_7120_005F_.pdf';
     </script>
     ```
   - Standard HTTP crawlers (and Onyx's built-in crawler) do not execute JavaScript inside ColdFusion redirect stubs, so they either index empty text or drop the link entirely.
   - **Solution**: The crawler resolves the underlying file structure and indexes the true, direct PDF assets in `npg_img/{Internal_ID}/{Internal_ID}.pdf`.

2. **Restricted vs. Public Directives**:
   - Directives touching sensitive operations (e.g., Security Program `NPR 1600`, Continuity of Operations `NPR 1040`, Access Control `NPR 2841`) return JavaScript redirects to NASA internal Launchpad authentication (`https://nodis-dms.gsfc.nasa.gov/...`).
   - If an unauthenticated external crawler encounters these, it either hangs, errors out, or fails with HTTP 401/403.
   - **Solution**: The crawler automatically identifies restricted directives during the catalog parse and safely isolates them from the public sitemap.

3. **Multi-Chapter Procedural Requirements (NPRs)**:
   - Major directives (e.g., NPR 7120.5F, NPR 8715.1B) have both individual HTML pages for every single chapter/appendix AND a complete unified PDF document.
   - **Solution**: The sitemap indexes both the individual HTML chapters (providing pinpoint section citations) and the complete unified PDF documents (providing full context and figures).

---

## Indexed Content Overview

The sitemap indexes **2,470+** high-value URLs:

- **262 Public NASA Policy Directives (NPDs) & Procedural Requirements (NPRs)**
- **308 Direct Full PDF Documents & Official Attachments**:
  - Unified master PDFs in `npg_img/`
  - Technical and policy attachments in `/OPD_docs/` and `/NPD_attachments/`
- **2,162 Clean HTML / CFM Pages**:
  - Full directive overviews and metadata
  - All individual chapters, prefaces, and appendices
  - Full revision and change histories (`directive_history.cfm`)

---

## Onyx Web Connector Configuration

In your Onyx Admin Console (**Connectors** → **Web**):

| Field | Configuration |
| :--- | :--- |
| **Connector Name** | `NASA-NODIS` |
| **Base URL** | `https://raw.githubusercontent.com/Oht8wooWi8yait9n/nodis/main/nodis_sitemap.xml` |
| **Scrape Method** | `sitemap` |

Click **Create Connector** to begin indexing the full NASA directives catalog into Onyx.

---

## Maintenance & Automation

- **Automated Updates**: A GitHub Actions workflow runs weekly every Sunday at 00:00 UTC (`.github/workflows/update-sitemap.yml`), crawls the master directive catalog, updates `nodis_sitemap.xml`, and commits any changes.
- **Manual Trigger**: Can be dispatched on-demand from GitHub Actions via `workflow_dispatch`.
- **Local Run**:
  ```bash
  python3 generate_nodis_sitemap.py
  ```
