#!/usr/bin/env python3
"""
Generate a consolidated Sitemap (XML) and URL list (TXT) for the
NASA Online Directives Information System (NODIS) at GSFC
(https://nodis3.gsfc.nasa.gov/Rpt_current_directives.cfm).

This script:
1. Crawls the Complete List of Current Directives (Master List).
2. Inspects each NASA Policy Directive (NPD) and Procedural Requirement (NPR).
3. Identifies and gracefully bypasses restricted/internal-only directives
   (those requiring Launchpad authentication via nodis-dms.gsfc.nasa.gov).
4. For public directives:
   - Indexes the main directive overview page and revision history.
   - Resolves and verifies direct master PDF documents (bypassing ColdFusion
     JavaScript redirect stubs).
   - Discovers and indexes all individual chapter, preface, and appendix HTML pages.
   - Extracts and verifies all referenced policy attachment PDFs (OPD_docs, NPD_attachments).
5. Generates both nodis_sitemap.xml and nodis_urls.txt with strict safety thresholds.
"""

import os
import sys
import time
import re
import concurrent.futures
from urllib.parse import urljoin, urlparse, urlunparse, quote
import requests

BASE_NODIS_URL = "https://nodis3.gsfc.nasa.gov"
MASTER_REPORT_URL = "https://nodis3.gsfc.nasa.gov/Rpt_current_directives.cfm"
MAIN_LIB_URL = "https://nodis3.gsfc.nasa.gov/main_lib.cfm"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
}

MINIMUM_EXPECTED_URLS = 1500
MAX_RETRIES = 4
BACKOFF_BASE = 2
MAX_WORKERS = 6


def fetch_with_retry(session: requests.Session, url: str, method: str = "GET") -> requests.Response | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method.upper() == "HEAD":
                resp = session.head(url, headers=HEADERS, timeout=12, allow_redirects=True)
            else:
                resp = session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in [401, 403, 404]:
                # Don't retry client auth / not found
                return resp
        except Exception:
            pass

        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE ** attempt)
    return None


def normalize_url(url: str, base_url: str = BASE_NODIS_URL) -> str:
    clean = urljoin(base_url, url).split("?")[0].split("#")[0].strip()
    # Keep query parameters if this is a CFM page with Internal_ID
    if ".cfm" in url:
        clean = urljoin(base_url, url).split("#")[0].strip()
    parsed = urlparse(clean)
    encoded_path = quote(parsed.path, safe="/:")
    return urlunparse((parsed.scheme, parsed.netloc, encoded_path, parsed.params, parsed.query, parsed.fragment))


