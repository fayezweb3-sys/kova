from kova.sources import bc, odb


def test_download_link_patterns_match_the_published_pages():
    odb_page = '<a href="https://ontario.ca/files/2026-10/moh-ontario-drug-benefit-odb-formulary-edition-43-data-extract-en-2026-10-28.xml">XML</a>'
    assert odb.LINK_PATTERN.findall(odb_page)[0].endswith("2026-10-28.xml")
    bc_page = ('<a href="/assets/gov/health/health-drug-coverage/pharmacare/lca-rdp-data-related-files/lca_current_october_1_2026.xlsx">LCA</a>'
               '<a href="/assets/gov/health/health-drug-coverage/pharmacare/lca-rdp-data-related-files/rdp_current_october_1_2026.xlsx">RDP</a>')
    assert bc.LCA_LINK.findall(bc_page)[0].endswith("lca_current_october_1_2026.xlsx")
    assert bc.RDP_LINK.findall(bc_page)[0].endswith("rdp_current_october_1_2026.xlsx")