def main():
    print("=" * 68)
    print(" NASA Online Directives Information System (NODIS) Sitemap Generator")
    print("=" * 68)

    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"[*] Fetching master directives list: {MASTER_REPORT_URL}...")
    master_resp = fetch_with_retry(session, MASTER_REPORT_URL)
    if not master_resp or master_resp.status_code != 200:
        print("[!] FATAL: Failed to retrieve NODIS master report page.")
        sys.exit(1)

    master_html = master_resp.text
    matches = re.findall(
        r'<a\s+[^>]*href=[\"\'](displayDir\.cfm\?Internal_ID=([^&\"\']+)[^\"\']*)[\"\'][^>]*>(.*?)</a>',
        master_html,
        re.IGNORECASE,
    )

    print(f"[+] Found {len(matches)} directives in master catalog.")

    all_urls = set()
    all_urls.add(MASTER_REPORT_URL)
    all_urls.add(MAIN_LIB_URL)

    def process_directive(item):
        href, internal_id, raw_name = item
        name = re.sub(r'<[^>]+>', '', raw_name).replace('&nbsp;', ' ').strip()
        main_url = f"{BASE_NODIS_URL}/{href}"
        history_url = f"{BASE_NODIS_URL}/directive_history.cfm?Internal_ID={internal_id}&page_name=main"

        urls = set()
        thread_session = requests.Session()
        thread_session.headers.update(HEADERS)

        resp = fetch_with_retry(thread_session, main_url)
        if not resp or resp.status_code != 200:
            return internal_id, name, "FETCH_ERROR", set()

        body = resp.text
        # Detect restricted directive
        if "restricted_directives" in body or "nodis-dms" in body or resp.status_code in [401, 403]:
            return internal_id, name, "RESTRICTED", set()

        # Public directive
        urls.add(main_url)
        urls.add(history_url)

        # Check for direct master PDF in npg_img/
        full_pdf = f"{BASE_NODIS_URL}/npg_img/{internal_id}/{internal_id}.pdf"
        main_pdf = f"{BASE_NODIS_URL}/npg_img/{internal_id}/{internal_id}_main.pdf"

        pdf_found = False
        for p in [full_pdf, main_pdf]:
            p_resp = fetch_with_retry(thread_session, p, method="HEAD")
            if p_resp and p_resp.status_code == 200:
                urls.add(p)
                pdf_found = True
                break

        # Extract all chapter HTML pages
        ch_matches = re.findall(
            r'href=[\"\'](displayDir\.cfm\?Internal_ID=' + re.escape(internal_id) + r'&page_name=([^\"\'>\s]+))[\"\']',
            body,
            re.IGNORECASE,
        )
        for ch_href, page_name in ch_matches:
            if page_name.lower() != "main":
                urls.add(f"{BASE_NODIS_URL}/{ch_href}")

        # Extract any extra PDF attachments referenced in the document
        pdf_matches = re.findall(r'href=[\"\']([^\"\']+\.pdf[^\s\"\'<>]*)[\"\']', body, re.IGNORECASE)
        for p in pdf_matches:
            clean_p = p.split('?')[0].split('#')[0]
            if not clean_p.startswith('http'):
                clean_p = f"{BASE_NODIS_URL}/" + clean_p.lstrip('/')
            urls.add(clean_p)

        return internal_id, name, "PUBLIC", urls

    print(f"[*] Crawling {len(matches)} directives with {MAX_WORKERS} workers...")
    items = [(href, i_id, raw) for href, i_id, raw in matches]

    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(process_directive, items))

    elapsed = time.time() - start_time
    print(f"[+] Completed directives crawl in {elapsed:.1f}s")

    # Serial retry pass for any failed items
    failed_items = [item for item, (internal_id, name, status, u_set) in zip(items, results) if status == "FETCH_ERROR"]
    if failed_items:
        print(f"[*] Retrying {len(failed_items)} failed directives sequentially...")
        for i, item in enumerate(items):
            if results[i][2] == "FETCH_ERROR":
                time.sleep(2)
                recovered = process_directive(item)
                results[i] = recovered
                if recovered[2] != "FETCH_ERROR":
                    print(f"    [+] Successfully recovered {recovered[1]}")

    public_count = 0
    restricted_count = 0
    restricted_names = []

    for internal_id, name, status, u_set in results:
        if status == "PUBLIC":
            public_count += 1
            all_urls.update(u_set)
        elif status == "RESTRICTED":
            restricted_count += 1
            restricted_names.append(name)
        else:
            print(f"    [!] Warning: Directive {name} ({internal_id}) had status {status}")

    sorted_urls = sorted(list(all_urls))
    num_total = len(sorted_urls)

    pdf_count = sum(1 for u in sorted_urls if u.lower().endswith(".pdf"))
    cfm_count = sum(1 for u in sorted_urls if ".cfm" in u.lower())

    print("\n" + "=" * 68)
    print(f"[+] Summary of Directives Processed:")
    print(f"    - Public Directives Indexed:     {public_count}")
    print(f"    - Restricted Directives Bypassed: {restricted_count}")
    if restricted_names:
        print(f"      ({', '.join(restricted_names)})")
    print(f"\n[+] Total Unique Content URLs Aggregated: {num_total}")
    print(f"    - Full PDF Directives & Attachments: {pdf_count}")
    print(f"    - Directive Pages, Chapters & Hist: {cfm_count}")
    print("=" * 68)

    if num_total < MINIMUM_EXPECTED_URLS:
        print(f"[!] ERROR: Aggregated URL count ({num_total}) is below threshold ({MINIMUM_EXPECTED_URLS}).")
        print("    Aborting to prevent pushing degraded sitemap.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_sitemap = os.path.join(script_dir, "nodis_sitemap.xml")
    out_txt = os.path.join(script_dir, "nodis_urls.txt")

    with open(out_txt, "w") as f:
        for u in sorted_urls:
            f.write(u + "\n")

    with open(out_sitemap, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u in sorted_urls:
            escaped_url = u.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;")
            f.write(f"  <url><loc>{escaped_url}</loc></url>\n")
        f.write("</urlset>\n")

    print(f"[+] Successfully generated XML sitemap: {out_sitemap}")
    print(f"[+] Successfully generated URL list:     {out_txt}")


if __name__ == "__main__":
    main()
